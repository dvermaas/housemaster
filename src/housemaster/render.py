"""Output formats for listing data.

Kept apart from `funda` so rendering can be tested without touching the network
and new formats can be added without disturbing extraction.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Sequence

from housemaster.models import FetchReport, Listing, SearchPage

RULE_WIDTH = 100


def euro(amount: int | None) -> str:
    """Dutch-style thousands separator, e.g. `EUR 289.500`."""
    return f"€ {amount:,}".replace(",", ".") if amount else "n/a"


def format_listing(listing: Listing, position: int | None = None) -> str:
    """One house as an indented multi-line block."""
    label = f"[{position:>3}] " if position is not None else ""
    indent = " " * len(label) if position is not None else ""
    return "\n".join(
        [
            f"{label}{listing.address}, {listing.postal_code} {listing.city}",
            f"{indent}{listing.neighbourhood} | {listing.object_type} | "
            f"{listing.construction_type}",
            f"{indent}{euro(listing.price)} {listing.price_condition}"
            f"  ({euro(listing.price_per_m2)}/m²)",
            f"{indent}{listing.living_area} m² | {listing.rooms} rooms "
            f"({listing.bedrooms} bed) | energy {listing.energy_label}",
            f"{indent}agent: {listing.agent} | listed {listing.published} | "
            f"{listing.photo_count} photos",
            f"{indent}{listing.url}",
        ]
    )


def format_summary(listings: Sequence[Listing]) -> str:
    """Price and area spread across a set of listings."""
    prices = [x.price for x in listings if x.price]
    per_m2 = [x.price_per_m2 for x in listings if x.price_per_m2]
    areas = [x.living_area for x in listings if x.living_area]

    lines = []
    if prices:
        ordered = sorted(prices)
        lines.append(
            f"price   min {euro(ordered[0])}  "
            f"median {euro(ordered[len(ordered) // 2])}  "
            f"max {euro(ordered[-1])}"
        )
    if per_m2:
        lines.append(
            f"per m²  min {euro(min(per_m2))}  "
            f"avg {euro(round(sum(per_m2) / len(per_m2)))}  "
            f"max {euro(max(per_m2))}"
        )
    if areas:
        lines.append(
            f"area    min {min(areas)} m²  "
            f"avg {round(sum(areas) / len(areas))} m²  "
            f"max {max(areas)} m²"
        )
    return "\n".join(lines)


def render_page_text(page: SearchPage, url: str, start_position: int) -> str:
    """A whole search page: header, each listing, then summary statistics."""
    parts = [
        "",
        url,
        f"page {page.page}/{page.total_pages}  |  "
        f"{page.total_results} results total  |  "
        f"{len(page.listings)} on this page",
        "=" * RULE_WIDTH,
    ]
    for offset, listing in enumerate(page.listings):
        parts.append("")
        parts.append(format_listing(listing, start_position + offset))
    parts.append("")
    parts.append("=" * RULE_WIDTH)
    parts.append(format_summary(page.listings))
    return "\n".join(parts)


def render_listings_text(listings: Sequence[Listing], start_position: int = 1) -> str:
    """A flat run of listings, without page framing."""
    parts = []
    for offset, listing in enumerate(listings):
        parts.append(format_listing(listing, start_position + offset))
        parts.append("")
    parts.append("=" * RULE_WIDTH)
    parts.append(format_summary(listings))
    return "\n".join(parts)


def render_json(listings: Iterable[Listing]) -> str:
    return json.dumps([x.as_dict() for x in listings], indent=2, ensure_ascii=False)


def render_csv(listings: Iterable[Listing]) -> str:
    rows = [x.as_dict() for x in listings]
    if not rows:
        return ""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def render_fetch_report(report: FetchReport, counts: dict[str, int]) -> str:
    """Summary printed after `housemaster fetch`."""
    lines = ["", "=" * RULE_WIDTH]
    if report.error:
        lines.append(f"run INCOMPLETE: {report.error}")
    changes = [
        f"{report.new_listings} new",
        f"{report.price_changes} price changes",
        f"{report.status_changes} status changes",
        f"{report.delisted} delisted",
    ]
    lines += [
        f"pages {report.pages_read}  |  {report.seen} listings seen  |  "
        + "  |  ".join(changes),
        f"details fetched {report.details_fetched}  |  photos downloaded {report.photos_downloaded}",
        "",
        f"cache: {counts['active']} active of {counts['total']} listings, "
        f"{counts['enriched']} enriched, {counts['photos']} photos on disk",
    ]
    return "\n".join(lines)


def render_status(db_path: str, counts: dict[str, int], run: object | None) -> str:
    """Summary printed by `housemaster status`."""
    lines = [
        "",
        f"database   {db_path}",
        f"listings   {counts['active']} active, {counts['total']} total",
        f"enriched   {counts['enriched']} with detail pages",
        f"photos     {counts['photos']} cached",
    ]
    if run is None:
        lines.append("last run   never -- run `housemaster fetch` to populate the cache")
    else:
        # A capped run is not a failed one: only an error means something broke.
        if run["error"]:
            state = "failed"
        elif run["complete"]:
            state = "complete"
        else:
            state = "partial"
        lines.append(
            f"last run   {run['started_at']}  ({state}, {run['pages_read']} pages)"
        )
        if run["error"]:
            lines.append(f"           {run['error']}")
    return "\n".join(lines)
