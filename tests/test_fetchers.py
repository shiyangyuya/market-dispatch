"""Tests for Bloomberg, WSJ, and Reuters Google News RSS fetchers (TC-FETCH-*)."""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from src.fetchers import BloombergFetcher, ReutersGoogleNewsFetcher, WSJFetcher

REF_NOW = datetime(2026, 11, 20, 18, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))

BLOOMBERG_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Bloomberg Markets</title>
  <item>
    <title>Fed Signals &lt;b&gt;Slower&lt;/b&gt; Cuts</title>
    <link>https://www.bloomberg.com/news/articles/2026-11-20/fed-cuts</link>
    <description>&lt;p&gt;Officials see cooler inflation.&lt;/p&gt;</description>
    <pubDate>Fri, 20 Nov 2026 06:26:00 GMT</pubDate>
  </item>
  <item>
    <title>Old Story From Two Days Ago</title>
    <link>https://www.bloomberg.com/news/articles/2026-11-18/old-story</link>
    <description>Too old for 24h lookback.</description>
    <pubDate>Wed, 18 Nov 2026 05:00:00 GMT</pubDate>
  </item>
</channel></rss>"""

WSJ_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>WSJ Markets</title>
  <item>
    <title>Oil Prices Steady as Traders Weigh Risks</title>
    <link>https://www.wsj.com/articles/oil-steady?mod=rss_markets_main</link>
    <description>Brent crude little changed on Friday.</description>
    <pubDate>Fri, 20 Nov 2026 05:05:00 GMT</pubDate>
  </item>
</channel></rss>"""

REUTERS_GN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Google News Reuters</title>
  <item>
    <title>Exclusive: Chinese Chipmakers Expand Capacity - Reuters</title>
    <link>https://news.google.com/rss/articles/CBMiExampleToken?oc=5</link>
    <description>&lt;a href="https://news.google.com/..."&gt;Exclusive...&lt;/a&gt;</description>
    <pubDate>Fri, 20 Nov 2026 05:48:00 GMT</pubDate>
  </item>
</channel></rss>"""


def test_tc_fetch_01_and_04_bloomberg_rss_and_lookback() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=BLOOMBERG_XML))
    with httpx.Client(transport=transport) as client:
        fetcher = BloombergFetcher(
            base_url="https://www.bloomberg.com/feeds",
            feeds={"markets": "markets"},
            lookback_hours=24,
            http_client=client,
        )
        res = fetcher.fetch(ref_now=REF_NOW)

    assert res.ok is True
    assert res.fetched == 1  # Old 48h item filtered out
    item = res.items[0]
    assert item.title_en == "Fed Signals Slower Cuts"
    assert item.summary_en == "Officials see cooler inflation."
    assert item.published_at == "2026-11-20T14:26:00+08:00"
    assert item.created_at == "2026-11-20T18:00:00+08:00"
    assert item.feed_topic == "markets"


def test_tc_fetch_02_wsj_new_rss() -> None:
    requested_urls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        requested_urls.append(str(req.url))
        return httpx.Response(200, text=WSJ_XML)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fetcher = WSJFetcher(
            base_url="https://feeds.content.dowjones.io/public/rss",
            feeds={"RSSMarketsMain": "markets"},
            lookback_hours=24,
            http_client=client,
        )
        res = fetcher.fetch(ref_now=REF_NOW)

    assert requested_urls == ["https://feeds.content.dowjones.io/public/rss/RSSMarketsMain"]
    assert res.ok is True
    assert res.fetched == 1
    assert res.items[0].published_at == "2026-11-20T13:05:00+08:00"
    assert res.items[0].summary_en == "Brent crude little changed on Friday."


def test_tc_fetch_03_reuters_gn_strips_suffix() -> None:
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=REUTERS_GN_XML))
    with httpx.Client(transport=transport) as client:
        fetcher = ReutersGoogleNewsFetcher(
            url="https://news.google.com/rss/search?q=site:reuters.com",
            lookback_hours=24,
            http_client=client,
        )
        res = fetcher.fetch(ref_now=REF_NOW)

    assert res.ok is True
    assert res.fetched == 1
    item = res.items[0]
    assert item.title_en == "Exclusive: Chinese Chipmakers Expand Capacity"
    assert item.summary_en is None
    assert item.published_at == "2026-11-20T13:48:00+08:00"
    assert "news.google.com" in item.url_display


def test_tc_fetch_05_partial_channel_failure_isolated() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if "technology" in str(req.url):
            return httpx.Response(503, text="Service Unavailable")
        return httpx.Response(200, text=BLOOMBERG_XML)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        fetcher = BloombergFetcher(
            base_url="https://www.bloomberg.com/feeds",
            feeds={"markets": "markets", "technology": "tech"},
            lookback_hours=24,
            http_client=client,
        )
        res = fetcher.fetch(ref_now=REF_NOW)

    assert res.ok is True
    assert res.fetched == 1
    assert len(res.errors) == 1
    assert "technology" in res.errors[0]
