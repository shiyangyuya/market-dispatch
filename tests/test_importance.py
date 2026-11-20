"""Tests for 3-signal importance scoring engine (TC-IMP-*)."""

from __future__ import annotations

import httpx

from src.config import ImportanceConfig
from src.importance import fetch_gn_headlines, score_day_articles, tokenize_title
from src.models import ArticleItem


def _item(source: str, title: str, item_id: int) -> ArticleItem:
    return ArticleItem(
        id=item_id,
        source=source,
        title_en=title,
        url_display=f"https://example.com/{item_id}",
        published_at="2026-11-20T14:00:00+08:00",
        created_at="2026-11-20T14:05:00+08:00",
    )


def test_tc_imp_01_title_feature_regex_signal() -> None:
    cfg = ImportanceConfig()
    art = _item("reuters", "Exclusive: Regulators Probe Sovereign Bond Desk", 1)
    hyphenated = _item("wsj", "What to Know About Record-Breaking Tech IPO", 2)
    score_day_articles([art, hyphenated], cfg, gn_headlines={})
    assert art.is_important == 1
    assert art.important_reason == "标题特征词"
    # Hyphenated 'Record-Breaking' must NOT false-trigger 'breaking'
    assert hyphenated.is_important == 0


def test_tc_imp_02_cross_source_jaccard_signal_and_same_source_ignored() -> None:
    cfg = ImportanceConfig(cross_source_jaccard=0.55)
    bbg = _item("bloomberg", "Nvidia Shares Surge on Blowout Data Center Demand", 1)
    wsj = _item("wsj", "Nvidia Shares Surge on Data Center Chip Demand", 2)
    same_src1 = _item("bloomberg", "European Utilities Prepare Grid Upgrade Plan", 3)
    same_src2 = _item("bloomberg", "European Utilities Prepare Grid Upgrade Plan Today", 4)

    score_day_articles([bbg, wsj, same_src1, same_src2], cfg, gn_headlines={})
    assert bbg.is_important == 1 and "跨源报道" in (bbg.important_reason or "")
    assert wsj.is_important == 1 and "跨源报道" in (wsj.important_reason or "")
    # Same-source similar stories must NOT trigger cross-source signal
    assert same_src1.is_important == 0
    assert same_src2.is_important == 0


def test_tc_imp_03_gn_same_source_headline_signal() -> None:
    cfg = ImportanceConfig(gn_jaccard=0.50)
    rtr = _item("reuters", "Federal Reserve Officials Signal Slower Pace of Rate Cuts", 1)
    bbg = _item("bloomberg", "Federal Reserve Officials Signal Slower Pace of Rate Cuts", 2)
    gn_map = {
        "reuters": [tokenize_title("Federal Reserve Officials Signal Slower Pace of Rate Cuts")],
        "bloomberg": [],
        "wsj": [],
    }
    score_day_articles([rtr], cfg, gn_headlines=gn_map)
    assert rtr.is_important == 1
    assert rtr.important_reason == "GN头条"

    # Must match same media only
    score_day_articles([bbg], cfg, gn_headlines=gn_map)
    assert bbg.is_important == 0


def test_tc_imp_04_multi_signal_stacking() -> None:
    cfg = ImportanceConfig()
    a1 = _item("reuters", "Exclusive: China Chipmakers Expand Mature Node Capacity", 1)
    a2 = _item("wsj", "China Chipmakers Expand Mature Node Capacity Quickly", 2)
    score_day_articles([a1, a2], cfg, gn_headlines={})
    assert a1.is_important == 1
    assert "标题特征词" in (a1.important_reason or "")
    assert "跨源报道" in (a1.important_reason or "")


def test_tc_imp_05_ordinary_news_stays_unmarked() -> None:
    cfg = ImportanceConfig()
    art = _item("wsj", "Regional Freight Volumes Ease in October", 1)
    score_day_articles([art], cfg, gn_headlines={})
    assert art.is_important == 0
    assert art.important_reason is None


def test_tc_imp_06_gn_feed_failure_degrades_gracefully() -> None:
    def fail_handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timeout")

    with httpx.Client(transport=httpx.MockTransport(fail_handler)) as client:
        gn = fetch_gn_headlines(["https://news.google.com/rss"], http_client=client)

    assert gn == {"bloomberg": [], "reuters": [], "wsj": []}
