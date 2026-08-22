"""Routes.

`/` serves both the full page and the htmx partial, discriminated by the
`HX-Request` header. The alternative -- a separate partial route plus
`hx-push-url` -- pushes a URL that renders a bare fragment when the user
reloads or bookmarks it, which is the classic htmx footgun.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from flask import (
    Blueprint,
    abort,
    current_app,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from werkzeug.wrappers import Response

from housemaster import db
from housemaster.web.filters import ENERGY_SCALE, status_label

bp = Blueprint("browse", __name__)

PER_PAGE = 24
GALLERY_PREVIEW = 5
"""Photos shown before the rest fold away behind a disclosure."""


def _conn() -> sqlite3.Connection:
    from housemaster.web import get_conn  # noqa: PLC0415 - avoids a circular import

    return get_conn()


def _int(name: str) -> int | None:
    """Tolerant parse: a hand-edited `?price_min=abc` must not 500."""
    raw = (request.args.get(name) or "").strip().replace(".", "").replace(",", "")
    try:
        return int(raw) if raw else None
    except ValueError:
        return None


def filters_from_args() -> db.Filters:
    return db.Filters(
        q=(request.args.get("q") or "").strip(),
        price_min=_int("price_min"),
        price_max=_int("price_max"),
        area_min=_int("area_min"),
        area_max=_int("area_max"),
        rooms_min=_int("rooms_min"),
        beds_min=_int("beds_min"),
        labels=tuple(request.args.getlist("label")),
        hoods=tuple(request.args.getlist("hood")),
        status=(request.args.get("status") or "").strip(),
        include_delisted=request.args.get("delisted") == "1",
        sort=(request.args.get("sort") or db.DEFAULT_SORT),
    )


def _is_partial() -> bool:
    """htmx swaps get the fragment -- but a history restore needs a full page,
    or the back button lands on a bare fragment."""
    return (
        request.headers.get("HX-Request") == "true"
        and request.headers.get("HX-History-Restore-Request") != "true"
    )


def _page_context(conn: sqlite3.Connection) -> dict[str, Any]:
    filters = filters_from_args()
    page = max(_int("page") or 1, 1)
    listings = db.query_listings(
        conn, filters, limit=PER_PAGE, offset=(page - 1) * PER_PAGE
    )
    total = db.count_listings(conn, filters)
    area_lo, area_hi = db.area_bounds(conn)

    # Built here rather than in the template: carrying the current filters over
    # to the next page is URL work, not markup.
    next_args = request.args.to_dict(flat=False) | {"page": [str(page + 1)]}

    return {
        "listings": listings,
        "more_url": url_for("browse.more", **next_args),
        "total": total,
        "page": page,
        "has_more": page * PER_PAGE < total,
        "next_page": page + 1,
        "filters": filters,
        "query": request.args.to_dict(flat=False),
        "area_range": (area_lo, area_hi),
        "sorts": db.SORTS,
        "status_label": status_label,
    }


@bp.route("/")
def index() -> str:
    conn = _conn()
    context = _page_context(conn)
    if _is_partial():
        return render_template("_results.html", **context)
    return render_template(
        "index.html",
        neighbourhoods=db.distinct_neighbourhoods(conn),
        label_counts=db.label_counts(conn),
        energy_scale=ENERGY_SCALE,
        price_range=db.price_bounds(conn),
        counts=db.counts(conn),
        last_run=db.latest_run(conn),
        **context,
    )


@bp.route("/more")
def more() -> str:
    """Next batch of cards, appended by the infinite-scroll sentinel."""
    return render_template("_cards.html", **_page_context(_conn()))


@bp.route("/house/<int:listing_id>")
def house(listing_id: int) -> str:
    conn = _conn()
    listing = db.get_listing(conn, listing_id)
    if listing is None:
        abort(404)

    history = db.get_price_history(conn, listing_id)
    return render_template(
        "house.html",
        listing=listing,
        features=db.get_features(conn, listing_id),
        photos=db.get_photos(conn, listing_id),
        history=history,
        first_price=history[0]["price"] if history else None,
        status_label=status_label,
        GALLERY_PREVIEW=GALLERY_PREVIEW,
    )


@bp.route("/media/<path:filename>")
def media_file(filename: str) -> Response:
    # send_from_directory rejects traversal outside the root itself.
    return send_from_directory(
        current_app.config["MEDIA_ROOT"], filename, max_age=31536000
    )
