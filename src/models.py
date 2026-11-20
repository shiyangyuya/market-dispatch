"""Core domain models and constants for Market Dispatch."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CATEGORIES: tuple[dict[str, str], ...] = (
    {"key": "markets", "icon": "📈", "name": "市场与经济", "en_name": "MARKETS & ECONOMY"},
    {"key": "tech", "icon": "💻", "name": "科技", "en_name": "TECHNOLOGY & AI"},
    {"key": "world", "icon": "🌍", "name": "政治与世界", "en_name": "WORLD & GEOPOLITICS"},
    {"key": "us", "icon": "🇺🇸", "name": "美国相关", "en_name": "US POLITICS & POLICY"},
    {"key": "china", "icon": "🇨🇳", "name": "中国相关", "en_name": "CHINA & GREATER CHINA"},
)

CATEGORY_KEYS: tuple[str, ...] = tuple(c["key"] for c in CATEGORIES)
CATEGORY_MAP: dict[str, dict[str, str]] = {c["key"]: c for c in CATEGORIES}

SOURCES: dict[str, dict[str, str]] = {
    "bloomberg": {"label": "彭博", "cls": "bbg", "en_name": "Bloomberg"},
    "reuters": {"label": "路透", "cls": "rtr", "en_name": "Reuters"},
    "wsj": {"label": "WSJ", "cls": "wsj", "en_name": "Wall Street Journal"},
}


@dataclass
class ArticleItem:
    """Represents a single news metadata item."""

    source: str
    title_en: str
    url_display: str
    guid: str = ""
    category: str = "world"
    feed_topic: str | None = None
    title_zh: str | None = None
    summary_en: str | None = None
    summary_zh: str | None = None
    published_at: str | None = None  # ISO-8601 Asia/Shanghai
    created_at: str = ""             # ISO-8601 Asia/Shanghai
    translated: int = 0              # 0=untranslated, 1=translated, 2=failed
    title_norm_hash: str = ""
    is_important: int = 0
    important_reason: str | None = None
    id: int | None = None

    @property
    def effective_day(self) -> str:
        """Return YYYY-MM-DD归属日 based on COALESCE(published_at, created_at)."""
        ts = self.published_at or self.created_at
        return ts[:10] if ts and len(ts) >= 10 else ""


@dataclass
class SourceFetchResult:
    """Fetch telemetry for a single news source."""

    source: str
    ok: bool = True
    fetched: int = 0
    errors: list[str] = field(default_factory=list)
    items: list[ArticleItem] = field(default_factory=list)

    def to_summary_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "fetched": self.fetched,
            "errors": self.errors,
        }


@dataclass
class BatchRecord:
    """Execution record persisted into fetch_batches."""

    ran_at: str
    duration_sec: int
    sources_json: dict[str, Any]
    items_new: int = 0
    items_dup: int = 0
    translate_ok: int = 0
    translate_fail: int = 0
    render_ok: int = 0
    id: int | None = None


@dataclass
class DayStats:
    """Aggregated article counts for a single date."""

    date: str
    total: int = 0
    important: int = 0
    by_category: dict[str, int] = field(
        default_factory=lambda: {k: 0 for k in CATEGORY_KEYS}
    )
