"""Config-driven 5-category topic classifier with word-boundary matching."""

from __future__ import annotations

import re

from src.models import CATEGORY_KEYS


def _compile_keyword_patterns(keywords: list[str]) -> list[re.Pattern[str]]:
    """Compile case-insensitive regex patterns with word boundaries for ASCII terms.

    Word boundaries prevent short keywords like 'ai' or 'xi' from falsely
    matching substrings inside unrelated words like 'said' or 'exit'.
    """
    patterns: list[re.Pattern[str]] = []
    for raw in keywords:
        term = raw.strip()
        if not term:
            continue
        escaped = re.escape(term)
        # Apply word boundaries at ASCII alphanumeric edges
        prefix = r"\b" if term[0].isascii() and term[0].isalnum() else ""
        suffix = r"\b" if term[-1].isascii() and term[-1].isalnum() else ""
        patterns.append(re.compile(f"{prefix}{escaped}{suffix}", re.IGNORECASE))
    return patterns


class TopicClassifier:
    """Classifies news items into markets / tech / world / us / china.

    Priority chain:
      1. china keywords (override)
      2. us keywords (override, domestic US politics/policy only)
      3. feed channel initial category (for Bloomberg / WSJ)
         OR keyword match (tech -> markets) for channel-less sources (Reuters)
      4. world fallback
    """

    def __init__(self, keywords_config: dict[str, list[str]]) -> None:
        self._patterns: dict[str, list[re.Pattern[str]]] = {
            cat: _compile_keyword_patterns(keywords_config.get(cat, []))
            for cat in ("china", "us", "tech", "markets")
        }

    def _matches(self, category: str, text: str) -> bool:
        return any(p.search(text) for p in self._patterns.get(category, ()))

    def classify(
        self,
        title_en: str,
        summary_en: str | None = None,
        initial_category: str | None = None,
    ) -> str:
        """Classify a single news item according to the priority chain."""
        combined = f"{title_en or ''} {summary_en or ''}".strip()

        # 1. Highest priority override: China
        if self._matches("china", combined):
            return "china"

        # 2. Second priority override: US domestic politics/policy
        if self._matches("us", combined):
            return "us"

        # 3. Initial channel mapping when provided (Bloomberg / WSJ)
        if initial_category and initial_category in CATEGORY_KEYS:
            return initial_category

        # 4. Keyword match for sources without channels (Reuters)
        if self._matches("tech", combined):
            return "tech"
        if self._matches("markets", combined):
            return "markets"

        # 5. Fallback
        return "world"
