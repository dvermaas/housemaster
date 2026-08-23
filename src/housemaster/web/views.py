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
    Response,
    abort,
    jsonify,
    render_template,
    request,
    url_for,
)

from housemaster import db
from housemaster.web.filters import ENERGY_SCALE, status_label

bp = Blueprint("browse", __name__)

PER_PAGE = 24
GALLERY_PREVIEW = 5
"""Photos shown before the rest fold away behind a disclosure."""

VIEWS = ("grid", "map")
"""Two ways to look at the same filtered set. The filter rail is shared."""


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


def current_view() -> str:
    requested = (request.args.get("view") or "").strip()
    return requested if requested in VIEWS else "grid"


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
    view = current_view()

    # The map reads its own data from this URL rather than from the swapped
    # markup, so the map instance survives a filter change with its viewport
    # intact instead of being torn down and rebuilt.
    geojson_args = {
        k: v
        for k, v in request.args.to_dict(flat=False).items()
        if k not in {"page", "view"}
    }

    return {
        "listings": listings,
        "more_url": url_for("browse.more", **next_args),
        "view": view,
        "bounds": db.map_bounds(conn, filters) if view == "map" else None,
        "geojson_url": url_for("browse.houses_geojson", **geojson_args),
        "other_view": "grid" if view == "map" else "map",
        "view_url": url_for(
            "browse.index",
            **(
                request.args.to_dict(flat=False)
                | {"view": ["grid" if view == "map" else "map"], "page": ["1"]}
            ),
        ),
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


@bp.route("/houses.geojson")
def houses_geojson() -> Response:
    """The filtered set as GeoJSON, for the map layer to render.

    Carries only what drawing a marker needs -- the popup fetches its own card,
    so this stays small even at a few thousand houses.
    """
    rows = db.query_map_points(_conn(), filters_from_args())
    return jsonify(
        {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "id": row["listing_id"],
                    "geometry": {
                        "type": "Point",
                        # GeoJSON is lng,lat -- the opposite order to how the
                        # rest of this codebase says it.
                        "coordinates": [row["lng"], row["lat"]],
                    },
                    "properties": {
                        "id": row["listing_id"],
                        "label": row["energy_label"] or "?",
                        "price": row["price"],
                    },
                }
                for row in rows
            ],
        }
    )


@bp.route("/house/<int:listing_id>/card")
def house_card(listing_id: int) -> str:
    """The popup shown when a map marker is clicked.

    Rendered by Jinja rather than assembled in JavaScript, so the card shares
    exactly one definition of how a house is presented.
    """
    listing = db.get_listing(_conn(), listing_id)
    if listing is None:
        abort(404)
    return render_template("_popup.html", house=listing, status_label=status_label)


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
