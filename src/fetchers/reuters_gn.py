"""Reuters fetcher via Google News RSS (public index, compliance-safe)."""

from __future__ import annotations

from datetime import datetime
import logging
import re

import feedparser
import httpx

from src.fetchers.base import (
    BaseFetcher,
    clean_text,
    extract_entry_published,
    is_within_lookback,
)
from src.models import ArticleItem, SourceFetchResult
from src.time_utils import now_iso, to_iso

logger = logging.getLogger(__name__)

_REUTERS_SUFFIX_RE = re.compile(r"\s*[-–—|]\s*Reuters\s*$", re.IGNORECASE)


def strip_reuters_suffix(title: str) -> str:
    """Remove trailing ' - Reuters' suffix appended by Google News RSS."""
    return _REUTERS_SUFFIX_RE.sub("", title).strip()


class ReutersGoogleNewsFetcher(BaseFetcher):
    """Fetches Reuters headlines from Google News RSS (site:reuters.com when:24h)."""

    source_name = "reuters"

    def __init__(
        self,
        url: str,
        lookback_hours: int = 24,
        timeout_sec: int = 15,
        tz_name: str = "Asia/Shanghai",
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(timeout_sec=timeout_sec, tz_name=tz_name, http_client=http_client)
        self.url = url
        self.lookback_hours = lookback_hours

    def fetch(self, ref_now: datetime | None = None) -> SourceFetchResult:
        result = SourceFetchResult(source=self.source_name)
        created_ts = to_iso(ref_now, self.tz_name) if ref_now else now_iso(self.tz_name)

        try:
            xml_text = self._get_text(self.url)
            parsed = feedparser.parse(xml_text)
        except Exception as exc:  # noqa: BLE001
            err_msg = f"reuters/gn: {exc}"
            logger.warning("Fetch failed: %s", err_msg)
            result.errors.append(err_msg)
            result.ok = False
            return result

        for entry in parsed.entries:
            raw_title = clean_text(entry.get("title"))
            link = (entry.get("link") or "").strip()
            if not raw_title or not link:
                continue

            title = strip_reuters_suffix(raw_title)
            if not title:
                continue

            pub_iso = extract_entry_published(entry, self.tz_name)
            if not is_within_lookback(pub_iso, self.lookback_hours, self.tz_name, ref_now):
                continue

            # Google News description is usually just an HTML anchor repeating the title;
            # leave summary_en=None so fetch_meta may optionally enrich og:description.
            result.items.append(
                ArticleItem(
                    source=self.source_name,
                    category="world",  # Re-classified via keywords in pipeline
                    feed_topic=None,
                    title_en=title,
                    summary_en=None,
                    url_display=link,
                    published_at=pub_iso,
                    created_at=created_ts,
                )
            )

        result.fetched = len(result.items)
        result.ok = True
        return result
