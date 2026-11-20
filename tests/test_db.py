"""Tests for SQLite/MySQL storage layer, dual timestamps, and FTS5 triggers (TC-DB-*)."""

from __future__ import annotations

from src.db import SQLiteDatabase, get_mysql_ddl
from src.models import ArticleItem, BatchRecord


def _sample_article(**overrides) -> ArticleItem:
    base = dict(
        guid="https://www.bloomberg.com/news/articles/2026-11-20/fed-rates",
        source="bloomberg",
        category="markets",
        feed_topic="markets",
        title_en="Fed Officials Signal Slower Pace of Rate Cuts",
        summary_en="Several Federal Reserve officials pointed to cooler inflation.",
        url_display="https://www.bloomberg.com/news/articles/2026-11-20/fed-rates",
        published_at="2026-11-20T14:26:00+08:00",
        created_at="2026-11-20T15:02:00+08:00",
        title_norm_hash="a" * 32,
    )
    base.update(overrides)
    return ArticleItem(**base)


def test_tc_db_01_init_db_idempotent_and_fts_present(sqlite_db: SQLiteDatabase) -> None:
    # Calling init_db a second time must be completely idempotent
    sqlite_db.init_db()

    tables = {
        row["name"]
        for row in sqlite_db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table', 'trigger')"
        ).fetchall()
    }
    assert {"articles", "fetch_batches", "articles_fts"} <= tables
    assert {"articles_fts_ai", "articles_fts_ad", "articles_fts_au"} <= tables


def test_tc_db_02_dual_timestamps_and_publish_day_grouping(sqlite_db: SQLiteDatabase) -> None:
    cross_item = _sample_article(
        guid="https://www.wsj.com/articles/cross-midnight",
        source="wsj",
        published_at="2026-11-19T23:40:00+08:00",
        created_at="2026-11-20T00:05:00+08:00",
        title_norm_hash="b" * 32,
    )
    row_id = sqlite_db.insert_article(cross_item)
    assert row_id is not None

    nov19 = sqlite_db.get_articles_by_day("2026-11-19")
    nov20 = sqlite_db.get_articles_by_day("2026-11-20")
    assert len(nov19) == 1
    assert nov19[0].published_at == "2026-11-19T23:40:00+08:00"
    assert nov19[0].created_at == "2026-11-20T00:05:00+08:00"
    assert len(nov20) == 0


def test_tc_db_03_unique_guid_prevents_duplicates(sqlite_db: SQLiteDatabase) -> None:
    art1 = _sample_article()
    art2 = _sample_article(title_en="Different title same guid")
    assert sqlite_db.insert_article(art1) is not None
    assert sqlite_db.insert_article(art2) is None
    assert sqlite_db.get_total_article_count() == 1


def test_tc_db_04_fts5_triggers_sync_on_insert_update_delete(sqlite_db: SQLiteDatabase) -> None:
    art = _sample_article()
    art_id = sqlite_db.insert_article(art)
    assert art_id is not None

    # INSERT trigger: English searchable immediately
    hits = sqlite_db.search_fts("Federal")
    assert len(hits) == 1 and hits[0].id == art_id

    # UPDATE trigger: Chinese translation searchable after update
    sqlite_db.update_translation(art_id, "美联储暗示放缓降息步伐", "多位官员提及通胀放缓", 1)
    zh_hits = sqlite_db.search_fts("美联储暗示放缓降息步伐")
    assert len(zh_hits) == 1 and zh_hits[0].id == art_id

    # DELETE trigger: removed from FTS index immediately
    with sqlite_db.conn:
        sqlite_db.conn.execute("DELETE FROM articles WHERE id = ?", (art_id,))
    assert sqlite_db.search_fts("Federal") == []


def test_tc_db_05_fetch_batches_crud(sqlite_db: SQLiteDatabase) -> None:
    b1 = BatchRecord(
        ran_at="2026-11-20T14:02:00+08:00",
        duration_sec=18,
        sources_json={"bloomberg": {"ok": True, "fetched": 20}, "reuters": {"ok": False, "fetched": 0}},
        items_new=12,
        items_dup=8,
        translate_ok=11,
        translate_fail=1,
        render_ok=1,
    )
    bid = sqlite_db.insert_batch(b1)
    assert bid > 0

    day_batches = sqlite_db.get_batches_by_day("2026-11-20")
    assert len(day_batches) == 1
    assert day_batches[0].sources_json["reuters"]["ok"] is False
    assert day_batches[0].items_new == 12


def test_tc_db_06_mysql_ddl_dialect() -> None:
    stmts = get_mysql_ddl()
    joined = "\n".join(stmts)
    assert "CREATE TABLE IF NOT EXISTS articles" in joined
    assert "CREATE TABLE IF NOT EXISTS fetch_batches" in joined
    assert "published_at" in joined and "created_at" in joined
    assert "utf8mb4" in joined
