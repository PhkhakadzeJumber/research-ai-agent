"""Time helpers. Every timestamp in Eureka goes through here (see CLAUDE.md conventions).

Timestamps are timezone-aware and written as ISO 8601 with the Asia/Tbilisi offset,
e.g. 2026-11-15T18:30:00+04:00. Naive datetimes (without an offset) are rejected.

On Windows, Python needs the `tzdata` package for named time zones. Until it's
installed, a fixed +04:00 offset is used, which is exact for Georgia: it has had
no daylight-saving changes since 2005.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

TIMEZONE_NAME = "Asia/Tbilisi"


def _load_timezone() -> tzinfo:
    try:
        return ZoneInfo(TIMEZONE_NAME)
    except ZoneInfoNotFoundError:
        return timezone(timedelta(hours=4), TIMEZONE_NAME)


TZ = _load_timezone()


def now() -> datetime:
    """The current time, timezone-aware, in Asia/Tbilisi."""
    return datetime.now(TZ)


def to_iso(moment: datetime) -> str:
    """'2026-11-15T18:30:00+04:00'. Raises ValueError for naive datetimes."""
    if moment.tzinfo is None:
        raise ValueError("Naive datetime: every timestamp must carry a timezone offset.")
    return moment.astimezone(TZ).isoformat(timespec="seconds")


def from_iso(text: str) -> datetime:
    """Parses an ISO 8601 timestamp. Raises ValueError if it has no offset."""
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError(f"Timestamp without timezone offset: {text!r}.")
    return moment
