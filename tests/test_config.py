"""Tests for configuration loading, validation, and time utilities (TC-CFG-*, TC-TIME-*)."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.config import load_config
from src.time_utils import effective_day, format_dual_time, parse_rss_datetime


def test_tc_cfg_01_load_default_example_config(tmp_workspace: Path) -> None:
    cfg = load_config(base_dir=tmp_workspace)
    assert cfg.storage.engine == "sqlite"
    assert cfg.retention_days == 3
    assert cfg.timezone == "Asia/Shanghai"
    assert cfg.sources.wsj_base_url == "https://feeds.content.dowjones.io/public/rss"
    assert set(cfg.sources.bloomberg_feeds.keys()) == {"markets", "economics", "technology", "politics"}
    assert set(cfg.sources.wsj_feeds.keys()) == {"RSSMarketsMain", "RSSWSJD", "RSSWorldNews"}


def test_tc_cfg_02_env_overrides(tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MD_LLM_API_KEY", "sk-secret-from-env")
    monkeypatch.setenv("MD_MYSQL_PASSWORD", "db-pwd-123")
    monkeypatch.setenv("MD_TRANSLATOR_ENGINE", "llm")

    cfg = load_config(base_dir=tmp_workspace)
    assert cfg.translator.engine == "llm"
    assert cfg.translator.llm_api_key == "sk-secret-from-env"
    assert cfg.storage.mysql_password == "db-pwd-123"


def test_tc_cfg_03_invalid_config_raises(tmp_workspace: Path) -> None:
    bad_cfg = tmp_workspace / "config" / "bad.yaml"
    bad_cfg.write_text("storage:\n  engine: postgres\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid storage.engine"):
        load_config(config_path=bad_cfg, base_dir=tmp_workspace)

    bad_ret = tmp_workspace / "config" / "bad_ret.yaml"
    bad_ret.write_text("retention_days: 0\n", encoding="utf-8")
    with pytest.raises(ValueError, match="retention_days"):
        load_config(config_path=bad_ret, base_dir=tmp_workspace)


def test_tc_time_01_rfc2822_to_shanghai() -> None:
    iso = parse_rss_datetime("Thu, 19 Nov 2026 16:30:00 GMT", tz_name="Asia/Shanghai")
    assert iso == "2026-11-20T00:30:00+08:00"
    assert effective_day(iso, "2026-11-20T01:00:00+08:00") == "2026-11-20"


def test_tc_time_02_missing_published_falls_back_to_created() -> None:
    day = effective_day(None, "2026-11-20T10:15:00+08:00")
    assert day == "2026-11-20"
    pub_disp, crt_disp, is_cross = format_dual_time(None, "2026-11-20T10:15:00+08:00", "2026-11-20")
    assert pub_disp == "—"
    assert crt_disp == "10:15"
    assert is_cross is False


def test_tc_time_03_same_day_vs_cross_day_dual_time() -> None:
    p1, c1, cross1 = format_dual_time(
        "2026-11-20T14:26:00+08:00",
        "2026-11-20T15:02:00+08:00",
        "2026-11-20",
    )
    assert (p1, c1, cross1) == ("14:26", "15:02", False)

    p2, c2, cross2 = format_dual_time(
        "2026-11-19T23:42:00+08:00",
        "2026-11-20T00:05:00+08:00",
        "2026-11-19",
    )
    assert (p2, c2, cross2) == ("11-19 23:42", "11-20 00:05", True)
