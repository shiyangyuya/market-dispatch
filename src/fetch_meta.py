"""Compliance-safe og:description / meta description fallback for Reuters items."""

from __future__ import annotations

import logging
import time
from typing import Callable

from bs4 import BeautifulSoup
import httpx

from src.fetchers.base import clean_text
from src.models import ArticleItem

logger = logging.getLogger(__name__)

META_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
DETERMINISTIC_STATUS_CODES = frozenset({400, 401, 403, 404, 410, 451})

BOILERPLATE_SUBSTRINGS = (
    "comprehensive up-to-date news coverage",
    "aggregated from sources all over the world by google news",
    "please enable javascript",
    "enable js and disable any ad blocker",
    "access to this page has been denied",
)

WRAPPER_HOSTS = frozenset({"news.google.com", "consent.google.com"})


def is_boilerplate_description(text: str) -> bool:
    """Detect generic aggregator/bot-wall meta descriptions that are not real summaries."""
    low = (text or "").lower()
    return any(needle in low for needle in BOILERPLATE_SUBSTRINGS)


def parse_meta_description(html_text: str) -> str | None:
    """Extract only public og:description or <meta name='description'> content.

    Strictly never reads or persists article body text, and filters generic
    Google News / bot-wall boilerplate descriptions.
    """
    if not html_text:
        return None
    soup = BeautifulSoup(html_text, "lxml")

    for attrs in (
        {"property": "og:description"},
        {"name": "og:description"},
        {"name": "description"},
        {"name": "twitter:description"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag is not None:
            content = clean_text(tag.get("content"))
            if content and len(content) >= 15 and not is_boilerplate_description(content):
                return content
    return None


def _fetch_single_meta_status(
    url: str,
    timeout_sec: int = 5,
    retry_delay_sec: float = 2.0,
    http_client: httpx.Client | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> tuple[str | None, bool]:
    """Internal helper returning (description_or_none, hit_wrapper_host)."""
    own_client = http_client is None
    client = http_client or httpx.Client(follow_redirects=True, timeout=timeout_sec)

    try:
        for attempt in range(2):
            try:
                resp = client.get(url, headers=META_HEADERS, timeout=timeout_sec)
            except (httpx.TimeoutException, httpx.ConnectError, httpx.RemoteProtocolError) as exc:
                logger.debug("Meta fetch transient error (attempt %d) for %s: %s", attempt + 1, url, exc)
                if attempt == 0:
                    sleep_fn(retry_delay_sec)
                    continue
                return None, False
            except httpx.HTTPError as exc:
                logger.debug("Meta fetch HTTP error for %s: %s", url, exc)
                return None, False

            status = resp.status_code
            if status == 200:
                final_host = (resp.url.host or "").lower()
                if final_host in WRAPPER_HOSTS:
                    logger.debug("Meta fetch stayed on wrapper host %s; skipping", final_host)
                    return None, True
                return parse_meta_description(resp.text), False

            if status in TRANSIENT_STATUS_CODES:
                logger.debug("Meta fetch transient HTTP %d (attempt %d) for %s", status, attempt + 1, url)
                if attempt == 0:
                    sleep_fn(retry_delay_sec)
                    continue
                return None, False

            if status in DETERMINISTIC_STATUS_CODES or 400 <= status < 500:
                logger.debug("Meta fetch deterministic HTTP %d (no retry) for %s", status, url)
                return None, False

            return None, False
        return None, False
    finally:
        if own_client:
            client.close()


def fetch_single_meta(
    url: str,
    timeout_sec: int = 5,
    retry_delay_sec: float = 2.0,
    http_client: httpx.Client | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> str | None:
    """Fetch public meta description with strict transient vs deterministic error rules.

    - Transient errors (timeout, connect error, 5xx, 429): retry once after retry_delay_sec.
    - Deterministic errors (401/403/404/200 without meta): abort immediately, no retry.
    """
    desc, _ = _fetch_single_meta_status(
        url,
        timeout_sec=timeout_sec,
        retry_delay_sec=retry_delay_sec,
        http_client=http_client,
        sleep_fn=sleep_fn,
    )
    return desc


def enrich_reuters_meta(
    items: list[ArticleItem],
    limit: int = 20,
    rate_limit_sec: float = 1.0,
    timeout_sec: int = 5,
    retry_delay_sec: float = 2.0,
    wrapper_circuit_threshold: int = 3,
    http_client: httpx.Client | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> int:
    """Fetch og:description for newest Reuters items lacking summary_en.

    Respects rate limit (<= 1 req/sec), per-batch cap (default 20), and short-circuits
    after `wrapper_circuit_threshold` consecutive Google News wrapper-host responses
    so batches do not burn time on non-redirecting JS wrapper pages.
    """
    candidates = [
        item
        for item in items
        if item.source == "reuters" and not item.summary_en and item.url_display
    ]
    if not candidates or limit <= 0:
        return 0

    candidates.sort(key=lambda x: x.published_at or x.created_at, reverse=True)
    selected = candidates[:limit]

    enriched = 0
    consecutive_wrappers = 0
    own_client = http_client is None
    client = http_client or httpx.Client(follow_redirects=True, timeout=timeout_sec)

    try:
        for idx, item in enumerate(selected):
            if idx > 0 and rate_limit_sec > 0:
                sleep_fn(rate_limit_sec)
            desc, is_wrapper = _fetch_single_meta_status(
                item.url_display,
                timeout_sec=timeout_sec,
                retry_delay_sec=retry_delay_sec,
                http_client=client,
                sleep_fn=sleep_fn,
            )
            if desc:
                item.summary_en = desc
                enriched += 1
                consecutive_wrappers = 0
            elif is_wrapper:
                consecutive_wrappers += 1
                if wrapper_circuit_threshold > 0 and consecutive_wrappers >= wrapper_circuit_threshold:
                    logger.info(
                        "Meta fetch short-circuited after %d consecutive wrapper-host hits",
                        consecutive_wrappers,
                    )
                    break
            else:
                consecutive_wrappers = 0
    finally:
        if own_client:
            client.close()

    return enriched
