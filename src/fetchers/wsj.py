"""Wall Street Journal official RSS fetcher (feeds.content.dowjones.io)."""

from __future__ import annotations

from datetime import datetime
import logging

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


class WSJFetcher(BaseFetcher):
    """Fetches WSJ live official RSS feeds via feeds.content.dowjones.io."""

    source_name = "wsj"

    def __init__(
        self,
        base_url: str,
        feeds: dict[str, str],
        lookback_hours: int = 24,
        timeout_sec: int = 15,
        tz_name: str = "Asia/Shanghai",
        http_client: httpx.Client | None = None,
    ) -> None:
        super().__init__(timeout_sec=timeout_sec, tz_name=tz_name, http_client=http_client)
        self.base_url = base_url.rstrip("/")
        self.feeds = feeds
        self.lookback_hours = lookback_hours

    def fetch(self, ref_now: datetime | None = None) -> SourceFetchResult:
        result = SourceFetchResult(source=self.source_name)
        created_ts = to_iso(ref_now, self.tz_name) if ref_now else now_iso(self.tz_name)
        succeeded_feeds = 0

        for feed_name, default_cat in self.feeds.items():
            url = f"{self.base_url}/{feed_name}"
            try:
                xml_text = self._get_text(url)
                parsed = feedparser.parse(xml_text)
                succeeded_feeds += 1
            except Exception as exc:  # noqa: BLE001
                err_msg = f"wsj/{feed_name}: {exc}"
                logger.warning("Fetch failed: %s", err_msg)
                result.errors.append(err_msg)
                continue

            for entry in parsed.entries:
                title = clean_text(entry.get("title"))
                link = (entry.get("link") or "").strip()
                if not title or not link:
                    continue

                pub_iso = extract_entry_published(entry, self.tz_name)
                if not is_within_lookback(pub_iso, self.lookback_hours, self.tz_name, ref_now):
                    continue

                summary = clean_text(entry.get("summary") or entry.get("description"))
                result.items.append(
                    ArticleItem(
                        source=self.source_name,
                        category=default_cat,
                        feed_topic=feed_name,
                        title_en=title,
                        summary_en=summary,
                        url_display=link,
                        published_at=pub_iso,
                        created_at=created_ts,
                    )
                )

        result.fetched = len(result.items)
        result.ok = succeeded_feeds > 0 or not self.feeds
        return result
