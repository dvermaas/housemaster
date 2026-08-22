"""The `fetch` orchestration: funda -> database -> disk.

The only module that touches both the network and the cache. It never prints --
callers pass a `progress` callback, which keeps output formatting in `render`
and printing in `cli`.

The design property worth preserving: **every queue is a SQL predicate over
stored state**, never a set built in memory during the run. `detail_fetched_at
IS NULL` and `local_path IS NULL` mean an interrupted run is resumed by simply
running `fetch` again -- there is no checkpoint state and no `--resume` flag.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from housemaster import db, media
from housemaster.funda import (
    DEFAULT_SEARCH_URL,
    BlockedError,
    FundaError,
    fetch_detail,
    fetch_search_page,
)
from housemaster.models import FetchReport

Progress = Callable[[str], None]

DEFAULT_PACE = 0.4
"""Seconds between funda page requests. ~1.4 req/s including latency."""
PHOTO_PACE = 0.15
DEFAULT_PHOTOS_PER_LISTING = 5


@dataclass(frozen=True, slots=True)
class FetchOptions:
    """Bundled so `run_fetch` stays under a sane argument count."""

    search_url: str = DEFAULT_SEARCH_URL
    max_pages: int | None = None
    max_details: int | None = None
    photos_per_listing: int = DEFAULT_PHOTOS_PER_LISTING
    photo_width: int = media.DEFAULT_WIDTH
    with_detail: bool = True
    with_photos: bool = True
    pace: float = DEFAULT_PACE


def _noop(_message: str) -> None:
    return None


def run_fetch(
    conn: sqlite3.Connection,
    media_root: Path,
    options: FetchOptions | None = None,
    *,
    progress: Progress = _noop,
) -> FetchReport:
    """Scrape the search into the cache. Returns counts; never raises FundaError."""
    options = options or FetchOptions()
    now = db.utcnow()  # one timestamp per run, so a run's rows sort together
    report = FetchReport(search_url=options.search_url)
    run_id = db.start_run(conn, options.search_url, now)

    try:
        seen = _sweep(conn, options, report, now, progress)
    except BlockedError as exc:
        report.error = str(exc)
        progress(f"blocked after {report.pages_read} pages -- stopping")
        db.finish_run(conn, run_id, report)
        return report

    # Only a complete sweep is evidence of absence. After a partial one, every
    # unread page's listings would look missing.
    if report.complete and options.max_pages is None:
        with conn:
            report.delisted = db.reconcile_presence(conn, seen, now=now)
        if report.delisted:
            progress(f"{report.delisted} listings no longer appear in this search")
    elif not report.complete:
        progress("partial sweep -- skipping presence check")

    if options.with_detail:
        _enrich(conn, options, report, progress)
    if options.with_photos and options.photos_per_listing > 0:
        _download(conn, media_root, options, report, progress)

    db.finish_run(conn, run_id, report)
    return report


def _sweep(
    conn: sqlite3.Connection,
    options: FetchOptions,
    report: FetchReport,
    now: str,
    progress: Progress,
) -> set[int]:
    """Walk the search pages, refreshing price and status. One commit per page."""
    seen: set[int] = set()
    page = 1
    while True:
        result = fetch_search_page(options.search_url, page)  # no transaction held here
        with conn:
            for listing in result.listings:
                if listing.listing_id is None:
                    continue
                outcome = db.upsert_listing(conn, listing, now=now)
                seen.add(listing.listing_id)
                report.seen += 1
                if outcome == "new":
                    report.new_listings += 1
                elif outcome == "price":
                    report.price_changes += 1
                elif outcome == "status":
                    report.status_changes += 1

        report.pages_read = page
        last_page = result.total_pages
        if options.max_pages is not None:
            last_page = min(last_page, options.max_pages)
        progress(
            f"page {page}/{last_page}  "
            f"({report.seen} listings, {report.new_listings} new)"
        )

        if page >= last_page:
            report.complete = options.max_pages is None or last_page == result.total_pages
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


def _download(
    conn: sqlite3.Connection,
    media_root: Path,
    options: FetchOptions,
    report: FetchReport,
    progress: Progress,
) -> None:
    """Cache the first N photos of each listing that lacks them."""
    pending = db.photos_needing_download(conn, options.photos_per_listing)
    if not pending:
        return
    progress(f"downloading {len(pending)} photos")

    failures = 0
    for index, row in enumerate(pending, start=1):
        result = media.download_photo(
            media_root,
            media.PhotoRequest(row["listing_id"], row["position"], row["image_id"]),
            width=options.photo_width,
        )
        with conn:
            if result.photo is not None:
                db.record_photo(conn, result.photo)
                report.photos_downloaded += 1
            else:
                failures += 1
                db.record_photo_failure(
                    conn, row["listing_id"], row["position"], result.error or "unknown"
                )
        if index % 100 == 0:
            progress(f"  {index}/{len(pending)} photos")
        time.sleep(PHOTO_PACE)

    if failures:
        progress(f"  {failures} photos failed (retried on the next run)")
