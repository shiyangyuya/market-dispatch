"""Tests for Google gtx & OpenAI-compatible LLM translators (TC-TR-*)."""

from __future__ import annotations

import json

import httpx

from src.db import SQLiteDatabase
from src.models import ArticleItem
from src.translator import (
    GoogleGtxTranslator,
    OpenAICompatLLMTranslator,
    translate_articles,
)


def test_tc_tr_01_google_gtx_multi_segment_parse() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        q = req.url.params.get("q", "")
        if "Fed" in q:
            return httpx.Response(200, json=[[["美联储暗示", "Fed signals"], ["放缓降息", "slower cuts"]]])
        return httpx.Response(200, json=[[["通胀回落。", "Inflation cools."]]])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        tx = GoogleGtxTranslator(pacing_ms=0, http_client=client, sleep_fn=lambda _: None)
        t_zh, s_zh, ok = tx.translate_item("Fed signals slower cuts", "Inflation cools.")

    assert ok is True
    assert t_zh == "美联储暗示放缓降息"
    assert s_zh == "通胀回落。"


def test_tc_tr_02_google_gtx_retry_then_degrade() -> None:
    calls = 0
    slept: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, text="Rate limited")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        tx = GoogleGtxTranslator(pacing_ms=0, retry_delay_sec=2.0, http_client=client, sleep_fn=slept.append)
        t_zh, s_zh, ok = tx.translate_item("Bond yields fall", "Details here")

    assert ok is False
    assert (t_zh, s_zh) == (None, None)
    assert calls == 2
    assert slept == [2.0]


def test_tc_tr_03_summary_truncated_to_1500_chars() -> None:
    seen_q: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        q = req.url.params.get("q", "")
        seen_q.append(q)
        return httpx.Response(200, json=[[["译文", q]]])

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        tx = GoogleGtxTranslator(pacing_ms=0, max_summary_chars=1500, http_client=client, sleep_fn=lambda _: None)
        tx.translate_item("Title", "A" * 2500)

    assert len(seen_q) == 2
    assert len(seen_q[1]) == 1500


def test_tc_tr_04_llm_translator_parses_fenced_json() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path.endswith("/chat/completions")
        reply = '```json\n{"title_zh": "英伟达业绩超预期", "summary_zh": "数据中心收入大增。"}\n```'
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": reply}}]},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        tx = OpenAICompatLLMTranslator(
            api_base="https://open.bigmodel.cn/api/paas/v4",
            model="glm-4-flash",
            api_key="test-key",
            http_client=client,
            sleep_fn=lambda _: None,
        )
        t_zh, s_zh, ok = tx.translate_item("Nvidia Beats Estimates", "Data-center revenue jumped.")

    assert ok is True
    assert t_zh == "英伟达业绩超预期"
    assert s_zh == "数据中心收入大增。"


def test_tc_tr_05_skips_already_translated_items(sqlite_db: SQLiteDatabase) -> None:
    calls = 0

    def handler(req: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=[[["新标题", "New title"]]])

    cached = ArticleItem(
        guid="g1",
        source="bloomberg",
        category="markets",
        title_en="Already Translated",
        title_zh="已翻译标题",
        url_display="https://example.com/1",
        published_at="2026-11-20T10:00:00+08:00",
        created_at="2026-11-20T10:05:00+08:00",
        translated=1,
        title_norm_hash="1" * 32,
    )
    fresh = ArticleItem(
        guid="g2",
        source="wsj",
        category="tech",
        title_en="Needs Translation",
        url_display="https://example.com/2",
        published_at="2026-11-20T11:00:00+08:00",
        created_at="2026-11-20T11:05:00+08:00",
        translated=0,
        title_norm_hash="2" * 32,
    )
    sqlite_db.insert_article(cached)
    sqlite_db.insert_article(fresh)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        tx = GoogleGtxTranslator(pacing_ms=0, http_client=client, sleep_fn=lambda _: None)
        ok_cnt, fail_cnt = translate_articles([cached, fresh], tx, db=sqlite_db)

    assert calls == 1
    assert ok_cnt == 1 and fail_cnt == 0
    assert fresh.title_zh == "新标题"
    assert fresh.translated == 1
