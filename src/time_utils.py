"""Timezone, RSS timestamp parsing, effective-day, and dual-time formatting utilities."""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import time
from zoneinfo import ZoneInfo

WEEKDAYS_CN = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def get_tz(tz_name: str = "Asia/Shanghai") -> ZoneInfo:
    """Return ZoneInfo for the configured timezone."""
    return ZoneInfo(tz_name)


def now_local(tz_name: str = "Asia/Shanghai") -> datetime:
    """Return current timezone-aware datetime in configured timezone."""
    return datetime.now(get_tz(tz_name))


def to_iso(dt: datetime, tz_name: str = "Asia/Shanghai") -> str:
    """Convert a datetime to ISO-8601 string in configured timezone (seconds precision)."""
    tz = get_tz(tz_name)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz).isoformat(timespec="seconds")


def now_iso(tz_name: str = "Asia/Shanghai") -> str:
    """Return current time as ISO-8601 string in configured timezone."""
    return to_iso(now_local(tz_name), tz_name)


def parse_iso(ts: str | None, tz_name: str = "Asia/Shanghai") -> datetime | None:
    """Parse an ISO-8601 timestamp string into a timezone-aware datetime."""
    if not ts or not ts.strip():
        return None
    raw = ts.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    tz = get_tz(tz_name)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=tz)
    return dt.astimezone(tz)


def parse_rss_datetime(
    raw_value: str | None = None,
    parsed_struct: time.struct_time | None = None,
    tz_name: str = "Asia/Shanghai",
) -> str | None:
    """Parse RSS pubDate string or feedparser struct_time to Asia/Shanghai ISO-8601.

    Returns None when neither value can be parsed.
    """
    if raw_value and raw_value.strip():
        text = raw_value.strip()
        try:
            dt = parsedate_to_datetime(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return to_iso(dt, tz_name)
        except (TypeError, ValueError, IndexError):
            iso_dt = parse_iso(text, tz_name)
            if iso_dt is not None:
                return to_iso(iso_dt, tz_name)

    if parsed_struct is not None:
        try:
            ts = calendar.timegm(parsed_struct)
            dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            return to_iso(dt, tz_name)
        except (TypeError, ValueError, OverflowError):
            return None

    return None


def effective_day(published_at: str | None, created_at: str) -> str:
    """Compute归属日 (YYYY-MM-DD) using COALESCE(published_at, created_at)."""
    ts = published_at or created_at
    return ts[:10] if ts and len(ts) >= 10 else ""


def recent_day_strings(
    days: int = 3,
    ref_date: date | None = None,
    tz_name: str = "Asia/Shanghai",
) -> list[str]:
    """Return ordered list of recent day strings [today, yesterday, ...]."""
    today = ref_date or now_local(tz_name).date()
    return [(today - timedelta(days=i)).isoformat() for i in range(days)]


def weekday_cn(day_str: str) -> str:
    """Return Chinese weekday label for YYYY-MM-DD."""
    d = date.fromisoformat(day_str)
    return WEEKDAYS_CN[d.weekday()]


def format_cn_date(day_str: str) -> str:
    """Format YYYY-MM-DD as 'YYYY 年 MM 月 DD 日'."""
    d = date.fromisoformat(day_str)
    return f"{d.year} 年 {d.month:02d} 月 {d.day:02d} 日"


def format_dual_time(
    published_at: str | None,
    created_at: str,
    page_date: str,
    tz_name: str = "Asia/Shanghai",
) -> tuple[str, str, bool]:
    """Format published_at and created_at for display on a daily report card.

    Returns (pub_display, crt_display, is_cross_day):
    - When published_at and created_at fall on the same date as page_date:
      ('HH:MM', 'HH:MM', False)
    - When created_at falls on a different date than published_at (cross-midnight fetch):
      ('MM-DD HH:MM', 'MM-DD HH:MM', True)
    - When published_at is missing:
      ('—', 'HH:MM' or 'MM-DD HH:MM', False)
    """
    pub_dt = parse_iso(published_at, tz_name)
    crt_dt = parse_iso(created_at, tz_name)

    if pub_dt is None:
        crt_disp = crt_dt.strftime("%H:%M") if crt_dt else "—"
        return "—", crt_disp, False

    if crt_dt is None:
        return pub_dt.strftime("%H:%M"), "—", False

    pub_day = pub_dt.date().isoformat()
    crt_day = crt_dt.date().isoformat()
    is_cross_day = pub_day != crt_day

    if is_cross_day or pub_day != page_date:
        return pub_dt.strftime("%m-%d %H:%M"), crt_dt.strftime("%m-%d %H:%M"), is_cross_day

    return pub_dt.strftime("%H:%M"), crt_dt.strftime("%H:%M"), False
