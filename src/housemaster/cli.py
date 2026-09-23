"""Command-line interface for HouseMaster."""

from __future__ import annotations

import argparse
import os
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from housemaster import __version__, db
from housemaster.funda import (
    DEFAULT_SEARCH_URL,
    PAGE_SIZE,
    FundaError,
    fetch_search_page,
    iter_all_listings,
)
from housemaster.pipeline import DEFAULT_PACE, FetchOptions
from housemaster.pipeline import run_fetch as run_pipeline
from housemaster.render import (
    render_csv,
    render_fetch_report,
    render_json,
    render_listings_text,
    render_page_text,
    render_status,
)
from housemaster.schedule import (
    DEFAULT_AT,
    DEFAULT_TZ,
    ScheduleError,
    next_run,
    parse_at,
)

DEFAULT_PORT = 8765

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_INTERRUPTED = 130


def _use_utf8_output() -> None:
    """Windows consoles default to cp1252 and mangle €, m² and Dutch names."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="housemaster",
        description="Extract house listings from funda.nl search results.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    search = subparsers.add_parser(
        "search",
        help="list houses from a funda search",
        description="Fetch a funda search and print the houses it returns.",
    )
    search.add_argument(
        "--url",
        default=DEFAULT_SEARCH_URL,
        help="funda search URL (default: the project's target search)",
    )
    search.add_argument(
        "--page",
        type=int,
        default=1,
        help="result page to fetch (default: %(default)s)",
    )
    search.add_argument(
        "--all",
        action="store_true",
        help=f"fetch every page, not just one ({PAGE_SIZE} listings per request)",
    )
    search.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help="with --all, stop after N pages",
    )
    search.add_argument(
        "--format",
        choices=("text", "json", "csv"),
        default="text",
        help="output format (default: %(default)s)",
    )
    search.set_defaults(handler=run_search)

    fetch = subparsers.add_parser(
        "fetch",
        help="scrape every tracked search into the local cache",
        description=(
            "Walk every search added with `housemaster add`, into SQLite. "
            "Already-known houses are not re-fetched, but their price and "
            "status are refreshed and changes are recorded. A house is delisted "
            "only once no tracked search returns it."
        ),
    )
    fetch.add_argument(
        "--url",
        default=None,
        action="append",
        metavar="URL",
        help="fetch this search instead of the tracked set (repeatable, not saved)",
    )
    fetch.add_argument("--db", default=None, metavar="PATH", help="database file")
    fetch.add_argument(
        "--max-pages",
        type=int,
        default=None,
        metavar="N",
        help="stop after N search pages (also suppresses delisting)",
    )
    fetch.add_argument(
        "--max-details",
        type=int,
        default=None,
        metavar="N",
        help="fetch at most N detail pages, to slice a long first run",
    )
    fetch.add_argument("--no-detail", action="store_true", help="skip detail pages")
    fetch.add_argument(
        "--no-boundaries",
        action="store_true",
        help="skip neighbourhood outlines (drains to nothing after one run)",
    )
    fetch.add_argument(
        "--pace",
        type=float,
        default=DEFAULT_PACE,
        metavar="SECONDS",
        help="delay between funda requests (default: %(default)s)",
    )
    fetch.add_argument("--quiet", action="store_true", help="only print the summary")
    fetch.set_defaults(handler=run_fetch)

    add = subparsers.add_parser(
        "add",
        help="track a funda search URL",
        description=(
            "Add a search to the tracked set. `fetch` walks every tracked "
            "search, so a house found by more than one is stored once."
        ),
    )
    add.add_argument("url", help="funda search URL")
    add.add_argument("--db", default=None, metavar="PATH", help="database file")
    add.add_argument("--name", default=None, help="a label, for your own reference")
    add.add_argument(
        "--no-check",
        action="store_true",
        help="skip the one request that confirms the URL resolves",
    )
    add.set_defaults(handler=run_add)

    remove = subparsers.add_parser(
        "rm",
        help="stop tracking a search",
        description=(
            "Remove a search by id -- `housemaster status` lists them. Houses it "
            "found are kept; they delist normally once nothing returns them."
        ),
    )
    remove.add_argument("search_id", type=int, metavar="ID", help="from `status`")
    remove.add_argument("--db", default=None, metavar="PATH", help="database file")
    remove.set_defaults(handler=run_rm)

    serve = subparsers.add_parser(
        "serve",
        help="browse the cached houses in a web UI",
    )
    serve.add_argument("--db", default=None, metavar="PATH", help="database file")
    serve.add_argument("--host", default="127.0.0.1", help="default: %(default)s")
    serve.add_argument(
        "--port", type=int, default=DEFAULT_PORT, help="default: %(default)s"
    )
    serve.add_argument("--debug", action="store_true", help="Flask debug mode")
    serve.set_defaults(handler=run_serve)

    schedule = subparsers.add_parser(
        "schedule",
        help="run fetch once a day, forever",
        description=(
            "Sleep until the next occurrence of --at, run a fetch, repeat. "
            "Intended as a long-running container; a missed run needs no "
            "catching up, because every pass is driven by what the cache lacks."
        ),
    )
    schedule.add_argument("--db", default=None, metavar="PATH", help="database file")
    schedule.add_argument(
        "--at",
        default=DEFAULT_AT,
        metavar="HH:MM",
        help="local time of day to run (default: %(default)s)",
    )
    schedule.add_argument(
        "--tz",
        default=DEFAULT_TZ,
        metavar="ZONE",
        help="IANA zone the time is read in (default: %(default)s)",
    )
    schedule.add_argument(
        "--run-now",
        action="store_true",
        help="fetch once on start rather than waiting for the first --at",
    )
    for shared in (fetch, schedule):
        shared.set_defaults(max_pages=None, max_details=None)
    schedule.set_defaults(
        url=None, no_detail=False, no_boundaries=False, pace=DEFAULT_PACE, quiet=False
    )
    schedule.set_defaults(handler=run_schedule)

    status = subparsers.add_parser(
        "status", help="cache contents, tracked searches and the last run"
    )
    status.add_argument("--db", default=None, metavar="PATH", help="database file")
    status.set_defaults(handler=run_status)

    return parser


def _db_path(args: argparse.Namespace) -> Path:
    """--db beats $HOUSEMASTER_DB beats the default."""
    return Path(args.db or os.environ.get("HOUSEMASTER_DB") or db.DEFAULT_DB_PATH)


STALE_LOCK_AFTER = 6 * 3600
"""Seconds after which a lock is assumed to be from a killed run, not a live
one. Longer than any real fetch, short enough to self-heal overnight."""


@contextmanager
def _fetch_lock(db_path: Path) -> Iterator[bool]:
    """Stop two fetches running at once. Yields False if one already is.

    Concurrent fetches do not corrupt anything -- SQLite serialises the writes
    -- but they contend for the writer lock until one gives up mid-run, and
    they ask funda for the same pages twice. Cheaper to refuse.

    A crashed run leaves the file behind, so it expires rather than wedging the
    schedule forever.
    """
    lock = db_path.parent / f"{db_path.name}.fetch-lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock.open("x", encoding="utf-8") as handle:
            handle.write(db.utcnow())
    except FileExistsError:
        age = time.time() - lock.stat().st_mtime
        if age < STALE_LOCK_AFTER:
            yield False
            return
        print(
            f"ignoring a {age / 3600:.1f}h-old fetch lock -- assuming a killed run",
            file=sys.stderr,
        )
    try:
        yield True
    finally:
        lock.unlink(missing_ok=True)


def run_fetch(args: argparse.Namespace) -> int:
    path = _db_path(args)
    with _fetch_lock(path) as acquired:
        if not acquired:
            print(
                "Another fetch is already running (scheduled, perhaps). Nothing to do.",
                file=sys.stderr,
            )
            return EXIT_ERROR
        return _run_fetch_locked(args, path)


def _run_fetch_locked(args: argparse.Namespace, path: Path) -> int:
    conn = db.connect(path)
    try:
        urls = tuple(args.url) if args.url else _tracked_urls(conn)
        if not urls:
            print("No searches tracked.", file=sys.stderr)
            print(
                "Add one first:  housemaster add "
                "'https://www.funda.nl/zoeken/koop?selected_area=den-haag'",
                file=sys.stderr,
            )
            return EXIT_ERROR
        return _fetch_with(args, conn, path, urls)
    finally:
        conn.close()


def _tracked_urls(conn: Any) -> tuple[str, ...]:
    return tuple(row["url"] for row in db.list_searches(conn))


def _fetch_with(
    args: argparse.Namespace, conn: Any, path: Path, urls: tuple[str, ...]
) -> int:
    options = FetchOptions(
        search_urls=urls,
        max_pages=args.max_pages,
        max_details=args.max_details,
        with_detail=not args.no_detail,
        with_boundaries=not args.no_boundaries,
        pace=args.pace,
    )

    def progress(message: str) -> None:
        print(message, file=sys.stderr)

    print(f"database: {path}", file=sys.stderr)
    print(f"searches: {len(urls)}", file=sys.stderr)
    report = run_pipeline(
        conn, options, progress=_noop_progress if args.quiet else progress
    )
    print(render_fetch_report(report, db.counts(conn)))
    return EXIT_ERROR if report.error else EXIT_OK


def _noop_progress(_message: str) -> None:
    return None


FUNDA_SEARCH_PREFIX = "https://www.funda.nl/zoeken/"
OFFERING_BY_PATH = {"koop": db.BUY, "huur": db.RENT}


def _offering_of(url: str) -> str | None:
    """`koop` -> buy, `huur` -> rent. None for a path we do not recognise.

    Only used to label the tracked search. A listing's own offering type comes
    from its payload, which is authoritative and self-classifying.
    """
    rest = url[len(FUNDA_SEARCH_PREFIX) :].split("?", 1)[0].strip("/")
    return OFFERING_BY_PATH.get(rest)


def run_add(args: argparse.Namespace) -> int:
    url = args.url.strip()
    if not url.startswith(FUNDA_SEARCH_PREFIX):
        print(
            f"error: not a funda search URL (expected {FUNDA_SEARCH_PREFIX}...)",
            file=sys.stderr,
        )
        return EXIT_ERROR

    offering = _offering_of(url)
    if offering is None:
        print(
            f"error: expected {FUNDA_SEARCH_PREFIX}koop/... "
            f"or {FUNDA_SEARCH_PREFIX}huur/...",
            file=sys.stderr,
        )
        return EXIT_ERROR

    # One request, so a typo'd URL fails here rather than silently returning
    # nothing on the next fetch. Also reports what the search actually holds.
    total = None
    if not args.no_check:
        page = fetch_search_page(url, 1)
        total = page.total_results
        if not total:
            print("error: that search returns no listings", file=sys.stderr)
            return EXIT_ERROR

    conn = db.connect(_db_path(args))
    try:
        search_id, was_new = db.add_search(conn, url, args.name, offering)
    finally:
        conn.close()

    verb = "tracking" if was_new else "already tracked"
    print(f"[{search_id}] {verb} ({offering}): {url}")
    if total is not None:
        pages = -(-total // PAGE_SIZE)
        unit = " per month" if offering == db.RENT else ""
        print(f"      {total} listings, {pages} pages, prices{unit or ' to buy'}")
    if was_new:
        print("Run `housemaster fetch` to pull it in.")
    return EXIT_OK


def run_rm(args: argparse.Namespace) -> int:
    path = _db_path(args)
    if not path.exists():
        print(f"No cache at {path}.", file=sys.stderr)
        return EXIT_ERROR
    conn = db.connect(path)
    try:
        url = db.remove_search(conn, args.search_id)
    finally:
        conn.close()
    if url is None:
        print(f"error: no search with id {args.search_id}", file=sys.stderr)
        return EXIT_ERROR
    print(f"[{args.search_id}] removed: {url}")
    print("Its houses are kept, and delist once nothing returns them.")
    return EXIT_OK


def run_schedule(args: argparse.Namespace) -> int:
    """Fetch once a day, forever. Only returns on interrupt."""
    try:
        at = parse_at(args.at)
        zone = ZoneInfo(args.tz)
    except (ScheduleError, ZoneInfoNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR

    print(f"scheduled: {args.at} {args.tz}", file=sys.stderr)
    if args.run_now:
        _fetch_once(args)

    while True:
        due = next_run(datetime.now(zone), at)
        print(f"next run: {due:%Y-%m-%d %H:%M %Z}", file=sys.stderr)
        # Recomputed from the clock rather than counted down, so a suspended
        # laptop or a slow fetch cannot make the schedule drift.
        time.sleep(max((due - datetime.now(zone)).total_seconds(), 0))
        _fetch_once(args)


def _fetch_once(args: argparse.Namespace) -> int:
    """One scheduled fetch. A failure is logged, never fatal -- the container
    should still be here tomorrow."""
    try:
        return run_fetch(args)
    except FundaError as exc:
        print(f"fetch failed: {exc}", file=sys.stderr)
    except Exception as exc:
        print(f"fetch failed: {type(exc).__name__}: {exc}", file=sys.stderr)
    return EXIT_ERROR


def run_status(args: argparse.Namespace) -> int:
    path = _db_path(args)
    if not path.exists():
        print(f"No cache at {path}. Run `housemaster fetch` to create one.")
        return EXIT_OK
    conn = db.connect(path, read_only=True)
    try:
        print(
            render_status(
                str(path),
                db.counts(conn),
                db.latest_run(conn),
                db.list_searches(conn),
            )
        )
    finally:
        conn.close()
    return EXIT_OK


def run_serve(args: argparse.Namespace) -> int:
    path = _db_path(args)
    if not path.exists():
        # Opening a missing SQLite file read-only says "unable to open database
        # file", which explains nothing.
        print(f"No cache at {path}.", file=sys.stderr)
        print("Run `housemaster fetch` first to populate it.", file=sys.stderr)
        return EXIT_ERROR

    # Imported here so `search` and `fetch` never pay Flask's import cost.
    from housemaster.web import create_app  # noqa: PLC0415

    app = create_app(path)
    print(f"HouseMaster on http://{args.host}:{args.port}", file=sys.stderr)
    app.run(host=args.host, port=args.port, debug=args.debug)
    return EXIT_OK


def run_search(args: argparse.Namespace) -> int:
    if args.page < 1:
        print("error: --page must be 1 or greater", file=sys.stderr)
        return EXIT_ERROR
    if args.max_pages is not None and args.max_pages < 1:
        print("error: --max-pages must be 1 or greater", file=sys.stderr)
        return EXIT_ERROR

    if args.all:
        listings = list(iter_all_listings(args.url, max_pages=args.max_pages))
        if args.format == "json":
            print(render_json(listings))
        elif args.format == "csv":
            print(render_csv(listings), end="")
        else:
            print(render_listings_text(listings))
        return EXIT_OK

    page = fetch_search_page(args.url, args.page)
    if args.format == "json":
        print(render_json(page.listings))
    elif args.format == "csv":
        print(render_csv(page.listings), end="")
    else:
        start = (page.page - 1) * PAGE_SIZE + 1
        print(render_page_text(page, args.url, start))
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    _use_utf8_output()
    args = build_parser().parse_args(argv)
    handler: Callable[[argparse.Namespace], int] = args.handler
    try:
        return handler(args)
    except FundaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    raise SystemExit(main())
