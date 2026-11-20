"""Main end-to-end execution pipeline orchestration for Market Dispatch."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import logging
from pathlib import Path
import time
from typing import Any

from src.archive import archive_old_daily_files
from src.classifier import TopicClassifier
from src.config import AppConfig
from src.db import BaseDatabase, create_database
from src.dedup import filter_duplicates
from src.fetch_meta import enrich_reuters_meta
from src.fetchers import (
    BaseFetcher,
    BloombergFetcher,
    ReutersGoogleNewsFetcher,
    WSJFetcher,
)
from src.importance import fetch_gn_headlines, score_day_articles
from src.models import ArticleItem, BatchRecord, SourceFetchResult
from src.render import SiteRenderer
from src.time_utils import now_local, recent_day_strings, to_iso
from src.translator import BaseTranslator, create_translator, translate_articles

logger = logging.getLogger(__name__)


def setup_daily_logging(logs_dir: Path, day_str: str) -> Path:
    """Configure console + logs/YYYY-MM-DD.log UTF-8 file logging."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    log_path = logs_dir / f"{day_str}.log"

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Avoid duplicating handlers if called repeatedly in tests
    has_file = any(
        isinstance(h, logging.FileHandler) and Path(h.baseFilename).resolve() == log_path.resolve()
        for h in root_logger.handlers
    )
    if not has_file:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setFormatter(fmt)
        root_logger.addHandler(fh)

    has_stream = any(
        type(h) is logging.StreamHandler for h in root_logger.handlers
    )
    if not has_stream:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        root_logger.addHandler(sh)

    return log_path


def build_default_fetchers(cfg: AppConfig) -> list[BaseFetcher]:
    """Instantiate enabled RSS fetchers from AppConfig."""
    fetchers: list[BaseFetcher] = []
    if cfg.sources.bloomberg_enabled:
        fetchers.append(
            BloombergFetcher(
                base_url=cfg.sources.bloomberg_base_url,
                feeds=cfg.sources.bloomberg_feeds,
                lookback_hours=cfg.sources.bloomberg_lookback_hours,
                timeout_sec=cfg.sources.timeout_sec,
                tz_name=cfg.timezone,
            )
        )
    if cfg.sources.wsj_enabled:
        fetchers.append(
            WSJFetcher(
                base_url=cfg.sources.wsj_base_url,
                feeds=cfg.sources.wsj_feeds,
                lookback_hours=cfg.sources.wsj_lookback_hours,
                timeout_sec=cfg.sources.timeout_sec,
                tz_name=cfg.timezone,
            )
        )
    if cfg.sources.reuters_enabled:
        fetchers.append(
            ReutersGoogleNewsFetcher(
                url=cfg.sources.reuters_url,
                lookback_hours=cfg.sources.reuters_lookback_hours,
                timeout_sec=cfg.sources.timeout_sec,
                tz_name=cfg.timezone,
            )
        )
    return fetchers


def _run_fetchers_parallel(
    fetchers: list[BaseFetcher],
    ref_now: datetime,
) -> list[SourceFetchResult]:
    if not fetchers:
        return []
    results: list[SourceFetchResult] = []
    with ThreadPoolExecutor(max_workers=len(fetchers)) as pool:
        fut_map = {pool.submit(f.fetch, ref_now): f for f in fetchers}
        for fut in as_completed(fut_map):
            fetcher = fut_map[fut]
            try:
                results.append(fut.result())
            except Exception as exc:  # noqa: BLE001
                logger.exception("Unhandled fetcher exception for %s", fetcher.source_name)
                results.append(
                    SourceFetchResult(
                        source=fetcher.source_name,
                        ok=False,
                        fetched=0,
                        errors=[str(exc)],
                    )
                )
    return results


def run_pipeline(
    cfg: AppConfig,
    *,
    db: BaseDatabase | None = None,
    fetchers: list[BaseFetcher] | None = None,
    translator: BaseTranslator | None = None,
    gn_headlines_override: dict[str, list[set[str]]] | None = None,
    enable_meta: bool = True,
    enable_translate: bool = True,
    ref_now: datetime | None = None,
) -> BatchRecord:
    """Execute the full Market Dispatch run pipeline with fault isolation."""
    t0 = time.monotonic()
    run_dt = ref_now or now_local(cfg.timezone)
    today_str = run_dt.date().isoformat()
    ran_at_iso = to_iso(run_dt, cfg.timezone)

    setup_daily_logging(cfg.paths.logs, today_str)
    logger.info("=== Market Dispatch pipeline start (%s) ===", ran_at_iso)

    own_db = db is None
    repo = db or create_database(cfg)
    repo.init_db()

    sources_summary: dict[str, Any] = {}
    items_new = 0
    items_dup = 0
    translate_ok = 0
    translate_fail = 0
    render_ok = 0

    try:
        # 1. Parallel fetch across configured sources
        active_fetchers = fetchers if fetchers is not None else build_default_fetchers(cfg)
        fetch_results = _run_fetchers_parallel(active_fetchers, run_dt)

        all_items: list[ArticleItem] = []
        for res in fetch_results:
            sources_summary[res.source] = res.to_summary_dict()
            all_items.extend(res.items)
            logger.info(
                "Source=%s ok=%s fetched=%d errors=%d",
                res.source,
                res.ok,
                res.fetched,
                len(res.errors),
            )

        # 2. Topic classification (china > us > channel/keywords > world)
        classifier = TopicClassifier(cfg.keywords)
        for item in all_items:
            initial = item.category if item.feed_topic else None
            item.category = classifier.classify(
                title_en=item.title_en,
                summary_en=item.summary_en,
                initial_category=initial,
            )

        # 3. L1 + L2 Deduplication
        new_items, items_dup = filter_duplicates(all_items, repo)

        # 4. Insert new items into DB
        inserted_items: list[ArticleItem] = []
        for item in new_items:
            row_id = repo.insert_article(item)
            if row_id is not None:
                inserted_items.append(item)
            else:
                items_dup += 1
        items_new = len(inserted_items)
        logger.info("Dedup & insert complete: new=%d dup=%d", items_new, items_dup)

        # 5. Polite og:description fallback for newly inserted Reuters items
        if enable_meta and inserted_items:
            enriched = enrich_reuters_meta(
                inserted_items,
                limit=cfg.sources.reuters_meta_fetch_limit,
                rate_limit_sec=cfg.sources.reuters_meta_rate_limit_sec,
                timeout_sec=cfg.sources.reuters_meta_timeout_sec,
            )
            for item in inserted_items:
                if item.source == "reuters" and item.summary_en and item.id is not None:
                    repo.update_summary_en(item.id, item.summary_en)
            logger.info("Reuters meta fallback enriched=%d", enriched)

        # 6. Translation (Google gtx default / LLM switch)
        if enable_translate and inserted_items:
            tx = translator or create_translator(cfg.translator)
            translate_ok, translate_fail = translate_articles(inserted_items, tx, db=repo)
            logger.info("Translation complete: ok=%d fail=%d", translate_ok, translate_fail)

        # 7. Importance 3-signal recomputation on affected dates within recent N days
        recent_days = recent_day_strings(
            days=cfg.retention_days,
            ref_date=run_dt.date(),
            tz_name=cfg.timezone,
        )
        affected_days = {today_str} | {
            item.effective_day for item in inserted_items if item.effective_day in recent_days
        }
        gn_headlines = (
            gn_headlines_override
            if gn_headlines_override is not None
            else fetch_gn_headlines(
                cfg.importance.gn_top_feeds,
                timeout_sec=cfg.importance.gn_timeout_sec,
            )
        )
        for day_str in sorted(affected_days):
            day_articles = repo.get_articles_by_day(day_str)
            if not day_articles:
                continue
            scored = score_day_articles(day_articles, cfg.importance, gn_headlines=gn_headlines)
            for art, is_imp, reason in scored:
                if art.id is not None:
                    repo.update_importance(art.id, is_imp, reason)

        # 8. Record batch before rendering so today's batch drawer includes current run
        duration_sec = max(0, int(round(time.monotonic() - t0)))
        batch = BatchRecord(
            ran_at=ran_at_iso,
            duration_sec=duration_sec,
            sources_json=sources_summary,
            items_new=items_new,
            items_dup=items_dup,
            translate_ok=translate_ok,
            translate_fail=translate_fail,
            render_ok=0,
        )
        repo.insert_batch(batch)

        # 9. Archive > retention_days & Render recent N days + root index + archive index
        try:
            moved_days = archive_old_daily_files(
                daily_dir=cfg.paths.daily,
                archive_dir=cfg.paths.archive,
                retention_days=cfg.retention_days,
                ref_date=run_dt.date(),
                tz_name=cfg.timezone,
            )
            renderer = SiteRenderer(cfg, repo)
            renderer.render_all(ref_date=run_dt.date(), archived_days_to_refresh=moved_days)
            render_ok = 1
            batch.render_ok = 1
            # Update render_ok flag in DB if SQLite/MySQL supports it
            if batch.id is not None and hasattr(repo, "conn"):
                try:
                    if cfg.storage.engine == "sqlite":
                        with repo.conn:
                            repo.conn.execute(
                                "UPDATE fetch_batches SET render_ok = 1 WHERE id = ?",
                                (batch.id,),
                            )
                    else:
                        with repo.conn.cursor() as cur:
                            cur.execute(
                                "UPDATE fetch_batches SET render_ok = 1 WHERE id = %s",
                                (batch.id,),
                            )
                        repo.conn.commit()
                except Exception:  # noqa: BLE001
                    logger.warning("Could not update batch render_ok flag", exc_info=True)
        except Exception:  # noqa: BLE001
            logger.exception("Archive/render failed; previous HTML files preserved")
            render_ok = 0

        batch.duration_sec = max(0, int(round(time.monotonic() - t0)))
        logger.info(
            "=== Pipeline finished in %ds | new=%d dup=%d tx_ok=%d tx_fail=%d render_ok=%d ===",
            batch.duration_sec,
            items_new,
            items_dup,
            translate_ok,
            translate_fail,
            render_ok,
        )
        return batch
    finally:
        if own_db:
            repo.close()
