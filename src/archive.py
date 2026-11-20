"""Rolling N-day (default 3 days) archiving logic."""

from __future__ import annotations

from datetime import date, timedelta
import logging
from pathlib import Path
import re
import shutil

from src.time_utils import now_local

logger = logging.getLogger(__name__)

_DAILY_FILE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.html$")


def list_dated_html_files(directory: Path) -> list[str]:
    """Return sorted (descending) YYYY-MM-DD strings for dated HTML files in directory."""
    if not directory.exists():
        return []
    days: list[str] = []
    for entry in directory.iterdir():
        if entry.is_file():
            m = _DAILY_FILE_RE.match(entry.name)
            if m:
                days.append(m.group(1))
    days.sort(reverse=True)
    return days


def archive_old_daily_files(
    daily_dir: Path,
    archive_dir: Path,
    retention_days: int = 3,
    ref_date: date | None = None,
    tz_name: str = "Asia/Shanghai",
) -> list[str]:
    """Move daily/YYYY-MM-DD.html older than retention_days into archive/.

    With retention_days=3 and today=2026-11-20:
    cutoff = 2026-11-18 -> keeps 11-20, 11-19, 11-18; moves <= 11-17 to archive/.
    Never deletes any file. Returns list of archived YYYY-MM-DD strings.
    """
    daily_dir.mkdir(parents=True, exist_ok=True)
    archive_dir.mkdir(parents=True, exist_ok=True)

    today = ref_date or now_local(tz_name).date()
    cutoff = today - timedelta(days=retention_days - 1)
    moved_days: list[str] = []

    for entry in sorted(daily_dir.iterdir()):
        if not entry.is_file():
            continue
        m = _DAILY_FILE_RE.match(entry.name)
        if not m:
            continue
        day_str = m.group(1)
        try:
            file_date = date.fromisoformat(day_str)
        except ValueError:
            continue

        if file_date < cutoff:
            target = archive_dir / entry.name
            shutil.move(str(entry), str(target))
            moved_days.append(day_str)
            logger.info("Archived %s -> %s", entry, target)

    return moved_days
