"""End-to-end deterministic pipeline and CLI tests (TC-E2E-01, TC-E2E-02)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.config import AppConfig
from src.db import SQLiteDatabase
from src.fetchers.base import BaseFetcher
from src.main import main as cli_main
from src.models import ArticleItem, SourceFetchResult
from src.pipeline import run_pipeline
from src.translator import BaseTranslator

REF_NOW = datetime(2026, 11, 20, 16, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


class StubFetcher(BaseFetcher):
    def __init__(self, source_name: str, items: list[ArticleItem] | None = None, fail: bool = False) -> None:
        super().__init__()
        self.source_name = source_name
        self._items = items or []
        self._fail = fail

    def fetch(self, ref_now: datetime | None = None) -> SourceFetchResult:
        if self._fail:
            return SourceFetchResult(source=self.source_name, ok=False, fetched=0, errors=["network down"])
        # Return fresh copies so repeated runs test deduplication cleanly
        cloned = [ArticleItem(**vars(it)) for it in self._items]
        return SourceFetchResult(source=self.source_name, ok=True, fetched=len(cloned), items=cloned)


class StubTranslator(BaseTranslator):
    def translate_item(self, title_en: str, summary_en: str | None) -> tuple[str | None, str | None, bool]:
        return f"[译]{title_en}", (f"[译]{summary_en}" if summary_en else None), True


def _build_stub_fetchers(fail_reuters: bool = False) -> list[BaseFetcher]:
    bbg_items = [
        ArticleItem(
            source="bloomberg",
            category="markets",
            feed_topic="markets",
            title_en="Nvidia Shares Surge on Blowout Data Center Demand",
            summary_en="Chipmaker guidance topped consensus.",
            url_display="https://www.bloomberg.com/news/articles/2026-11-20/nvidia-surge?utm_source=rss",
            published_at="2026-11-20T15:10:00+08:00",
            created_at="2026-11-20T16:00:00+08:00",
        ),
        ArticleItem(
            source="bloomberg",
            category="world",
            feed_topic="politics",
            title_en="Trump and Beijing Officials Discuss Trade Tariff Path",
            summary_en="Talks covered agricultural quotas.",
            url_display="https://www.bloomberg.com/news/articles/2026-11-20/us-china-tariff",
            published_at="2026-11-20T14:00:00+08:00",
            created_at="2026-11-20T16:00:00+08:00",
        ),
    ]
    wsj_items = [
        ArticleItem(
            source="wsj",
            category="tech",
            feed_topic="RSSWSJD",
            title_en="Nvidia Shares Surge on Data Center Chip Demand",
            summary_en="AI server orders accelerated.",
            url_display="https://www.wsj.com/articles/nvidia-surge?mod=rss_wsjd",
            published_at="2026-11-20T15:15:00+08:00",
            created_at="2026-11-20T16:00:00+08:00",
        ),
    ]
    rtr_items = [
        ArticleItem(
            source="reuters",
            category="world",
            feed_topic=None,
            title_en="Exclusive: Senate Reaches Stopgap Budget Framework",
            summary_en=None,
            url_display="https://news.google.com/rss/articles/reuters-senate",
            published_at="2026-11-20T13:30:00+08:00",
            created_at="2026-11-20T16:00:00+08:00",
        ),
    ]
    return [
        StubFetcher("bloomberg", bbg_items),
        StubFetcher("wsj", wsj_items),
        StubFetcher("reuters", rtr_items, fail=fail_reuters),
    ]


def test_tc_e2e_01_full_pipeline_and_second_run_zero_duplicates(
    tmp_config: AppConfig, sqlite_db: SQLiteDatabase
) -> None:
    b1 = run_pipeline(
        tmp_config,
        db=sqlite_db,
        fetchers=_build_stub_fetchers(fail_reuters=False),
        translator=StubTranslator(),
        gn_headlines_override={},
        enable_meta=False,
        ref_now=REF_NOW,
    )
    assert b1.items_new == 4
    assert b1.items_dup == 0
    assert b1.translate_ok == 4
    assert b1.render_ok == 1

    # Verify classification, importance, and HTML output
    articles = sqlite_db.get_articles_by_day("2026-11-20")
    assert len(articles) == 4
    by_title = {a.title_en: a for a in articles}
    assert by_title["Trump and Beijing Officials Discuss Trade Tariff Path"].category == "china"
    assert by_title["Exclusive: Senate Reaches Stopgap Budget Framework"].category == "us"
    assert by_title["Exclusive: Senate Reaches Stopgap Budget Framework"].is_important == 1
    assert by_title["Nvidia Shares Surge on Blowout Data Center Demand"].is_important == 1  # cross-source

    assert (tmp_config.paths.root / "index.html").exists()
    assert (tmp_config.paths.daily / "2026-11-20.html").exists()
    assert (tmp_config.paths.daily / "2026-11-19.html").exists()
    assert (tmp_config.paths.daily / "2026-11-18.html").exists()
    assert (tmp_config.paths.archive / "index.html").exists()
    assert (tmp_config.paths.logs / "2026-11-20.log").exists()

    # Second run immediately: MUST produce 0 new items and 4 duplicates
    b2 = run_pipeline(
        tmp_config,
        db=sqlite_db,
        fetchers=_build_stub_fetchers(fail_reuters=False),
        translator=StubTranslator(),
        gn_headlines_override={},
        enable_meta=False,
        ref_now=REF_NOW,
    )
    assert b2.items_new == 0
    assert b2.items_dup == 4
    assert b2.render_ok == 1
    assert sqlite_db.get_total_article_count() == 4


def test_tc_e2e_02_single_source_failure_still_renders(
    tmp_config: AppConfig, sqlite_db: SQLiteDatabase
) -> None:
    batch = run_pipeline(
        tmp_config,
        db=sqlite_db,
        fetchers=_build_stub_fetchers(fail_reuters=True),
        translator=StubTranslator(),
        gn_headlines_override={},
        enable_meta=False,
        ref_now=REF_NOW,
    )
    assert batch.items_new == 3
    assert batch.sources_json["reuters"]["ok"] is False
    assert batch.sources_json["bloomberg"]["ok"] is True
    assert batch.render_ok == 1
    index_html = (tmp_config.paths.root / "index.html").read_text(encoding="utf-8")
    assert "路透✗" in index_html


def test_cli_init_render_status_vacuum(tmp_workspace) -> None:
    cfg_path = str(tmp_workspace / "config" / "config.example.yaml")
    assert cli_main(["-c", cfg_path, "init-db"]) == 0
    assert cli_main(["-c", cfg_path, "render"]) == 0
    assert cli_main(["-c", cfg_path, "status"]) == 0
    assert cli_main(["-c", cfg_path, "vacuum"]) == 0
