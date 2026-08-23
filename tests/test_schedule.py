"""Tests for the daily-run arithmetic.

The interesting cases are the two days a year that are not 24 hours long, so
these use a real zone rather than a fixed offset.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from housemaster.schedule import ScheduleError, next_run, parse_at

AMS = ZoneInfo("Europe/Amsterdam")
SIX = parse_at("06:00")


def test_parse_at_reads_hh_mm() -> None:
    assert parse_at("06:00").hour == 6
    assert parse_at("23:30").minute == 30


@pytest.mark.parametrize("text", ["", "6", "25:00", "half six", "6pm"])
def test_parse_at_rejects_nonsense(text: str) -> None:
    with pytest.raises(ScheduleError, match="time of day"):
        parse_at(text)


def test_before_the_hour_runs_today() -> None:
    now = datetime(2026, 5, 10, 5, 0, tzinfo=AMS)
    assert next_run(now, SIX) == datetime(2026, 5, 10, 6, 0, tzinfo=AMS)


def test_after_the_hour_waits_for_tomorrow() -> None:
    now = datetime(2026, 5, 10, 9, 0, tzinfo=AMS)
    assert next_run(now, SIX) == datetime(2026, 5, 11, 6, 0, tzinfo=AMS)


def test_exactly_on_the_hour_waits_for_tomorrow() -> None:
    # Otherwise a run that finishes inside the same second loops immediately.
    now = datetime(2026, 5, 10, 6, 0, tzinfo=AMS)
    assert next_run(now, SIX) == datetime(2026, 5, 11, 6, 0, tzinfo=AMS)


@pytest.mark.parametrize(
    ("label", "eve"),
    [
        ("clocks forward", datetime(2026, 3, 28, 23, 0, tzinfo=AMS)),
        ("clocks back", datetime(2026, 10, 24, 23, 0, tzinfo=AMS)),
    ],
)
def test_the_hour_survives_a_clock_change(label: str, eve: datetime) -> None:
    """Adding 24 hours would drift the run an hour twice a year.

    Nobody notices until the March run lands at 05:00, so this is computed from
    the local date instead.
    """
    due = next_run(eve, SIX)
    assert (due.hour, due.minute) == (6, 0), label
    assert due.date() == (eve + timedelta(days=1)).date()
    # The wall clock says 06:00 either way, but the real gap is not 7 hours on
    # a normal night -- which is the whole point.
    assert due.utcoffset() != eve.utcoffset(), label


def test_an_aware_datetime_is_required() -> None:
    with pytest.raises(ScheduleError, match="aware"):
        next_run(datetime(2026, 5, 10, 5, 0), SIX)  # noqa: DTZ001
