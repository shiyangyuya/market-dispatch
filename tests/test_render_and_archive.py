"""Tests for Jinja2 HTML rendering contract, atomic writes, and 3-day archiving (TC-REND-*, TC-ARCH-*)."""

from __future__ import annotations

from datetime import date
import re

from bs4 import BeautifulSoup
import pytest

from src.archive import archive_old_daily_files, list_dated_html_files
from src.config import AppConfig
from src.db import SQLiteDatabase
from src.models import ArticleItem, BatchRecord
from src.render import SiteRenderer, atomic_write_text

REF_DATE = date(2026, 11, 20)


def _seed_articles(db: SQLiteDatabase) -> None:
    items = [
        ArticleItem(
            guid="bbg-1",
            source="bloomberg",
            category="markets",
            title_en="Fed Officials Signal Slower Pace of Rate Cuts",
            title_zh="美联储官员暗示放缓降息步伐",
            summary_en="Several officials pointed to cooling inflation.",
            summary_zh="多位官员指出通胀正在降温。",
            url_display="https://www.bloomberg.com/news/articles/fed-cuts",
            published_at="2026-11-20T14:26:00+08:00",
            created_at="2026-11-20T15:02:00+08:00",
            translated=1,
            title_norm_hash="1" * 32,
            is_important=1,
            important_reason="跨源报道 · GN头条",
        ),
        ArticleItem(
            guid="rtr-untx",
            source="reuters",
            category="markets",
            title_en="Global Bonds Rally on Growth Fears",
            summary_en="Government bonds rallied across Europe.",
            url_display="https://news.google.com/rss/articles/example",
            published_at="2026-11-20T11:40:00+08:00",
            created_at="2026-11-20T12:04:00+08:00",
            translated=2,
            title_norm_hash="2" * 32,
        ),
        ArticleItem(
            guid="rtr-nosum",
            source="reuters",
            category="tech",
            title_en="OpenAI Talks Enterprise Cloud Deal",
            title_zh="OpenAI 洽谈企业云合作",
            url_display="https://news.google.com/rss/articles/example2",
            published_at="2026-11-20T12:34:00+08:00",
            created_at="2026-11-20T13:01:00+08:00",
            translated=1,
            title_norm_hash="3" * 32,
        ),
        # Cross-midnight item belonging to 2026-11-19
        ArticleItem(
            guid="wsj-cross",
            source="wsj",
            category="markets",
            title_en="Nasdaq Advances on Tech Gains",
            title_zh="纳斯达克因科技股上涨收高",
            summary_en="Tech stocks led markets higher.",
            summary_zh="科技股领涨大盘。",
            url_display="https://www.wsj.com/articles/nasdaq-advances",
            published_at="2026-11-19T23:42:00+08:00",
            created_at="2026-11-20T00:05:00+08:00",
            translated=1,
            title_norm_hash="4" * 32,
        ),
    ]
    for it in items:
        db.insert_article(it)

    db.insert_batch(
        BatchRecord(
            ran_at="2026-11-20T15:02:00+08:00",
            duration_sec=14,
            sources_json={"bloomberg": {"ok": True}, "wsj": {"ok": True}, "reuters": {"ok": False}},
            items_new=3,
            items_dup=0,
            translate_ok=2,
            translate_fail=1,
            render_ok=1,
        )
    )


def test_tc_rend_01_to_05_full_template_contract(tmp_config: AppConfig, sqlite_db: SQLiteDatabase) -> None:
    _seed_articles(sqlite_db)
    renderer = SiteRenderer(tmp_config, sqlite_db)
    renderer.render_all(ref_date=REF_DATE)

    index_path = tmp_config.paths.root / "index.html"
    yest_path = tmp_config.paths.daily / "2026-11-19.html"
    arch_idx_path = tmp_config.paths.archive / "index.html"
    assert index_path.exists() and yest_path.exists() and arch_idx_path.exists()

    index_html = index_path.read_text(encoding="utf-8")
    yest_html = yest_path.read_text(encoding="utf-8")

    # TC-REND-01: Zero external CSS/JS/font references
    assert not re.search(r'<link[^>]+href=["\']https?://', index_html, re.I)
    assert not re.search(r'<script[^>]+src=["\']https?://', index_html, re.I)
    assert "@import url(http" not in index_html.lower()

    soup = BeautifulSoup(index_html, "lxml")

    # TC-REND-05: Masthead & interactive attributes contract
    assert soup.find("h1").get_text(strip=True) == "Market Dispatch"
    assert "md_theme" in index_html
    assert soup.find("section", attrs={"data-cat": "markets"}) is not None
    assert soup.find("article", attrs={"data-source": "bloomberg", "data-star": "1"}) is not None

    # TC-REND-02: Dual time formatting (same-day on index, cross-day on 11-19)
    assert "发布 14:26" in soup.get_text(" ", strip=True)
    assert "抓取 15:02" in soup.get_text(" ", strip=True)
    yest_text = BeautifulSoup(yest_html, "lxml").get_text(" ", strip=True)
    assert "发布 11-19 23:42" in yest_text
    assert "抓取 11-20 00:05" in yest_text
    assert "跨零点补入" in yest_text

    # TC-REND-03: Lead briefing, untranslated auto-open details, no-summary tag, safe external links
    assert soup.find("section", id="leadSection") is not None
    untx_art = soup.find("article", class_="untranslated")
    assert untx_art is not None
    assert "未翻译" in untx_art.get_text()
    assert untx_art.find("details", attrs={"open": True}) is not None
    assert "仅标题" in soup.get_text()

    for a in soup.select("article.item a[href^='http']"):
        assert a.get("target") == "_blank"
        assert "noopener" in (a.get("rel") or [])

    # TC-REND-04: Relative links from root index.html vs daily/2026-11-19.html
    assert 'href="daily/2026-11-19.html"' in index_html
    assert 'href="archive/index.html"' in index_html
    assert 'href="../index.html"' in yest_html
    assert 'href="../archive/index.html"' in yest_html


def test_tc_rend_06_atomic_write_preserves_previous_file_on_error(tmp_config: AppConfig) -> None:
    target = tmp_config.paths.root / "index.html"
    atomic_write_text(target, "<html>PREVIOUS GOOD</html>")

    # Simulate failure before replace by passing invalid non-str content
    with pytest.raises(TypeError):
        atomic_write_text(target, None)  # type: ignore[arg-type]

    assert target.read_text(encoding="utf-8") == "<html>PREVIOUS GOOD</html>"


def test_tc_arch_01_and_02_3_day_retention_and_monthly_index(
    tmp_config: AppConfig, sqlite_db: SQLiteDatabase
) -> None:
    daily = tmp_config.paths.daily
    archive = tmp_config.paths.archive
    daily.mkdir(parents=True, exist_ok=True)

    # Create 5 daily HTML files spanning 2 months
    for d in ("2026-11-20", "2026-11-19", "2026-11-18", "2026-11-17", "2026-10-31"):
        (daily / f"{d}.html").write_text(f"<html>{d}</html>", encoding="utf-8")

    moved = archive_old_daily_files(
        daily_dir=daily,
        archive_dir=archive,
        retention_days=3,
        ref_date=REF_DATE,
    )

    assert sorted(moved) == ["2026-10-31", "2026-11-17"]
    assert list_dated_html_files(daily) == ["2026-11-20", "2026-11-19", "2026-11-18"]
    assert list_dated_html_files(archive) == ["2026-11-17", "2026-10-31"]

    renderer = SiteRenderer(tmp_config, sqlite_db)
    renderer.render_all(ref_date=REF_DATE, archived_days_to_refresh=moved)

    arch_index = (archive / "index.html").read_text(encoding="utf-8")
    assert "2026 年 11 月" in arch_index
    assert "2026 年 10 月" in arch_index
    assert 'href="2026-11-17.html"' in arch_index
    assert 'href="2026-10-31.html"' in arch_index

    # Newly archived page should carry archived banner
    archived_page = (archive / "2026-11-17.html").read_text(encoding="utf-8")
    assert "历史归档版面" in archived_page
