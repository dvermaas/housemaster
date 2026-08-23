"""Working out when the next daily run is due.

Pure date arithmetic, kept apart from the CLI so the awkward part -- the two
days a year that are not 24 hours long -- can be tested without waiting for
them.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

DEFAULT_AT = "06:00"
DEFAULT_TZ = "Europe/Amsterdam"


class ScheduleError(ValueError):
    """The requested time of day could not be read."""


def parse_at(text: str) -> time:
    """Read `HH:MM` (or `HH:MM:SS`) into a time of day."""
    try:
        return time.fromisoformat(text.strip())
    except ValueError as exc:
        raise ScheduleError(f"not a time of day: {text!r} -- expected HH:MM") from exc


def next_run(now: datetime, at: time) -> datetime:
    """The next moment the clock on the wall reads `at`.

    Computed from the local date rather than by adding 24 hours, so the run
    stays at the same wall-clock time across a daylight-saving change. Adding a
    fixed day would drift it an hour twice a year, which is exactly the sort of
    thing nobody notices until the March run lands at 05:00.

    `now` must be timezone-aware; the result carries the same zone.
    """
    if now.tzinfo is None:
        raise ScheduleError("next_run needs an aware datetime")
    today = datetime.combine(now.date(), at, tzinfo=now.tzinfo)
    if today > now:
        return today
    return datetime.combine(now.date() + timedelta(days=1), at, tzinfo=now.tzinfo)
