"""Tests for polite Reuters og:description fallback fetcher (TC-META-*)."""

from __future__ import annotations

import httpx

from src.fetch_meta import enrich_reuters_meta, fetch_single_meta, parse_meta_description
from src.models import ArticleItem


def test_tc_meta_01_extracts_og_description_only() -> None:
    html = """
    <html><head>
      <meta property="og:description" content="Global bond yields slid after softer industrial data.">
    </head><body><p>Full body text that must never be scraped.</p></body></html>
    """
    assert parse_meta_description(html) == "Global bond yields slid after softer industrial data."


def test_tc_meta_02_fallback_to_meta_name_description() -> None:
    html = """
    <html><head>
      <meta name="description" content="Central bank officials signaled patience on monetary easing.">
    </head></html>
    """
    assert parse_meta_description(html) == "Central bank officials signaled patience on monetary easing."


def test_tc_meta_03_transient_error_retries_once() -> None:
    calls = 0
    slept: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, text="Too Many Requests")
        return httpx.Response(
            200,
            text='<meta property="og:description" content="Recovered after transient rate limit error.">',
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        desc = fetch_single_meta(
            "https://example.com/news/1",
            retry_delay_sec=2.0,
            http_client=client,
            sleep_fn=slept.append,
        )

    assert calls == 2
    assert slept == [2.0]
    assert desc == "Recovered after transient rate limit error."


def test_tc_meta_04_deterministic_error_never_retries() -> None:
    for status_code in (401, 403, 404):
        calls = 0

        def handler(req: httpx.Request, code: int = status_code) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(code, text="Blocked")

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            desc = fetch_single_meta(
                "https://example.com/blocked",
                http_client=client,
                sleep_fn=lambda _: None,
            )

        assert calls == 1
        assert desc is None


def test_tc_meta_05_caps_batch_at_20_newest_and_paces() -> None:
    items = [
        ArticleItem(
            source="reuters",
            title_en=f"Story {i}",
            url_display=f"https://example.com/r/{i}",
            published_at=f"2026-11-20T{i:02d}:00:00+08:00" if i < 24 else "2026-11-20T23:59:00+08:00",
            created_at="2026-11-20T23:59:00+08:00",
        )
        for i in range(25)
    ]

    requested: list[str] = []
    sleeps: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requested.append(str(req.url))
        return httpx.Response(
            200,
            text='<meta property="og:description" content="Valid meta description for testing batch cap.">',
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        enriched = enrich_reuters_meta(
            items,
            limit=20,
            rate_limit_sec=1.0,
            http_client=client,
            sleep_fn=sleeps.append,
        )

    assert enriched == 20
    assert len(requested) == 20
    assert len(sleeps) == 19  # Paced between consecutive requests
    assert "https://example.com/r/24" in requested  # Newest prioritized
    assert "https://example.com/r/0" not in requested  # Oldest skipped


def test_tc_meta_06_rejects_google_news_wrapper_and_boilerplate() -> None:
    gn_boilerplate_html = (
        '<meta property="og:description" '
        'content="Comprehensive up-to-date news coverage, aggregated from sources all over the world by Google News.">'
    )
    assert parse_meta_description(gn_boilerplate_html) is None

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text='<meta property="og:description" content="Valid looking text but stayed on Google News wrapper.">',
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        desc = fetch_single_meta(
            "https://news.google.com/rss/articles/CBMiExample",
            http_client=client,
            sleep_fn=lambda _: None,
        )
    assert desc is None

    # Circuit-breaker: 10 wrapper URLs stop after 3 consecutive wrapper hits
    calls = 0

    def count_handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, text=gn_boilerplate_html)

    items = [
        ArticleItem(
            source="reuters",
            title_en=f"R {i}",
            url_display=f"https://news.google.com/rss/articles/tok{i}",
            published_at=f"2026-11-20T1{i}:00:00+08:00",
            created_at="2026-11-20T18:00:00+08:00",
        )
        for i in range(10)
    ]
    with httpx.Client(transport=httpx.MockTransport(count_handler)) as client:
        enriched = enrich_reuters_meta(items, rate_limit_sec=0, http_client=client, sleep_fn=lambda _: None)
    assert enriched == 0
    assert calls == 3
