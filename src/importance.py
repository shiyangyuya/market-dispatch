"""Three-signal heuristic importance scoring engine (FR10)."""

from __future__ import annotations

import logging
import re

import feedparser
import httpx

from src.config import ImportanceConfig
from src.fetchers.base import DEFAULT_HEADERS, clean_text
from src.models import ArticleItem

logger = logging.getLogger(__name__)

STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "have", "he", "in", "is", "it", "its", "of", "on", "or", "over", "says",
    "say", "said", "she", "that", "the", "their", "this", "to", "was", "were",
    "will", "with", "after", "amid", "into", "new", "more", "than", "up", "out",
    "us", "u", "s", "about", "after", "before", "could", "would", "may", "report",
})

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_GN_SOURCE_MAP: tuple[tuple[str, str], ...] = (
    ("bloomberg", "bloomberg"),
    ("reuters", "reuters"),
    ("wall street journal", "wsj"),
    ("wsj", "wsj"),
)


def tokenize_title(title: str) -> set[str]:
    """Extract normalized non-stopword alphanumeric tokens from an English headline."""
    tokens = _TOKEN_RE.findall((title or "").lower())
    return {t for t in tokens if len(t) > 1 and t not in STOPWORDS}


def jaccard_similarity(tokens_a: set[str], tokens_b: set[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not tokens_a or not tokens_b:
        return 0.0
    union = tokens_a | tokens_b
    if not union:
        return 0.0
    return len(tokens_a & tokens_b) / len(union)


def _map_gn_source(source_text: str) -> str | None:
    low = (source_text or "").lower()
    for needle, canonical in _GN_SOURCE_MAP:
        if needle in low:
            return canonical
    return None


def fetch_gn_headlines(
    feed_urls: list[str],
    timeout_sec: int = 10,
    http_client: httpx.Client | None = None,
) -> dict[str, list[set[str]]]:
    """Fetch Google News Top Stories / Business headlines grouped by canonical source.

    Returns mapping: {'bloomberg': [token_set, ...], 'reuters': [...], 'wsj': [...]}.
    Never raises on network error (degrades gracefully to empty map).
    """
    headlines: dict[str, list[set[str]]] = {"bloomberg": [], "reuters": [], "wsj": []}
    if not feed_urls:
        return headlines

    own_client = http_client is None
    client = http_client or httpx.Client(follow_redirects=True, timeout=timeout_sec)

    try:
        for url in feed_urls:
            try:
                resp = client.get(url, headers=DEFAULT_HEADERS, timeout=timeout_sec)
                resp.raise_for_status()
                parsed = feedparser.parse(resp.text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to fetch GN signal feed %s: %s", url, exc)
                continue

            for entry in parsed.entries:
                src_obj = entry.get("source")
                src_title = ""
                if isinstance(src_obj, dict):
                    src_title = str(src_obj.get("title") or "")
                elif src_obj is not None:
                    src_title = str(getattr(src_obj, "title", "") or "")

                raw_title = clean_text(entry.get("title")) or ""
                canonical = _map_gn_source(src_title)
                if not canonical and " - " in raw_title:
                    canonical = _map_gn_source(raw_title.rsplit(" - ", 1)[-1])
                if not canonical:
                    continue

                # Strip trailing " - Media Name" before tokenizing
                title_part = raw_title.rsplit(" - ", 1)[0] if " - " in raw_title else raw_title
                toks = tokenize_title(title_part)
                if toks:
                    headlines[canonical].append(toks)
    finally:
        if own_client:
            client.close()

    return headlines


def score_day_articles(
    articles: list[ArticleItem],
    cfg: ImportanceConfig,
    gn_headlines: dict[str, list[set[str]]] | None = None,
) -> list[tuple[ArticleItem, int, str | None]]:
    """Compute 3-signal importance score for all articles of a single day.

    Signals:
      1. Title feature regex (breaking/exclusive/urgent/just in): +2 -> '标题特征词'
      2. Cross-source same-day title Jaccard >= threshold:       +2 -> '跨源报道'
      3. GN Top/Business same-source headline Jaccard >= thresh: +3 -> 'GN头条'

    Total score >= cfg.score_threshold (default 2) => is_important = 1.
    """
    title_pattern = re.compile(cfg.title_regex)
    gn_map = gn_headlines or {}
    token_cache = [tokenize_title(a.title_en) for a in articles]

    results: list[tuple[ArticleItem, int, str | None]] = []

    for i, art in enumerate(articles):
        score = 0
        reasons: list[str] = []
        toks_i = token_cache[i]

        # Signal 1: Title feature keywords
        if title_pattern.search(art.title_en or ""):
            score += 2
            reasons.append("标题特征词")

        # Signal 2: Cross-source event match within the same day
        if toks_i:
            for j, other in enumerate(articles):
                if i == j or other.source == art.source:
                    continue
                if jaccard_similarity(toks_i, token_cache[j]) >= cfg.cross_source_jaccard:
                    score += 2
                    reasons.append("跨源报道")
                    break

        # Signal 3: Google News Top Stories / Business same-media headline match
        src_gn = gn_map.get(art.source, [])
        if toks_i and src_gn:
            for gn_toks in src_gn:
                if jaccard_similarity(toks_i, gn_toks) >= cfg.gn_jaccard:
                    score += 3
                    reasons.append("GN头条")
                    break

        is_imp = 1 if score >= cfg.score_threshold else 0
        reason_str = " · ".join(reasons) if is_imp and reasons else None
        art.is_important = is_imp
        art.important_reason = reason_str
        results.append((art, is_imp, reason_str))

    return results
