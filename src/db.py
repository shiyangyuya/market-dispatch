"""Dual-dialect storage layer (SQLite primary with FTS5 + optional MySQL backend)."""

from __future__ import annotations

from abc import ABC, abstractmethod
import json
from pathlib import Path
import sqlite3
from typing import Any

from src.config import AppConfig, StorageConfig
from src.models import ArticleItem, BatchRecord, CATEGORY_KEYS, DayStats

SQLITE_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS articles (
      id               INTEGER PRIMARY KEY AUTOINCREMENT,
      guid             TEXT NOT NULL UNIQUE,
      source           TEXT NOT NULL,
      category         TEXT NOT NULL,
      feed_topic       TEXT,
      title_en         TEXT NOT NULL,
      title_zh         TEXT,
      summary_en       TEXT,
      summary_zh       TEXT,
      url_display      TEXT NOT NULL,
      published_at     TEXT,
      created_at       TEXT NOT NULL,
      translated       INTEGER NOT NULL DEFAULT 0,
      title_norm_hash  TEXT NOT NULL,
      is_important     INTEGER NOT NULL DEFAULT 0,
      important_reason TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_dedup ON articles(source, title_norm_hash)",
    "CREATE INDEX IF NOT EXISTS idx_day   ON articles(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_pub   ON articles(published_at)",
    "CREATE INDEX IF NOT EXISTS idx_cat   ON articles(category, published_at)",
    """
    CREATE TABLE IF NOT EXISTS fetch_batches (
      id             INTEGER PRIMARY KEY AUTOINCREMENT,
      ran_at         TEXT NOT NULL,
      duration_sec   INTEGER NOT NULL,
      sources_json   TEXT,
      items_new      INTEGER DEFAULT 0,
      items_dup      INTEGER DEFAULT 0,
      translate_ok   INTEGER DEFAULT 0,
      translate_fail INTEGER DEFAULT 0,
      render_ok      INTEGER DEFAULT 0
    )
    """,
    # FR11: Reserved FTS5 full-text index & sync triggers (zero backfill needed later)
    """
    CREATE VIRTUAL TABLE IF NOT EXISTS articles_fts USING fts5(
      title_en, title_zh, summary_en, summary_zh,
      content='articles', content_rowid='id', tokenize='unicode61'
    )
    """,
    """
    CREATE TRIGGER IF NOT EXISTS articles_fts_ai AFTER INSERT ON articles BEGIN
      INSERT INTO articles_fts(rowid, title_en, title_zh, summary_en, summary_zh)
      VALUES (new.id, new.title_en, new.title_zh, new.summary_en, new.summary_zh);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS articles_fts_ad AFTER DELETE ON articles BEGIN
      INSERT INTO articles_fts(articles_fts, rowid, title_en, title_zh, summary_en, summary_zh)
      VALUES ('delete', old.id, old.title_en, old.title_zh, old.summary_en, old.summary_zh);
    END
    """,
    """
    CREATE TRIGGER IF NOT EXISTS articles_fts_au AFTER UPDATE ON articles BEGIN
      INSERT INTO articles_fts(articles_fts, rowid, title_en, title_zh, summary_en, summary_zh)
      VALUES ('delete', old.id, old.title_en, old.title_zh, old.summary_en, old.summary_zh);
      INSERT INTO articles_fts(rowid, title_en, title_zh, summary_en, summary_zh)
      VALUES (new.id, new.title_en, new.title_zh, new.summary_en, new.summary_zh);
    END
    """,
)


def get_mysql_ddl() -> tuple[str, ...]:
    """Return MySQL utf8mb4 DDL statements equivalent to SQLite schema."""
    return (
        """
        CREATE TABLE IF NOT EXISTS articles (
          id               BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
          guid             VARCHAR(191) NOT NULL UNIQUE,
          source           VARCHAR(16) NOT NULL,
          category         VARCHAR(16) NOT NULL,
          feed_topic       VARCHAR(32) NULL,
          title_en         VARCHAR(512) NOT NULL,
          title_zh         VARCHAR(512) NULL,
          summary_en       TEXT NULL,
          summary_zh       TEXT NULL,
          url_display      VARCHAR(1024) NOT NULL,
          published_at     VARCHAR(32) NULL,
          created_at       VARCHAR(32) NOT NULL,
          translated       TINYINT NOT NULL DEFAULT 0,
          title_norm_hash  CHAR(32) NOT NULL,
          is_important     TINYINT NOT NULL DEFAULT 0,
          important_reason VARCHAR(128) NULL,
          INDEX idx_dedup (source, title_norm_hash),
          INDEX idx_day (created_at),
          INDEX idx_pub (published_at),
          INDEX idx_cat (category, published_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
        """
        CREATE TABLE IF NOT EXISTS fetch_batches (
          id             BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
          ran_at         VARCHAR(32) NOT NULL,
          duration_sec   INT NOT NULL,
          sources_json   JSON NULL,
          items_new      INT DEFAULT 0,
          items_dup      INT DEFAULT 0,
          translate_ok   INT DEFAULT 0,
          translate_fail INT DEFAULT 0,
          render_ok      TINYINT DEFAULT 0
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """,
    )


def _row_to_article(row: dict[str, Any]) -> ArticleItem:
    return ArticleItem(
        id=row["id"],
        guid=row["guid"],
        source=row["source"],
        category=row["category"],
        feed_topic=row["feed_topic"],
        title_en=row["title_en"],
        title_zh=row["title_zh"],
        summary_en=row["summary_en"],
        summary_zh=row["summary_zh"],
        url_display=row["url_display"],
        published_at=row["published_at"],
        created_at=row["created_at"],
        translated=int(row["translated"]),
        title_norm_hash=row["title_norm_hash"],
        is_important=int(row["is_important"]),
        important_reason=row["important_reason"],
    )


class BaseDatabase(ABC):
    """Unified database repository interface across SQLite and MySQL."""

    @abstractmethod
    def init_db(self) -> None: ...

    @abstractmethod
    def close(self) -> None: ...

    @abstractmethod
    def exists_by_guid(self, guid: str) -> bool: ...

    @abstractmethod
    def exists_by_title_hash(self, source: str, title_norm_hash: str, day_str: str) -> bool: ...

    @abstractmethod
    def insert_article(self, item: ArticleItem) -> int | None: ...

    @abstractmethod
    def update_summary_en(self, article_id: int, summary_en: str | None) -> None: ...

    @abstractmethod
    def update_translation(
        self,
        article_id: int,
        title_zh: str | None,
        summary_zh: str | None,
        translated: int,
    ) -> None: ...

    @abstractmethod
    def update_importance(
        self,
        article_id: int,
        is_important: int,
        important_reason: str | None,
    ) -> None: ...

    @abstractmethod
    def get_articles_by_day(self, day_str: str) -> list[ArticleItem]: ...

    @abstractmethod
    def get_day_stats(self, day_str: str) -> DayStats: ...

    @abstractmethod
    def get_all_day_stats(self) -> dict[str, DayStats]: ...

    @abstractmethod
    def insert_batch(self, batch: BatchRecord) -> int: ...

    @abstractmethod
    def get_batches_by_day(self, day_str: str) -> list[BatchRecord]: ...

    @abstractmethod
    def get_recent_batches(self, limit: int = 10) -> list[BatchRecord]: ...

    @abstractmethod
    def get_total_article_count(self) -> int: ...

    def vacuum(self) -> None:
        """Optional database compaction hook."""


class SQLiteDatabase(BaseDatabase):
    """SQLite primary backend with FTS5 full-text index & sync triggers."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def init_db(self) -> None:
        with self.conn:
            for stmt in SQLITE_DDL:
                self.conn.execute(stmt)

    def close(self) -> None:
        self.conn.close()

    def exists_by_guid(self, guid: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM articles WHERE guid = ? LIMIT 1", (guid,)
        ).fetchone()
        return row is not None

    def exists_by_title_hash(self, source: str, title_norm_hash: str, day_str: str) -> bool:
        row = self.conn.execute(
            """
            SELECT 1 FROM articles
            WHERE source = ?
              AND title_norm_hash = ?
              AND substr(COALESCE(published_at, created_at), 1, 10) = ?
            LIMIT 1
            """,
            (source, title_norm_hash, day_str),
        ).fetchone()
        return row is not None

    def insert_article(self, item: ArticleItem) -> int | None:
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT OR IGNORE INTO articles (
                  guid, source, category, feed_topic,
                  title_en, title_zh, summary_en, summary_zh,
                  url_display, published_at, created_at,
                  translated, title_norm_hash, is_important, important_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    item.guid,
                    item.source,
                    item.category,
                    item.feed_topic,
                    item.title_en,
                    item.title_zh,
                    item.summary_en,
                    item.summary_zh,
                    item.url_display,
                    item.published_at,
                    item.created_at,
                    item.translated,
                    item.title_norm_hash,
                    item.is_important,
                    item.important_reason,
                ),
            )
            if cur.rowcount == 0:
                return None
            item.id = int(cur.lastrowid)
            return item.id

    def update_summary_en(self, article_id: int, summary_en: str | None) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE articles SET summary_en = ? WHERE id = ?",
                (summary_en, article_id),
            )

    def update_translation(
        self,
        article_id: int,
        title_zh: str | None,
        summary_zh: str | None,
        translated: int,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE articles
                SET title_zh = ?, summary_zh = ?, translated = ?
                WHERE id = ?
                """,
                (title_zh, summary_zh, translated, article_id),
            )

    def update_importance(
        self,
        article_id: int,
        is_important: int,
        important_reason: str | None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE articles
                SET is_important = ?, important_reason = ?
                WHERE id = ?
                """,
                (is_important, important_reason, article_id),
            )

    def get_articles_by_day(self, day_str: str) -> list[ArticleItem]:
        rows = self.conn.execute(
            """
            SELECT * FROM articles
            WHERE substr(COALESCE(published_at, created_at), 1, 10) = ?
            ORDER BY is_important DESC, COALESCE(published_at, created_at) DESC, id DESC
            """,
            (day_str,),
        ).fetchall()
        return [_row_to_article(dict(r)) for r in rows]

    def get_day_stats(self, day_str: str) -> DayStats:
        stats = DayStats(date=day_str)
        rows = self.conn.execute(
            """
            SELECT category, COUNT(*) AS cnt, SUM(is_important) AS imp
            FROM articles
            WHERE substr(COALESCE(published_at, created_at), 1, 10) = ?
            GROUP BY category
            """,
            (day_str,),
        ).fetchall()
        for r in rows:
            cat = r["category"]
            cnt = int(r["cnt"])
            imp = int(r["imp"] or 0)
            if cat in stats.by_category:
                stats.by_category[cat] = cnt
            stats.total += cnt
            stats.important += imp
        return stats

    def get_all_day_stats(self) -> dict[str, DayStats]:
        result: dict[str, DayStats] = {}
        rows = self.conn.execute(
            """
            SELECT
              substr(COALESCE(published_at, created_at), 1, 10) AS day_str,
              category,
              COUNT(*) AS cnt,
              SUM(is_important) AS imp
            FROM articles
            GROUP BY day_str, category
            ORDER BY day_str DESC
            """
        ).fetchall()
        for r in rows:
            day_str = r["day_str"]
            if day_str not in result:
                result[day_str] = DayStats(date=day_str)
            st = result[day_str]
            cat = r["category"]
            cnt = int(r["cnt"])
            imp = int(r["imp"] or 0)
            if cat in st.by_category:
                st.by_category[cat] = cnt
            st.total += cnt
            st.important += imp
        return result

    def insert_batch(self, batch: BatchRecord) -> int:
        with self.conn:
            cur = self.conn.execute(
                """
                INSERT INTO fetch_batches (
                  ran_at, duration_sec, sources_json,
                  items_new, items_dup, translate_ok, translate_fail, render_ok
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.ran_at,
                    batch.duration_sec,
                    json.dumps(batch.sources_json, ensure_ascii=False),
                    batch.items_new,
                    batch.items_dup,
                    batch.translate_ok,
                    batch.translate_fail,
                    batch.render_ok,
                ),
            )
            batch.id = int(cur.lastrowid)
            return batch.id

    def _rows_to_batches(self, rows: list[sqlite3.Row]) -> list[BatchRecord]:
        out: list[BatchRecord] = []
        for r in rows:
            raw_json = r["sources_json"]
            try:
                parsed = json.loads(raw_json) if raw_json else {}
            except json.JSONDecodeError:
                parsed = {}
            out.append(
                BatchRecord(
                    id=int(r["id"]),
                    ran_at=r["ran_at"],
                    duration_sec=int(r["duration_sec"]),
                    sources_json=parsed,
                    items_new=int(r["items_new"] or 0),
                    items_dup=int(r["items_dup"] or 0),
                    translate_ok=int(r["translate_ok"] or 0),
                    translate_fail=int(r["translate_fail"] or 0),
                    render_ok=int(r["render_ok"] or 0),
                )
            )
        return out

    def get_batches_by_day(self, day_str: str) -> list[BatchRecord]:
        rows = self.conn.execute(
            """
            SELECT * FROM fetch_batches
            WHERE substr(ran_at, 1, 10) = ?
            ORDER BY ran_at ASC, id ASC
            """,
            (day_str,),
        ).fetchall()
        return self._rows_to_batches(rows)

    def get_recent_batches(self, limit: int = 10) -> list[BatchRecord]:
        rows = self.conn.execute(
            "SELECT * FROM fetch_batches ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return self._rows_to_batches(rows)

    def get_total_article_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) AS cnt FROM articles").fetchone()
        return int(row["cnt"]) if row else 0

    def search_fts(self, query: str, limit: int = 20) -> list[ArticleItem]:
        """Query the reserved FTS5 virtual table (FR11 verification & CLI utility)."""
        rows = self.conn.execute(
            """
            SELECT a.* FROM articles a
            JOIN articles_fts f ON a.id = f.rowid
            WHERE articles_fts MATCH ?
            ORDER BY COALESCE(a.published_at, a.created_at) DESC
            LIMIT ?
            """,
            (query, limit),
        ).fetchall()
        return [_row_to_article(dict(r)) for r in rows]

    def vacuum(self) -> None:
        self.conn.execute("VACUUM")


class MySQLDatabase(BaseDatabase):
    """Optional MySQL backend for local deployment mode."""

    def __init__(self, cfg: StorageConfig) -> None:
        try:
            import pymysql
            from pymysql.cursors import DictCursor
        except ImportError as exc:
            raise RuntimeError(
                "MySQL backend requires pymysql. Install via: pip install pymysql"
            ) from exc

        self._pymysql = pymysql
        self._cfg = cfg
        self.conn = pymysql.connect(
            host=cfg.mysql_host,
            port=cfg.mysql_port,
            user=cfg.mysql_user,
            password=cfg.mysql_password,
            database=cfg.mysql_database,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )

    def init_db(self) -> None:
        with self.conn.cursor() as cur:
            for stmt in get_mysql_ddl():
                cur.execute(stmt)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def exists_by_guid(self, guid: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute("SELECT 1 FROM articles WHERE guid = %s LIMIT 1", (guid,))
            return cur.fetchone() is not None

    def exists_by_title_hash(self, source: str, title_norm_hash: str, day_str: str) -> bool:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM articles
                WHERE source = %s
                  AND title_norm_hash = %s
                  AND LEFT(COALESCE(published_at, created_at), 10) = %s
                LIMIT 1
                """,
                (source, title_norm_hash, day_str),
            )
            return cur.fetchone() is not None

    def insert_article(self, item: ArticleItem) -> int | None:
        with self.conn.cursor() as cur:
            rows = cur.execute(
                """
                INSERT IGNORE INTO articles (
                  guid, source, category, feed_topic,
                  title_en, title_zh, summary_en, summary_zh,
                  url_display, published_at, created_at,
                  translated, title_norm_hash, is_important, important_reason
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    item.guid,
                    item.source,
                    item.category,
                    item.feed_topic,
                    item.title_en,
                    item.title_zh,
                    item.summary_en,
                    item.summary_zh,
                    item.url_display,
                    item.published_at,
                    item.created_at,
                    item.translated,
                    item.title_norm_hash,
                    item.is_important,
                    item.important_reason,
                ),
            )
            self.conn.commit()
            if rows == 0:
                return None
            item.id = int(cur.lastrowid)
            return item.id

    def update_summary_en(self, article_id: int, summary_en: str | None) -> None:
        with self.conn.cursor() as cur:
            cur.execute("UPDATE articles SET summary_en = %s WHERE id = %s", (summary_en, article_id))
        self.conn.commit()

    def update_translation(
        self,
        article_id: int,
        title_zh: str | None,
        summary_zh: str | None,
        translated: int,
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE articles SET title_zh = %s, summary_zh = %s, translated = %s WHERE id = %s",
                (title_zh, summary_zh, translated, article_id),
            )
        self.conn.commit()

    def update_importance(
        self,
        article_id: int,
        is_important: int,
        important_reason: str | None,
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                "UPDATE articles SET is_important = %s, important_reason = %s WHERE id = %s",
                (is_important, important_reason, article_id),
            )
        self.conn.commit()

    def get_articles_by_day(self, day_str: str) -> list[ArticleItem]:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT * FROM articles
                WHERE LEFT(COALESCE(published_at, created_at), 10) = %s
                ORDER BY is_important DESC, COALESCE(published_at, created_at) DESC, id DESC
                """,
                (day_str,),
            )
            return [_row_to_article(r) for r in cur.fetchall()]

    def get_day_stats(self, day_str: str) -> DayStats:
        stats = DayStats(date=day_str)
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT category, COUNT(*) AS cnt, SUM(is_important) AS imp
                FROM articles
                WHERE LEFT(COALESCE(published_at, created_at), 10) = %s
                GROUP BY category
                """,
                (day_str,),
            )
            for r in cur.fetchall():
                cat = r["category"]
                cnt = int(r["cnt"])
                imp = int(r["imp"] or 0)
                if cat in stats.by_category:
                    stats.by_category[cat] = cnt
                stats.total += cnt
                stats.important += imp
        return stats

    def get_all_day_stats(self) -> dict[str, DayStats]:
        result: dict[str, DayStats] = {}
        with self.conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                  LEFT(COALESCE(published_at, created_at), 10) AS day_str,
                  category,
                  COUNT(*) AS cnt,
                  SUM(is_important) AS imp
                FROM articles
                GROUP BY day_str, category
                ORDER BY day_str DESC
                """
            )
            for r in cur.fetchall():
                day_str = r["day_str"]
                if day_str not in result:
                    result[day_str] = DayStats(date=day_str)
                st = result[day_str]
                cat = r["category"]
                cnt = int(r["cnt"])
                imp = int(r["imp"] or 0)
                if cat in st.by_category:
                    st.by_category[cat] = cnt
                st.total += cnt
                st.important += imp
        return result

    def insert_batch(self, batch: BatchRecord) -> int:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO fetch_batches (
                  ran_at, duration_sec, sources_json,
                  items_new, items_dup, translate_ok, translate_fail, render_ok
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    batch.ran_at,
                    batch.duration_sec,
                    json.dumps(batch.sources_json, ensure_ascii=False),
                    batch.items_new,
                    batch.items_dup,
                    batch.translate_ok,
                    batch.translate_fail,
                    batch.render_ok,
                ),
            )
            self.conn.commit()
            batch.id = int(cur.lastrowid)
            return batch.id

    def _rows_to_batches(self, rows: list[dict[str, Any]]) -> list[BatchRecord]:
        out: list[BatchRecord] = []
        for r in rows:
            raw = r["sources_json"]
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = {}
            else:
                parsed = raw or {}
            out.append(
                BatchRecord(
                    id=int(r["id"]),
                    ran_at=r["ran_at"],
                    duration_sec=int(r["duration_sec"]),
                    sources_json=parsed,
                    items_new=int(r["items_new"] or 0),
                    items_dup=int(r["items_dup"] or 0),
                    translate_ok=int(r["translate_ok"] or 0),
                    translate_fail=int(r["translate_fail"] or 0),
                    render_ok=int(r["render_ok"] or 0),
                )
            )
        return out

    def get_batches_by_day(self, day_str: str) -> list[BatchRecord]:
        with self.conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM fetch_batches WHERE LEFT(ran_at, 10) = %s ORDER BY ran_at ASC, id ASC",
                (day_str,),
            )
            return self._rows_to_batches(cur.fetchall())

    def get_recent_batches(self, limit: int = 10) -> list[BatchRecord]:
        with self.conn.cursor() as cur:
            cur.execute("SELECT * FROM fetch_batches ORDER BY id DESC LIMIT %s", (limit,))
            return self._rows_to_batches(cur.fetchall())

    def get_total_article_count(self) -> int:
        with self.conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM articles")
            row = cur.fetchone()
            return int(row["cnt"]) if row else 0


def create_database(cfg: AppConfig) -> BaseDatabase:
    """Factory creating the configured SQLite or MySQL database backend."""
    if cfg.storage.engine == "mysql":
        return MySQLDatabase(cfg.storage)
    return SQLiteDatabase(cfg.storage.sqlite_path)
