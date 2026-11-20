"""Base RSS fetcher abstractions and shared HTML/XML sanitization utilities."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime, timedelta
import html
import re
from typing import Any

from bs4 import BeautifulSoup
import httpx

from src.models import SourceFetchResult
from src.time_utils import now_local, parse_iso, parse_rss_datetime

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
}

_WHITESPACE_RE = re.compile(r"\s+")


def clean_text(raw: str | None) -> str | None:
    """Strip HTML tags, unescape entities, and collapse whitespace."""
    if not raw:
        return None
    text = raw
    if "<" in text and ">" in text:
        text = BeautifulSoup(text, "lxml").get_text(" ")
    text = html.unescape(text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text or None


def is_within_lookback(
    published_at: str | None,
    lookback_hours: int,
    tz_name: str = "Asia/Shanghai",
    ref_now: datetime | None = None,
) -> bool:
    """Return True if published_at is missing or within [now - lookback_hours, now + 1h]."""
    if not published_at:
        return True
    pub_dt = parse_iso(published_at, tz_name)
    if pub_dt is None:
        return True
    now_dt = ref_now or now_local(tz_name)
    cutoff = now_dt - timedelta(hours=lookback_hours)
    return pub_dt >= cutoff


def extract_entry_published(entry: Any, tz_name: str = "Asia/Shanghai") -> str | None:
    """Extract normalized ISO-8601 published_at from a feedparser entry."""
    raw = entry.get("published") or entry.get("updated")
    parsed_struct = entry.get("published_parsed") or entry.get("updated_parsed")
    return parse_rss_datetime(raw_value=raw, parsed_struct=parsed_struct, tz_name=tz_name)


class BaseFetcher(ABC):
    """Abstract fetcher returning a SourceFetchResult (never raises unhandled errors)."""

    source_name: str = ""

    def __init__(
        self,
        timeout_sec: int = 15,
        tz_name: str = "Asia/Shanghai",
        http_client: httpx.Client | None = None,
    ) -> None:
        self.timeout_sec = timeout_sec
        self.tz_name = tz_name
        self._external_client = http_client

    def _get_text(self, url: str) -> str:
        if self._external_client is not None:
            resp = self._external_client.get(url, headers=DEFAULT_HEADERS, timeout=self.timeout_sec)
            resp.raise_for_status()
            return resp.text

        with httpx.Client(follow_redirects=True, timeout=self.timeout_sec) as client:
            resp = client.get(url, headers=DEFAULT_HEADERS)
            resp.raise_for_status()
            return resp.text

    @abstractmethod
    def fetch(self, ref_now: datetime | None = None) -> SourceFetchResult: ...
