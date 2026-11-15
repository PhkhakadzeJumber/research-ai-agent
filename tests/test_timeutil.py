"""Tests for src/timeutil.py: aware timestamps with the Asia/Tbilisi offset."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from src import timeutil


def test_real_time_zone_database_is_used():
    # tzdata is a dependency, so the named zone must load (not the +04:00 safety net).
    assert isinstance(timeutil.TZ, ZoneInfo)
    assert str(timeutil.TZ) == "Asia/Tbilisi"


def test_now_is_timezone_aware_with_tbilisi_offset():
    moment = timeutil.now()
    assert moment.tzinfo is not None
    assert moment.utcoffset().total_seconds() == 4 * 3600


def test_to_iso_converts_to_tbilisi_time():
    noon_utc = datetime(2026, 11, 15, 12, 0, tzinfo=timezone.utc)
    assert timeutil.to_iso(noon_utc) == "2026-11-15T16:00:00+04:00"


def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError, match="Naive"):
        timeutil.to_iso(datetime(2026, 11, 15, 12, 0))
    with pytest.raises(ValueError, match="offset"):
        timeutil.from_iso("2026-11-15T12:00:00")


def test_round_trip():
    text = "2026-11-15T18:30:00+04:00"
    assert timeutil.to_iso(timeutil.from_iso(text)) == text
