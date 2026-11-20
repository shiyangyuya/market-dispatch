"""URL canonicalization, title normalization, hashing, and L1/L2 deduplication."""

from __future__ import annotations

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.models import ArticleItem

# Tracking query parameters stripped during URL canonicalization
TRACKING_PARAMS = frozenset({
    "mod",
    "ref",
    "fbclid",
    "gclid",
    "oc",
    "srnd",
    "sref",
    "leadsource",
    "cmpid",
    "ns_mchannel",
    "ns_source",
    "ns_campaign",
    "ns_linkname",
    "ns_fee",
})

_NON_ALNUM_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


def canonicalize_url(url: str) -> str:
    """Normalize a URL by lowercasing scheme/host, stripping tracking params & fragment."""
    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    scheme = (parts.scheme or "https").lower()
    netloc = parts.netloc.lower()
    path = parts.path.rstrip("/") or "/"

    kept_params = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=False)
        if not k.lower().startswith("utm_") and k.lower() not in TRACKING_PARAMS
    ]
    kept_params.sort()
    query = urlencode(kept_params)

    return urlunsplit((scheme, netloc, path, query, ""))


def normalize_title(title: str) -> str:
    """Lowercase, strip punctuation, and collapse whitespace for L2 title matching."""
    cleaned = _NON_ALNUM_RE.sub(" ", (title or "").lower())
    return _WHITESPACE_RE.sub(" ", cleaned).strip()


def title_md5(title: str) -> str:
    """Compute 32-char lowercase MD5 hash of the normalized title."""
    norm = normalize_title(title)
    return hashlib.md5(norm.encode("utf-8")).hexdigest()


def build_guid(source: str, url: str, title_hash: str) -> str:
    """Build deterministic unique guid key (L1 dedup).

    - Bloomberg / WSJ: canonicalized real URL (truncated safely if >191? No,
      SQLite TEXT has no limit; for MySQL VARCHAR(191) safety, if canonical URL
      exceeds 191 chars, fall back to '<source>:url:<md5(canonical_url)>').
    - Reuters: 'reuters:<title_norm_hash>' because Google News redirect URLs rotate.
    """
    src = source.lower()
    if src == "reuters":
        return f"reuters:{title_hash}"

    canon = canonicalize_url(url)
    if not canon:
        return f"{src}:{title_hash}"
    if len(canon) > 191:
        url_hash = hashlib.md5(canon.encode("utf-8")).hexdigest()
        return f"{src}:url:{url_hash}"
    return canon


def populate_dedup_keys(item: ArticleItem) -> ArticleItem:
    """Populate title_norm_hash and guid on an ArticleItem in place."""
    if not item.title_norm_hash:
        item.title_norm_hash = title_md5(item.title_en)
    if not item.guid:
        item.guid = build_guid(item.source, item.url_display, item.title_norm_hash)
    return item


def filter_duplicates(
    items: list[ArticleItem],
    db: object,
) -> tuple[list[ArticleItem], int]:
    """Deduplicate incoming items against DB and within the current batch.

    Enforces:
    - L1: unique `guid`
    - L2: same `(source, title_norm_hash, effective_day)`
    - Cross-source items are NEVER merged.
    """
    new_items: list[ArticleItem] = []
    dup_count = 0

    seen_guids: set[str] = set()
    seen_l2: set[tuple[str, str, str]] = set()

    for item in items:
        populate_dedup_keys(item)
        l2_key = (item.source, item.title_norm_hash, item.effective_day)

        if item.guid in seen_guids or l2_key in seen_l2:
            dup_count += 1
            continue

        if db.exists_by_guid(item.guid) or db.exists_by_title_hash(
            item.source, item.title_norm_hash, item.effective_day
        ):
            dup_count += 1
            seen_guids.add(item.guid)
            seen_l2.add(l2_key)
            continue

        seen_guids.add(item.guid)
        seen_l2.add(l2_key)
        new_items.append(item)

    return new_items, dup_count
