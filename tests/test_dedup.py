"""Tests for URL canonicalization, title hashing, and L1/L2 dedup (TC-DEDUP-*)."""

from __future__ import annotations

from src.db import SQLiteDatabase
from src.dedup import build_guid, canonicalize_url, filter_duplicates, title_md5
from src.models import ArticleItem


def test_tc_dedup_01_canonicalize_url() -> None:
    url = "HTTPS://WWW.WSJ.COM/articles/abc-123/?mod=rss_markets&utm_source=feed&id=42#section"
    assert canonicalize_url(url) == "https://www.wsj.com/articles/abc-123?id=42"


def test_tc_dedup_02_l1_url_dedup(sqlite_db: SQLiteDatabase) -> None:
    a1 = ArticleItem(
        source="wsj",
        title_en="Oil Prices Steady",
        url_display="https://www.wsj.com/articles/oil-steady?mod=rss_markets",
        published_at="2026-11-20T12:00:00+08:00",
        created_at="2026-11-20T12:05:00+08:00",
    )
    a2 = ArticleItem(
        source="wsj",
        title_en="Oil Prices Steady — Updated Headline",
        url_display="https://www.wsj.com/articles/oil-steady?utm_campaign=x",
        published_at="2026-11-20T12:30:00+08:00",
        created_at="2026-11-20T13:05:00+08:00",
    )
    new_items, dup = filter_duplicates([a1, a2], sqlite_db)
    assert len(new_items) == 1
    assert dup == 1
    assert a1.guid == a2.guid


def test_tc_dedup_03_reuters_guid_uses_title_hash() -> None:
    h = title_md5("Fed Cuts Rates by 25bps!")
    g = build_guid("reuters", "https://news.google.com/rss/articles/rotating-token", h)
    assert g == f"reuters:{h}"


def test_tc_dedup_04_l2_same_source_same_day_title_hash(sqlite_db: SQLiteDatabase) -> None:
    first = ArticleItem(
        source="bloomberg",
        title_en="Fed Officials Signal Slower Rate Cuts!",
        url_display="https://www.bloomberg.com/news/articles/2026-11-20/story-a",
        published_at="2026-11-20T10:00:00+08:00",
        created_at="2026-11-20T10:05:00+08:00",
    )
    new1, _ = filter_duplicates([first], sqlite_db)
    sqlite_db.insert_article(new1[0])

    # Different URL, punctuation/case changed, same source & same effective day
    second = ArticleItem(
        source="bloomberg",
        title_en="fed officials signal slower rate cuts",
        url_display="https://www.bloomberg.com/news/articles/2026-11-20/story-b",
        published_at="2026-11-20T11:00:00+08:00",
        created_at="2026-11-20T11:05:00+08:00",
    )
    new2, dup2 = filter_duplicates([second], sqlite_db)
    assert new2 == []
    assert dup2 == 1


def test_tc_dedup_05_cross_source_not_merged(sqlite_db: SQLiteDatabase) -> None:
    b = ArticleItem(
        source="bloomberg",
        title_en="Nvidia Beats Quarterly Revenue Forecasts",
        url_display="https://www.bloomberg.com/news/articles/nvidia-beats",
        published_at="2026-11-20T15:00:00+08:00",
        created_at="2026-11-20T15:05:00+08:00",
    )
    w = ArticleItem(
        source="wsj",
        title_en="Nvidia Beats Quarterly Revenue Forecasts",
        url_display="https://www.wsj.com/articles/nvidia-beats",
        published_at="2026-11-20T15:02:00+08:00",
        created_at="2026-11-20T15:05:00+08:00",
    )
    new_items, dup = filter_duplicates([b, w], sqlite_db)
    assert len(new_items) == 2
    assert dup == 0
