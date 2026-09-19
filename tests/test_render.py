from __future__ import annotations

import csv
import io
import json

import pytest

from housemaster.models import SearchPage
from housemaster.render import (
    euro,
    format_listing,
    format_summary,
    local_published,
    render_csv,
    render_json,
    render_page_text,
)

from .test_models import make_listing


@pytest.mark.parametrize(
    ("amount", "expected"),
    [(289500, "€ 289.500"), (1000, "€ 1.000"), (999, "€ 999"), (None, "n/a"), (0, "n/a")],
)
def test_euro_uses_dutch_thousands_separators(amount: int | None, expected: str) -> None:
    assert euro(amount) == expected


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        ("2026-09-04T06:30:00+00:00", "2026-09-04 08:30"),  # CEST, +02:00
        ("2026-01-15T07:00:03+00:00", "2026-01-15 08:00"),  # CET, +01:00
        ("2026-09-03T22:30:00+00:00", "2026-09-04 00:30"),  # crosses local midnight
        ("2026-09-04", "2026-09-04"),  # pre-migration row: no invented 00:00
        ("", ""),
    ],
)
def test_local_published_shows_dutch_local_time(stored: str, expected: str) -> None:
    assert local_published(stored) == expected


def test_local_published_can_drop_the_time() -> None:
    assert local_published("2026-09-03T22:30:00+00:00", with_time=False) == "2026-09-04"


def test_format_listing_includes_the_key_facts() -> None:
    text = format_listing(make_listing(), position=1)
    assert "[  1] Teststraat 1, 1000AA Den Haag" in text
    assert "€ 300.000 kosten_koper" in text
    assert "€ 4.000/m²" in text
    assert "75 m² | 3 rooms (2 bed) | energy C" in text
    assert "listed 2026-08-21 12:23" in text


def test_format_listing_aligns_continuation_lines_under_the_index() -> None:
    lines = format_listing(make_listing(), position=12).splitlines()
    indent = len("[ 12] ")
    assert all(line.startswith(" " * indent) for line in lines[1:])


def test_format_listing_without_a_position_is_unindented() -> None:
    lines = format_listing(make_listing()).splitlines()
    assert not lines[0].startswith(" ")
    assert not lines[1].startswith(" ")


def test_summary_reports_spread() -> None:
    listings = [
        make_listing(price=200_000, living_area=50),
        make_listing(price=300_000, living_area=75),
        make_listing(price=400_000, living_area=100),
    ]
    summary = format_summary(listings)
    assert "min € 200.000" in summary
    assert "median € 300.000" in summary
    assert "max € 400.000" in summary
    assert "avg 75 m²" in summary


def test_summary_of_nothing_is_empty() -> None:
    assert format_summary([]) == ""


def test_summary_skips_listings_with_missing_values() -> None:
    listings = [make_listing(price=200_000), make_listing(price=None, living_area=None)]
    assert "min € 200.000" in format_summary(listings)


def test_render_page_text_shows_paging_context() -> None:
    page = SearchPage(page=2, total_results=521, listings=[make_listing()])
    text = render_page_text(page, "https://example.test/zoeken", start_position=16)
    assert "page 2/35" in text
    assert "521 results total" in text
    assert "1 on this page" in text
    assert "[ 16]" in text


def test_render_json_is_valid_and_carries_the_derived_field() -> None:
    data = json.loads(render_json([make_listing()]))
    assert data[0]["price_per_m2"] == 4000
    assert data[0]["city"] == "Den Haag"


def test_render_json_keeps_unicode_readable() -> None:
    assert "\\u" not in render_json([make_listing(agent="Makelaardij dé lokale")])


def test_render_csv_has_a_header_and_one_row_per_listing() -> None:
    output = render_csv([make_listing(), make_listing(listing_id=2)])
    rows = list(csv.DictReader(io.StringIO(output)))
    assert len(rows) == 2
    assert rows[0]["price_per_m2"] == "4000"
    assert "listing_id" in rows[0]


def test_render_csv_of_nothing_is_empty() -> None:
    assert render_csv([]) == ""
