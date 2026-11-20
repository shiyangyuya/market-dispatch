"""Atomic Jinja2 HTML renderer for Market Dispatch daily reports and archive index."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
import logging
import os
from pathlib import Path
import tempfile
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from src.archive import list_dated_html_files
from src.config import AppConfig
from src.db import BaseDatabase
from src.models import (
    ArticleItem,
    BatchRecord,
    CATEGORIES,
    CATEGORY_MAP,
    DayStats,
    SOURCES,
)
from src.time_utils import (
    format_cn_date,
    format_dual_time,
    now_local,
    parse_iso,
    recent_day_strings,
    weekday_cn,
)

logger = logging.getLogger(__name__)

_RECENT_LABELS = ("今日", "昨日", "前日")


def atomic_write_text(target: Path, content: str) -> None:
    """Write UTF-8 text atomically via same-directory temp file + os.replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.replace(str(tmp_path), str(target))
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _build_jinja_env(templates_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(templates_dir)),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )


def _format_item_vm(item: ArticleItem, page_date: str, tz_name: str) -> dict[str, Any]:
    src_info = SOURCES.get(item.source, {"label": item.source.upper(), "cls": "bbg"})
    cat_info = CATEGORY_MAP.get(item.category, {"name": item.category, "icon": ""})
    pub_disp, crt_disp, is_cross_day = format_dual_time(
        item.published_at, item.created_at, page_date, tz_name
    )
    return {
        "id": item.id or 0,
        "source": item.source,
        "source_label": src_info["label"],
        "source_cls": src_info["cls"],
        "category": item.category,
        "category_name": cat_info["name"],
        "is_important": bool(item.is_important),
        "important_reason": item.important_reason or "",
        "translated": item.translated,
        "is_untranslated": item.translated != 1 or not item.title_zh,
        "title_zh": item.title_zh,
        "title_en": item.title_en,
        "summary_zh": item.summary_zh,
        "summary_en": item.summary_en,
        "url_display": item.url_display,
        "pub_display": pub_disp,
        "crt_display": crt_disp,
        "is_cross_day": is_cross_day,
    }


def _format_batches_vm(batches: list[BatchRecord], tz_name: str) -> tuple[list[dict[str, Any]], int]:
    """Build batch drawer chips and total single-source failure count."""
    chips: list[dict[str, Any]] = []
    fail_runs = 0
    for b in batches:
        dt = parse_iso(b.ran_at, tz_name)
        hm = dt.strftime("%H:%M") if dt else b.ran_at[11:16]
        failed_sources: list[str] = []
        for src_key, detail in (b.sources_json or {}).items():
            if isinstance(detail, dict) and not detail.get("ok", True):
                label = SOURCES.get(src_key, {}).get("label", src_key)
                failed_sources.append(label)
        if failed_sources:
            fail_runs += 1
            chips.append({
                "hm": hm,
                "ok": False,
                "text": f"{hm} {'/'.join(failed_sources)}✗",
            })
        else:
            chips.append({
                "hm": hm,
                "ok": True,
                "text": f"{hm} ✓ +{b.items_new}",
            })
    return chips, fail_runs


def _build_recent_nav(
    recent_days: list[str],
    day_stats_map: dict[str, DayStats],
    active_date: str | None,
    root_prefix: str,
) -> list[dict[str, Any]]:
    nav: list[dict[str, Any]] = []
    for idx, d_str in enumerate(recent_days):
        is_today = idx == 0
        prefix = _RECENT_LABELS[idx] if idx < len(_RECENT_LABELS) else d_str[5:]
        short_label = f"{prefix} {d_str[5:]}" if idx < len(_RECENT_LABELS) else d_str[5:]
        href = f"{root_prefix}index.html" if is_today else f"{root_prefix}daily/{d_str}.html"
        cnt = day_stats_map[d_str].total if d_str in day_stats_map else 0
        nav.append({
            "date": d_str,
            "short_label": short_label,
            "count": cnt,
            "href": href,
            "is_today": is_today,
            "is_active": active_date == d_str,
        })
    return nav


class SiteRenderer:
    """Renders daily reports, root index.html, archived pages, and archive/index.html."""

    def __init__(self, cfg: AppConfig, db: BaseDatabase) -> None:
        self.cfg = cfg
        self.db = db
        self.env = _build_jinja_env(cfg.paths.templates)

    def render_day_html(
        self,
        day_str: str,
        recent_days: list[str],
        day_stats_map: dict[str, DayStats],
        root_prefix: str,
        is_archived: bool = False,
        updated_at_hm: str | None = None,
    ) -> str:
        """Render a single day's complete self-contained HTML string."""
        articles = self.db.get_articles_by_day(day_str)
        batches = self.db.get_batches_by_day(day_str)
        batch_chips, batch_fail_count = _format_batches_vm(batches, self.cfg.timezone)

        by_cat: dict[str, list[dict[str, Any]]] = {c["key"]: [] for c in CATEGORIES}
        important_items: list[dict[str, Any]] = []

        for art in articles:
            vm = _format_item_vm(art, day_str, self.cfg.timezone)
            cat_key = art.category if art.category in by_cat else "world"
            by_cat[cat_key].append(vm)
            if vm["is_important"]:
                important_items.append(vm)

        sections = [
            {
                "key": c["key"],
                "icon": c["icon"],
                "name": c["name"],
                "en_name": c["en_name"],
                "count": len(by_cat[c["key"]]),
                "articles": by_cat[c["key"]],
            }
            for c in CATEGORIES
        ]

        is_today = bool(recent_days) and day_str == recent_days[0]
        if updated_at_hm is None:
            if batches:
                last_dt = parse_iso(batches[-1].ran_at, self.cfg.timezone)
                updated_at_hm = last_dt.strftime("%H:%M") if last_dt else now_local(self.cfg.timezone).strftime("%H:%M")
            else:
                updated_at_hm = now_local(self.cfg.timezone).strftime("%H:%M")

        recent_nav = _build_recent_nav(
            recent_days=recent_days,
            day_stats_map=day_stats_map,
            active_date=None if is_archived else day_str,
            root_prefix=root_prefix,
        )

        # Prev/next day links in footer
        prev_link = None
        next_link = None
        if not is_archived and day_str in recent_days:
            pos = recent_days.index(day_str)
            if pos + 1 < len(recent_days):
                older = recent_days[pos + 1]
                prev_link = {"label": f"← {older[5:]}", "href": f"{root_prefix}daily/{older}.html"}
            else:
                prev_link = {"label": "← 历史归档", "href": f"{root_prefix}archive/index.html"}
            if pos > 0:
                newer = recent_days[pos - 1]
                newer_href = f"{root_prefix}index.html" if pos - 1 == 0 else f"{root_prefix}daily/{newer}.html"
                next_link = {"label": f"{newer[5:]} →", "href": newer_href}

        template = self.env.get_template("daily.html.j2")
        return template.render(
            page_date=day_str,
            cn_date=format_cn_date(day_str),
            weekday_cn=weekday_cn(day_str),
            is_today=is_today,
            is_archived=is_archived,
            root_prefix=root_prefix,
            timezone=self.cfg.timezone,
            retention_days=self.cfg.retention_days,
            updated_at_hm=updated_at_hm,
            total_count=len(articles),
            important_count=len(important_items),
            important_items=important_items,
            sections=sections,
            recent_days=recent_nav,
            batches=batch_chips,
            batch_count=len(batch_chips),
            batch_fail_count=batch_fail_count,
            prev_link=prev_link,
            next_link=next_link,
        )

    def render_archive_index(
        self,
        recent_days: list[str],
        day_stats_map: dict[str, DayStats],
    ) -> Path:
        """Render archive/index.html grouped by YYYY-MM descending."""
        archived_days = list_dated_html_files(self.cfg.paths.archive)
        months_grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for d_str in archived_days:
            ym = d_str[:7]
            st = day_stats_map.get(d_str, DayStats(date=d_str))
            months_grouped[ym].append({
                "date": d_str,
                "weekday_cn": weekday_cn(d_str),
                "href": f"{d_str}.html",
                "markets": st.by_category.get("markets", 0),
                "tech": st.by_category.get("tech", 0),
                "world": st.by_category.get("world", 0),
                "us": st.by_category.get("us", 0),
                "china": st.by_category.get("china", 0),
                "important": st.important,
                "total": st.total,
            })

        months_vm = []
        for ym in sorted(months_grouped.keys(), reverse=True):
            y, m = ym.split("-")
            months_vm.append({
                "key": ym,
                "label": f"{y} 年 {int(m)} 月",
                "count": len(months_grouped[ym]),
                "days": months_grouped[ym],
            })

        recent_nav = _build_recent_nav(
            recent_days=recent_days,
            day_stats_map=day_stats_map,
            active_date=None,
            root_prefix="../",
        )

        template = self.env.get_template("archive.html.j2")
        html = template.render(
            recent_days=recent_nav,
            months=months_vm,
            total_archived_days=len(archived_days),
            retention_days=self.cfg.retention_days,
            timezone=self.cfg.timezone,
        )

        out_path = self.cfg.paths.archive / "index.html"
        atomic_write_text(out_path, html)
        return out_path

    def render_all(
        self,
        ref_date: date | None = None,
        archived_days_to_refresh: list[str] | None = None,
    ) -> dict[str, Any]:
        """Render recent N days (daily/*.html), root index.html, moved archive pages, and archive/index.html."""
        recent_days = recent_day_strings(
            days=self.cfg.retention_days,
            ref_date=ref_date,
            tz_name=self.cfg.timezone,
        )
        day_stats_map = self.db.get_all_day_stats()
        updated_hm = now_local(self.cfg.timezone).strftime("%H:%M")

        rendered_files: list[str] = []

        # 1. Re-render any newly archived days with archived header & clean relative paths
        for arch_day in archived_days_to_refresh or []:
            html = self.render_day_html(
                day_str=arch_day,
                recent_days=recent_days,
                day_stats_map=day_stats_map,
                root_prefix="../",
                is_archived=True,
                updated_at_hm=updated_hm,
            )
            arch_file = self.cfg.paths.archive / f"{arch_day}.html"
            atomic_write_text(arch_file, html)
            rendered_files.append(str(arch_file))

        # 2. Render recent N days into daily/YYYY-MM-DD.html
        for d_str in recent_days:
            html = self.render_day_html(
                day_str=d_str,
                recent_days=recent_days,
                day_stats_map=day_stats_map,
                root_prefix="../",
                is_archived=False,
                updated_at_hm=updated_hm,
            )
            daily_file = self.cfg.paths.daily / f"{d_str}.html"
            atomic_write_text(daily_file, html)
            rendered_files.append(str(daily_file))

        # 3. Render root index.html directly as today's daily report (root_prefix="")
        today_str = recent_days[0]
        index_html = self.render_day_html(
            day_str=today_str,
            recent_days=recent_days,
            day_stats_map=day_stats_map,
            root_prefix="",
            is_archived=False,
            updated_at_hm=updated_hm,
        )
        index_file = self.cfg.paths.root / "index.html"
        atomic_write_text(index_file, index_html)
        rendered_files.append(str(index_file))

        # 4. Render archive/index.html
        archive_index = self.render_archive_index(recent_days, day_stats_map)
        rendered_files.append(str(archive_index))

        return {
            "today": today_str,
            "recent_days": recent_days,
            "rendered_files": rendered_files,
        }
