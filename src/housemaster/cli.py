"""Command-line interface for HouseMaster."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

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

    status = subparsers.add_parser(
        "status", help="cache contents, tracked searches and the last run"
    )
    status.add_argument("--db", default=None, metavar="PATH", help="database file")
    status.set_defaults(handler=run_status)

    return parser


def _db_path(args: argparse.Namespace) -> Path:
    """--db beats $HOUSEMASTER_DB beats the default."""
    return Path(args.db or os.environ.get("HOUSEMASTER_DB") or db.DEFAULT_DB_PATH)


def run_fetch(args: argparse.Namespace) -> int:
    path = _db_path(args)
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


def run_add(args: argparse.Namespace) -> int:
    url = args.url.strip()
    if not url.startswith(FUNDA_SEARCH_PREFIX):
        print(
            f"error: not a funda search URL (expected {FUNDA_SEARCH_PREFIX}...)",
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
        search_id, was_new = db.add_search(conn, url, args.name)
    finally:
        conn.close()

    verb = "tracking" if was_new else "already tracked"
    print(f"[{search_id}] {verb}: {url}")
    if total is not None:
        pages = -(-total // PAGE_SIZE)
        print(f"      {total} listings, {pages} pages")
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
    try:
        return args.handler(args)
    except FundaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return EXIT_INTERRUPTED


if __name__ == "__main__":
    raise SystemExit(main())
