"""Shared pytest fixtures for Market Dispatch tests."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from src.config import AppConfig, PROJECT_ROOT, load_config
from src.db import SQLiteDatabase


@pytest.fixture()
def tmp_workspace(tmp_path: Path) -> Path:
    """Create an isolated workspace with config.example.yaml and real Jinja2 templates."""
    (tmp_path / "config").mkdir()
    shutil.copy(PROJECT_ROOT / "config" / "config.example.yaml", tmp_path / "config" / "config.example.yaml")
    shutil.copytree(PROJECT_ROOT / "templates", tmp_path / "templates")
    return tmp_path


@pytest.fixture()
def tmp_config(tmp_workspace: Path) -> AppConfig:
    """Load AppConfig pointing all paths & SQLite DB inside tmp_workspace."""
    return load_config(base_dir=tmp_workspace)


@pytest.fixture()
def sqlite_db(tmp_config: AppConfig) -> SQLiteDatabase:
    """Provide an initialized temporary SQLiteDatabase."""
    db = SQLiteDatabase(tmp_config.storage.sqlite_path)
    db.init_db()
    yield db
    db.close()
