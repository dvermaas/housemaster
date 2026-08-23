"""The `fetch` orchestration: funda -> database.

The only module that touches both the network and the cache. It never prints --
callers pass a `progress` callback, which keeps output formatting in `render`
and printing in `cli`.

The design property worth preserving: **every queue is a SQL predicate over
stored state**, never a set built in memory during the run. `detail_fetched_at
IS NULL` means an interrupted run is resumed by simply running `fetch` again --
there is no checkpoint state and no `--resume` flag.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from housemaster import db
from housemaster.funda import (
    DEFAULT_SEARCH_URL,
    BlockedError,
    FundaError,
    fetch_boundary,
    fetch_detail,
    fetch_search_page,
    neighbourhood_slug,
)
from housemaster.models import FetchReport

Progress = Callable[[str], None]

DEFAULT_PACE = 0.4
"""Seconds between funda page requests. ~1.4 req/s including latency."""


@dataclass(frozen=True, slots=True)
class FetchOptions:
    """Bundled so `run_fetch` stays under a sane argument count."""

    search_urls: tuple[str, ...] = (DEFAULT_SEARCH_URL,)
    max_pages: int | None = None
    max_details: int | None = None
    with_detail: bool = True
    with_boundaries: bool = True
    pace: float = DEFAULT_PACE


def _noop(_message: str) -> None:
    return None


def run_fetch(
    conn: sqlite3.Connection,
    options: FetchOptions | None = None,
    *,
    progress: Progress = _noop,
) -> FetchReport:
    """Scrape every tracked search into the cache. Never raises FundaError."""
    options = options or FetchOptions()
    now = db.utcnow()  # one timestamp per run, so a run's rows sort together
    label = " | ".join(options.search_urls)
    report = FetchReport(search_url=label, started_at=now)
    run_id = db.start_run(conn, label, now)

    # THE union, not one set per search. A house is present if *any* tracked
    # search still returns it, so reconciling per search would have each sweep
    # delist every other search's houses.
    seen: set[int] = set()
    report.complete = True
    try:
        for index, url in enumerate(options.search_urls, start=1):
            if len(options.search_urls) > 1:
                progress(f"search {index}/{len(options.search_urls)}: {url}")
            seen |= _sweep(conn, url, options, report, progress)
    except BlockedError as exc:
        report.error = str(exc)
        report.complete = False
        progress(f"blocked after {report.pages_read} pages -- stopping")
        db.finish_run(conn, run_id, report)
        return report

    # Only a complete walk of *every* search is evidence of absence. After a
    # partial one, every unread page's listings would look missing.
    if report.complete and options.max_pages is None:
        with conn:
            report.delisted = db.reconcile_presence(conn, seen, now=now)
        if report.delisted:
            progress(f"{report.delisted} listings no longer appear in any search")
    elif not report.complete:
        progress("partial sweep -- skipping presence check")

    if options.with_detail:
        _enrich(conn, options, report, progress)
    if options.with_boundaries:
        _outline(conn, options, report, progress)

    db.finish_run(conn, run_id, report)
    return report


def _sweep(
    conn: sqlite3.Connection,
    search_url: str,
    options: FetchOptions,
    report: FetchReport,
    progress: Progress,
) -> set[int]:
    """Walk one search's pages, refreshing price and status. One commit per page.

    `report.pages_read` and the change counters accumulate across searches --
    they describe the run, not this search.
    """
    seen: set[int] = set()
    page = 1
    while True:
        result = fetch_search_page(search_url, page)  # no transaction held here
        with conn:
            for listing in result.listings:
                if listing.listing_id is None:
                    continue
                outcome = db.upsert_listing(conn, listing, now=report.started_at)
                seen.add(listing.listing_id)
                report.seen += 1
                if outcome == "new":
                    report.new_listings += 1
                elif outcome == "price":
                    report.price_changes += 1
                elif outcome == "status":
                    report.status_changes += 1

        report.pages_read += 1
        last_page = result.total_pages
        if options.max_pages is not None:
            last_page = min(last_page, options.max_pages)
        progress(
            f"page {page}/{last_page}  "
            f"({report.seen} listings, {report.new_listings} new)"
        )

        if page >= last_page:
            # One capped search makes the whole run partial: `complete` gates
            # delisting, and it must never be true while pages went unread.
            if options.max_pages is not None and last_page != result.total_pages:
                report.complete = False
            return seen
        page += 1
        time.sleep(options.pace)


def _enrich(
    conn: sqlite3.Connection,
    options: FetchOptions,
    report: FetchReport,
    progress: Progress,
) -> None:
    """Fetch detail pages for listings that have never had one."""
    pending = db.listings_needing_detail(conn, options.max_details)
    if not pending:
        return
    progress(f"fetching {len(pending)} detail pages")

    for index, row in enumerate(pending, start=1):
        try:
            detail = fetch_detail(row["url"])
        except BlockedError as exc:
            # If search got through but detail is blocked, stop asking.
            report.error = str(exc)
            progress("blocked during detail pass -- stopping")
            return
        except FundaError as exc:
            # One unparseable listing must not end the run; it stays queued.
            progress(f"  skipped {row['listing_id']}: {exc}")
            continue

        with conn:
            db.save_detail(conn, detail)
        report.details_fetched += 1
        if index % 25 == 0:
            progress(f"  {index}/{len(pending)} details")
        time.sleep(options.pace)


def _outline(
    conn: sqlite3.Connection,
    options: FetchOptions,
    report: FetchReport,
    progress: Progress,
) -> None:
    """Fetch the outline of every buurt that does not have one yet.

    Boundaries do not move, so this drains to nothing after the first run and
    costs zero requests thereafter. A buurt whose slug does not resolve is
    recorded as a miss rather than retried forever -- see MAX_BOUNDARY_ATTEMPTS.
    """
    pending = db.neighbourhoods_needing_boundary(conn)
    if not pending:
        return
    progress(f"fetching {len(pending)} neighbourhood outlines")

    missed = 0
    for index, row in enumerate(pending, start=1):
        name = row["name"]
        try:
            boundary = fetch_boundary(name)
        except BlockedError as exc:
            report.error = str(exc)
            progress("blocked during boundary pass -- stopping")
            return
        except FundaError as exc:
            boundary, reason = None, str(exc)
        else:
            reason = "slug did not resolve to a neighborhood"

        with conn:
            if boundary is not None:
                db.save_boundary(conn, boundary)
                report.boundaries_fetched += 1
            else:
                missed += 1
                db.record_boundary_miss(conn, name, neighbourhood_slug(name), reason)

        if index % 25 == 0:
            progress(f"  {index}/{len(pending)} outlines")
        time.sleep(options.pace)

    if missed:
        progress(f"  {missed} buurten have no resolvable outline")
