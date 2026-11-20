"""Tests for 5-category keyword classifier and priority chain (TC-CLS-*)."""

from __future__ import annotations

from src.classifier import TopicClassifier
from src.config import AppConfig


def test_tc_cls_01_china_overrides_channel(tmp_config: AppConfig) -> None:
    cls = TopicClassifier(tmp_config.keywords)
    assert cls.classify("Beijing Signals Fresh Fiscal Support for Yuan", initial_category="markets") == "china"


def test_tc_cls_02_china_beats_us_on_dual_hit(tmp_config: AppConfig) -> None:
    cls = TopicClassifier(tmp_config.keywords)
    assert cls.classify("Trump Weighs New Tariffs on China Imports", initial_category="world") == "china"


def test_tc_cls_03_us_domestic_politics_override(tmp_config: AppConfig) -> None:
    cls = TopicClassifier(tmp_config.keywords)
    assert cls.classify("Senate Reaches Bipartisan Spending Deal at Capitol Hill", initial_category="world") == "us"
    assert cls.classify("White House Proposes New Pentagon Budget", initial_category="markets") == "us"


def test_tc_cls_04_us_finance_stays_in_markets(tmp_config: AppConfig) -> None:
    cls = TopicClassifier(tmp_config.keywords)
    # Fed & Wall Street must NOT be hijacked into 'us'
    assert cls.classify("Fed Holds Rates Steady as Wall Street Stocks Rally", initial_category="markets") == "markets"
    assert cls.classify("Fed Holds Rates Steady as Wall Street Stocks Rally", initial_category=None) == "markets"


def test_tc_cls_05_word_boundary_prevents_substring_false_positives(tmp_config: AppConfig) -> None:
    cls = TopicClassifier(tmp_config.keywords)
    # 'said' contains 'ai'; 'exit' contains 'xi'; must NOT match tech or china
    assert cls.classify("European Envoy Said Talks Will Exit Deadlock Next Month", initial_category=None) == "world"


def test_tc_cls_06_reuters_keyword_only_and_world_fallback(tmp_config: AppConfig) -> None:
    cls = TopicClassifier(tmp_config.keywords)
    assert cls.classify("Nvidia Unveils Next-Gen Semiconductor Chip Architecture", initial_category=None) == "tech"
    assert cls.classify("UN Mediators Meet in Geneva Over Maritime Shipping Treaty", initial_category=None) == "world"
