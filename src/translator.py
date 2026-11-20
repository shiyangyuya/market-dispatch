"""Pluggable EN->ZH-CN translators (Google gtx free default + OpenAI-compatible LLM)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import re
import threading
import time
from typing import Callable

import httpx

from src.config import TranslatorConfig
from src.models import ArticleItem

logger = logging.getLogger(__name__)

GOOGLE_GTX_URL = "https://translate.googleapis.com/translate_a/single"

LLM_SYSTEM_PROMPT = (
    "你是一名资深中文财经新闻编辑，熟悉 Bloomberg、Reuters、WSJ 报道风格。"
    "请将给定的英文财经新闻标题和摘要准确翻译为简洁、专业的简体中文，"
    "保持财经术语规范（如 Fed→美联储、Treasury yields→美债收益率、basis points→基点）。"
    '只输出严格 JSON：{"title_zh": "...", "summary_zh": "..."}；'
    "若摘要为空，summary_zh 输出 null。不要输出任何额外解释或标记。"
)

_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)


class BaseTranslator(ABC):
    """Abstract translator returning (title_zh, summary_zh, success)."""

    @abstractmethod
    def translate_item(self, title_en: str, summary_en: str | None) -> tuple[str | None, str | None, bool]: ...

    def __enter__(self) -> BaseTranslator:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def close(self) -> None:
        """Release any held HTTP connection resources."""


class GoogleGtxTranslator(BaseTranslator):
    """Free Google translate_a/single (client=gtx) engine with retry, pacing & keep-alive."""

    def __init__(
        self,
        timeout_sec: int = 10,
        pacing_ms: int = 300,
        max_summary_chars: int = 1500,
        retry_delay_sec: float = 2.0,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout_sec = timeout_sec
        self.pacing_sec = max(0.0, pacing_ms / 1000.0)
        self.max_summary_chars = max_summary_chars
        self.retry_delay_sec = retry_delay_sec
        self._external_client = http_client
        self._owned_client: httpx.Client | None = None
        self._lock = threading.Lock()
        self._sleep = sleep_fn
        self._request_count = 0

    def _get_client(self) -> httpx.Client:
        if self._external_client is not None:
            return self._external_client
        with self._lock:
            if self._owned_client is None:
                limits = httpx.Limits(max_connections=8, max_keepalive_connections=8)
                self._owned_client = httpx.Client(
                    follow_redirects=True, timeout=self.timeout_sec, limits=limits
                )
            return self._owned_client

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()
            self._owned_client = None

    def _translate_text(self, text: str, client: httpx.Client) -> str | None:
        cleaned = (text or "").strip()
        if not cleaned:
            return None

        with self._lock:
            req_idx = self._request_count
            self._request_count += 1
        if req_idx > 0 and self.pacing_sec > 0:
            self._sleep(self.pacing_sec)

        params = {
            "client": "gtx",
            "sl": "en",
            "tl": "zh-CN",
            "dt": "t",
            "q": cleaned,
        }

        for attempt in range(2):
            try:
                resp = client.get(GOOGLE_GTX_URL, params=params, timeout=self.timeout_sec)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list) and data and isinstance(data[0], list):
                        parts = [
                            str(seg[0])
                            for seg in data[0]
                            if isinstance(seg, list) and seg and seg[0] is not None
                        ]
                        joined = "".join(parts).strip()
                        if joined:
                            return joined
                logger.warning(
                    "Google translate unexpected status=%s (attempt %d)",
                    resp.status_code,
                    attempt + 1,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Google translate error (attempt %d): %s", attempt + 1, exc)

            if attempt == 0:
                self._sleep(self.retry_delay_sec)

        return None

    def translate_item(self, title_en: str, summary_en: str | None) -> tuple[str | None, str | None, bool]:
        client = self._get_client()
        title_zh = self._translate_text(title_en, client)
        if not title_zh:
            return None, None, False

        summary_zh: str | None = None
        if summary_en and summary_en.strip():
            truncated = summary_en.strip()[: self.max_summary_chars]
            summary_zh = self._translate_text(truncated, client)

        return title_zh, summary_zh, True


class OpenAICompatLLMTranslator(BaseTranslator):
    """OpenAI-compatible /chat/completions translator (e.g., Zhipu GLM, DeepSeek, OpenAI)."""

    def __init__(
        self,
        api_base: str,
        model: str,
        api_key: str,
        timeout_sec: int = 20,
        max_summary_chars: int = 1500,
        retry_delay_sec: float = 2.0,
        http_client: httpx.Client | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.api_base = api_base.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout_sec = timeout_sec
        self.max_summary_chars = max_summary_chars
        self.retry_delay_sec = retry_delay_sec
        self._external_client = http_client
        self._owned_client: httpx.Client | None = None
        self._sleep = sleep_fn

    def _get_client(self) -> httpx.Client:
        if self._external_client is not None:
            return self._external_client
        if self._owned_client is None:
            self._owned_client = httpx.Client(follow_redirects=True, timeout=self.timeout_sec)
        return self._owned_client

    def close(self) -> None:
        if self._owned_client is not None:
            self._owned_client.close()
            self._owned_client = None

    def _parse_json_reply(self, content: str) -> tuple[str | None, str | None]:
        cleaned = _CODE_FENCE_RE.sub("", (content or "").strip()).strip()
        # Extract JSON object even if model wrapped extra prose
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end > start:
            cleaned = cleaned[start : end + 1]
        data = json.loads(cleaned)
        title_zh = (data.get("title_zh") or "").strip() or None
        raw_sum = data.get("summary_zh")
        summary_zh = (str(raw_sum).strip() or None) if raw_sum is not None else None
        return title_zh, summary_zh

    def translate_item(self, title_en: str, summary_en: str | None) -> tuple[str | None, str | None, bool]:
        if not self.api_base or not self.model or not self.api_key:
            logger.error("LLM translator missing api_base/model/api_key configuration")
            return None, None, False

        truncated_sum = (summary_en or "").strip()[: self.max_summary_chars] or None
        user_payload = json.dumps(
            {"title_en": title_en, "summary_en": truncated_sum},
            ensure_ascii=False,
        )
        url = f"{self.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": LLM_SYSTEM_PROMPT},
                {"role": "user", "content": user_payload},
            ],
        }

        client = self._get_client()
        for attempt in range(2):
            try:
                resp = client.post(url, headers=headers, json=body, timeout=self.timeout_sec)
                resp.raise_for_status()
                payload = resp.json()
                content = payload["choices"][0]["message"]["content"]
                title_zh, summary_zh = self._parse_json_reply(content)
                if title_zh:
                    return title_zh, summary_zh, True
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM translate error (attempt %d): %s", attempt + 1, exc)
            if attempt == 0:
                self._sleep(self.retry_delay_sec)
        return None, None, False


def create_translator(
    cfg: TranslatorConfig,
    http_client: httpx.Client | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> BaseTranslator:
    """Create configured Google gtx or OpenAI-compatible LLM translator."""
    if cfg.engine == "llm":
        return OpenAICompatLLMTranslator(
            api_base=cfg.llm_api_base,
            model=cfg.llm_model,
            api_key=cfg.llm_api_key,
            timeout_sec=max(cfg.timeout_sec, 15),
            max_summary_chars=cfg.max_summary_chars,
            http_client=http_client,
            sleep_fn=sleep_fn,
        )
    return GoogleGtxTranslator(
        timeout_sec=cfg.timeout_sec,
        pacing_ms=cfg.pacing_ms,
        max_summary_chars=cfg.max_summary_chars,
        http_client=http_client,
        sleep_fn=sleep_fn,
    )


def translate_articles(
    items: list[ArticleItem],
    translator: BaseTranslator,
    db: object | None = None,
    max_workers: int = 4,
) -> tuple[int, int]:
    """Translate untranslated articles with optional bounded concurrency & DB writes on main thread.

    Skips items that already have translated == 1 (cache hit).
    Returns (ok_count, fail_count).
    """
    pending = [it for it in items if not (it.translated == 1 and it.title_zh)]
    if not pending:
        return 0, 0

    ok_count = 0
    fail_count = 0

    # Keep single-threaded when small batch or injected mock client
    use_workers = (
        1
        if max_workers <= 1 or len(pending) <= 2 or getattr(translator, "_external_client", None) is not None
        else min(max_workers, len(pending))
    )

    try:
        if use_workers == 1:
            results = [
                (item, *translator.translate_item(item.title_en, item.summary_en))
                for item in pending
            ]
        else:
            results = []
            with ThreadPoolExecutor(max_workers=use_workers) as pool:
                fut_map = {
                    pool.submit(translator.translate_item, item.title_en, item.summary_en): item
                    for item in pending
                }
                for fut in as_completed(fut_map):
                    item = fut_map[fut]
                    try:
                        t_zh, s_zh, ok = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Translation worker error: %s", exc)
                        t_zh, s_zh, ok = None, None, False
                    results.append((item, t_zh, s_zh, ok))

        # Apply and persist results sequentially on the main thread (SQLite thread-safe)
        for item, title_zh, summary_zh, ok in results:
            if ok and title_zh:
                item.title_zh = title_zh
                item.summary_zh = summary_zh
                item.translated = 1
                ok_count += 1
            else:
                item.translated = 2
                fail_count += 1

            if db is not None and item.id is not None:
                db.update_translation(item.id, item.title_zh, item.summary_zh, item.translated)
    finally:
        translator.close()

    return ok_count, fail_count
