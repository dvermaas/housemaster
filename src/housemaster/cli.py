"""Command-line interface for HouseMaster."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from housemaster import __version__
from housemaster.funda import (
    DEFAULT_SEARCH_URL,
    PAGE_SIZE,
    FundaError,
    fetch_search_page,
    iter_all_listings,
)
from housemaster.render import (
    render_csv,
    render_json,
    render_listings_text,
    render_page_text,
)

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

    return parser


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
