"""Configuration loading, validation, and environment-variable overrides."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "config.yaml"
EXAMPLE_CONFIG_PATH = PROJECT_ROOT / "config" / "config.example.yaml"


@dataclass
class StorageConfig:
    engine: str = "sqlite"
    sqlite_path: Path = field(default_factory=lambda: PROJECT_ROOT / "data" / "news.db")
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = ""
    mysql_database: str = "market_dispatch"


@dataclass
class TranslatorConfig:
    engine: str = "google"
    timeout_sec: int = 10
    pacing_ms: int = 300
    max_summary_chars: int = 1500
    llm_api_base: str = ""
    llm_model: str = ""
    llm_api_key: str = ""


@dataclass
class SourcesConfig:
    timeout_sec: int = 15
    bloomberg_enabled: bool = True
    bloomberg_base_url: str = "https://www.bloomberg.com/feeds"
    bloomberg_lookback_hours: int = 24
    bloomberg_feeds: dict[str, str] = field(default_factory=dict)

    wsj_enabled: bool = True
    wsj_base_url: str = "https://feeds.content.dowjones.io/public/rss"
    wsj_lookback_hours: int = 24
    wsj_feeds: dict[str, str] = field(default_factory=dict)

    reuters_enabled: bool = True
    reuters_url: str = (
        "https://news.google.com/rss/search?"
        "q=site:reuters.com%20when:24h&hl=en-US&gl=US&ceid=US:en"
    )
    reuters_lookback_hours: int = 24
    reuters_meta_fetch_limit: int = 20
    reuters_meta_rate_limit_sec: float = 1.0
    reuters_meta_timeout_sec: int = 5


@dataclass
class ImportanceConfig:
    title_regex: str = r"(?i)(?<!-)\b(breaking|exclusive|urgent|just in)\b"
    cross_source_jaccard: float = 0.55
    gn_jaccard: float = 0.50
    score_threshold: int = 2
    gn_timeout_sec: int = 10
    gn_top_feeds: list[str] = field(default_factory=list)


@dataclass
class PathsConfig:
    root: Path = PROJECT_ROOT
    daily: Path = field(default_factory=lambda: PROJECT_ROOT / "daily")
    archive: Path = field(default_factory=lambda: PROJECT_ROOT / "archive")
    logs: Path = field(default_factory=lambda: PROJECT_ROOT / "logs")
    templates: Path = field(default_factory=lambda: PROJECT_ROOT / "templates")


@dataclass
class AppConfig:
    storage: StorageConfig
    translator: TranslatorConfig
    sources: SourcesConfig
    importance: ImportanceConfig
    paths: PathsConfig
    keywords: dict[str, list[str]]
    retention_days: int = 3
    timezone: str = "Asia/Shanghai"


def _resolve_path(raw: str | Path, base_dir: Path) -> Path:
    p = Path(raw)
    return p if p.is_absolute() else (base_dir / p).resolve()


def load_config(
    config_path: str | Path | None = None,
    base_dir: Path | None = None,
) -> AppConfig:
    """Load and validate YAML config with environment-variable overrides.

    If config_path is omitted, tries config/config.yaml and falls back to
    config/config.example.yaml so CI and cloud Actions work without manual copying.
    """
    root = (base_dir or PROJECT_ROOT).resolve()

    if config_path is not None:
        cfg_file = Path(config_path)
        if not cfg_file.exists():
            raise FileNotFoundError(f"Config file not found: {cfg_file}")
    else:
        primary = root / "config" / "config.yaml"
        fallback = root / "config" / "config.example.yaml"
        if primary.exists():
            cfg_file = primary
        elif fallback.exists():
            cfg_file = fallback
        else:
            raise FileNotFoundError(f"No config file found at {primary} or {fallback}")

    with cfg_file.open("r", encoding="utf-8") as fh:
        raw: dict[str, Any] = yaml.safe_load(fh) or {}

    # Storage
    st = raw.get("storage", {}) or {}
    my = raw.get("mysql", {}) or {}
    storage_engine = os.environ.get("MD_STORAGE_ENGINE", st.get("engine", "sqlite")).strip().lower()
    if storage_engine not in ("sqlite", "mysql"):
        raise ValueError(f"Invalid storage.engine: {storage_engine!r} (expected 'sqlite' or 'mysql')")

    sqlite_raw = os.environ.get("MD_SQLITE_PATH", st.get("sqlite_path", "data/news.db"))
    storage = StorageConfig(
        engine=storage_engine,
        sqlite_path=_resolve_path(sqlite_raw, root),
        mysql_host=os.environ.get("MD_MYSQL_HOST", str(my.get("host", "127.0.0.1"))),
        mysql_port=int(os.environ.get("MD_MYSQL_PORT", my.get("port", 3306))),
        mysql_user=os.environ.get("MD_MYSQL_USER", str(my.get("user", "root"))),
        mysql_password=os.environ.get("MD_MYSQL_PASSWORD", str(my.get("password", ""))),
        mysql_database=os.environ.get("MD_MYSQL_DATABASE", str(my.get("database", "market_dispatch"))),
    )

    # Translator
    tr = raw.get("translator", {}) or {}
    llm = tr.get("llm", {}) or {}
    tr_engine = os.environ.get("MD_TRANSLATOR_ENGINE", tr.get("engine", "google")).strip().lower()
    if tr_engine not in ("google", "llm"):
        raise ValueError(f"Invalid translator.engine: {tr_engine!r} (expected 'google' or 'llm')")

    translator = TranslatorConfig(
        engine=tr_engine,
        timeout_sec=int(tr.get("timeout_sec", 10)),
        pacing_ms=int(tr.get("pacing_ms", 300)),
        max_summary_chars=int(tr.get("max_summary_chars", 1500)),
        llm_api_base=os.environ.get("MD_LLM_API_BASE", str(llm.get("api_base", ""))).strip(),
        llm_model=os.environ.get("MD_LLM_MODEL", str(llm.get("model", ""))).strip(),
        llm_api_key=os.environ.get("MD_LLM_API_KEY", str(llm.get("api_key", ""))).strip(),
    )

    # Sources
    src = raw.get("sources", {}) or {}
    bbg = src.get("bloomberg", {}) or {}
    wsj = src.get("wsj", {}) or {}
    rtr = src.get("reuters", {}) or {}
    sources = SourcesConfig(
        timeout_sec=int(src.get("timeout_sec", 15)),
        bloomberg_enabled=bool(bbg.get("enabled", True)),
        bloomberg_base_url=str(bbg.get("base_url", "https://www.bloomberg.com/feeds")).rstrip("/"),
        bloomberg_lookback_hours=int(bbg.get("lookback_hours", 24)),
        bloomberg_feeds=dict(bbg.get("feeds", {}) or {}),
        wsj_enabled=bool(wsj.get("enabled", True)),
        wsj_base_url=str(wsj.get("base_url", "https://feeds.content.dowjones.io/public/rss")).rstrip("/"),
        wsj_lookback_hours=int(wsj.get("lookback_hours", 24)),
        wsj_feeds=dict(wsj.get("feeds", {}) or {}),
        reuters_enabled=bool(rtr.get("enabled", True)),
        reuters_url=str(
            rtr.get(
                "url",
                "https://news.google.com/rss/search?q=site:reuters.com%20when:24h&hl=en-US&gl=US&ceid=US:en",
            )
        ),
        reuters_lookback_hours=int(rtr.get("lookback_hours", 24)),
        reuters_meta_fetch_limit=int(rtr.get("meta_fetch_limit", 20)),
        reuters_meta_rate_limit_sec=float(rtr.get("meta_rate_limit_sec", 1.0)),
        reuters_meta_timeout_sec=int(rtr.get("meta_timeout_sec", 5)),
    )

    # Importance
    imp = raw.get("importance", {}) or {}
    importance = ImportanceConfig(
        title_regex=str(imp.get("title_regex", r"(?i)(?<!-)\b(breaking|exclusive|urgent|just in)\b")),
        cross_source_jaccard=float(imp.get("cross_source_jaccard", 0.55)),
        gn_jaccard=float(imp.get("gn_jaccard", 0.50)),
        score_threshold=int(imp.get("score_threshold", 2)),
        gn_timeout_sec=int(imp.get("gn_timeout_sec", 10)),
        gn_top_feeds=list(imp.get("gn_top_feeds", []) or []),
    )

    # Retention
    retention_days = int(raw.get("retention_days", 3))
    if retention_days < 1:
        raise ValueError(f"retention_days must be >= 1, got {retention_days}")

    # Paths
    p_raw = raw.get("paths", {}) or {}
    paths = PathsConfig(
        root=root,
        daily=_resolve_path(p_raw.get("daily", "daily"), root),
        archive=_resolve_path(p_raw.get("archive", "archive"), root),
        logs=_resolve_path(p_raw.get("logs", "logs"), root),
        templates=_resolve_path(p_raw.get("templates", "templates"), root),
    )

    # Keywords
    kw_raw = raw.get("keywords", {}) or {}
    keywords: dict[str, list[str]] = {
        k: [str(w).strip() for w in (v or []) if str(w).strip()]
        for k, v in kw_raw.items()
    }

    tz_name = str(raw.get("timezone", "Asia/Shanghai")).strip() or "Asia/Shanghai"

    return AppConfig(
        storage=storage,
        translator=translator,
        sources=sources,
        importance=importance,
        paths=paths,
        keywords=keywords,
        retention_days=retention_days,
        timezone=tz_name,
    )
