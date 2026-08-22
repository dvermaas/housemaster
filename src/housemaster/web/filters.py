"""Jinja filters. Pure functions, so they are testable without an app."""

from __future__ import annotations

from datetime import UTC, datetime

# The Dutch NEN energy scale, best to worst. The UI renders the whole ladder and
# greys out the steps no house in the cache has, so the scale stays legible.
ENERGY_SCALE = (
    "A+++++", "A++++", "A+++", "A++", "A+", "A", "B", "C", "D", "E", "F", "G",
)  # fmt: skip

_ENERGY_CLASS = {
    "A+++++": "a5", "A++++": "a4", "A+++": "a3", "A++": "a2", "A+": "a1",
    "A": "a", "B": "b", "C": "c", "D": "d", "E": "e", "F": "f", "G": "g",
}  # fmt: skip

RECENT_DAYS = 30
"""Beyond this, an absolute date reads better than "N days ago"."""

STATUS_LABELS = {
    "none": "",
    "under_bid": "Under offer",
    "sold_under_reservation": "Sold under reservation",
}


def energy_class(label: str | None) -> str:
    """CSS suffix for an energy label. Unknown labels get a neutral swatch."""
    return _ENERGY_CLASS.get((label or "").strip(), "unknown")


def status_label(status: str | None) -> str:
    """Human wording for funda's status vocabulary.

    Unseen values are title-cased rather than hidden -- the vocabulary is not
    closed, and silently dropping an unknown status would hide a real signal.
    """
    key = (status or "").strip()
    if key in STATUS_LABELS:
        return STATUS_LABELS[key]
    return key.replace("_", " ").capitalize()


def compact(value: int | None) -> str:
    """Thousands separator without a currency symbol, for axis-style figures."""
    return f"{value:,}".replace(",", ".") if value else "-"


def since(timestamp: str | None) -> str:
    """'today' / '3 days ago' / a date. Recency is what matters when scanning."""
    if not timestamp:
        return ""
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return timestamp[:10]
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)

    days = (datetime.now(UTC) - moment).days
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < RECENT_DAYS:
        return f"{days} days ago"
    return moment.date().isoformat()
