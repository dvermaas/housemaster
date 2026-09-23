"""Routes: a small JSON API, and the single-page app that reads it.

The browser does the browsing. It downloads one offering type's whole index
once, keeps it in IndexedDB, and filters, sorts and maps it in memory -- so a
filter change never touches the network. The server's job shrinks to handing
over that index cheaply and answering the two questions the index cannot:
full-text search over descriptions, and one house's detail.

Everything under `/api/` is JSON. Everything else is the app shell, so a
reload or a shared link on any client-side route still lands on the app.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import mimetypes
import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from flask import Blueprint, Response, abort, current_app, jsonify, request

from housemaster import db
from housemaster.web.api import (
    ErrorPayload,
    HousePayload,
    IndexColumn,
    IndexPayload,
    Listing,
    Offering,
    SearchPayload,
    ShapesPayload,
)

bp = Blueprint("browse", __name__)

MAX_QUERY = 100
"""Search text is bound, not interpolated, but a megabyte of it is still a
megabyte of LIKE. Nobody types more than this."""

_COMPRESSIBLE = ("application/json", "text/", "application/javascript", "image/svg")
_MIN_GZIP = 1024


def _conn() -> sqlite3.Connection:
    from housemaster.web import get_conn  # noqa: PLC0415 - avoids a circular import

    return get_conn()


def _offering(raw: str) -> Offering:
    if raw not in db.OFFERING_TYPES:
        abort(404)
    return cast("Offering", raw)


# --- caching ---------------------------------------------------------------


def _data_version() -> tuple[int, ...]:
    """Changes whenever the cache does.

    A `fetch` commits into the WAL, and a checkpoint later folds it into the
    main file, so between them the two stats move on every write. That is far
    cheaper than asking SQLite, and a spurious change only costs a rebuild.
    """
    path: Path = current_app.config["DB_PATH"]
    version: list[int] = []
    for candidate in (path, path.with_name(path.name + "-wal")):
        try:
            stat = candidate.stat()
        except FileNotFoundError:
            version += [0, 0]
        else:
            version += [stat.st_mtime_ns, stat.st_size]
    return tuple(version)


class _Payload:
    """A serialised response body, its gzip, and an ETag over its content."""

    __slots__ = ("body", "etag", "gzipped")

    def __init__(self, data: Mapping[str, object]) -> None:
        self.body = json.dumps(data, separators=(",", ":")).encode()
        self.gzipped = gzip.compress(self.body, compresslevel=6)
        self.etag = hashlib.sha1(self.body, usedforsecurity=False).hexdigest()[:20]


def _cached(key: str, build: Callable[[], Mapping[str, object]]) -> _Payload:
    """Build once per data version, per process.

    The index is ~1.5 MB of JSON for a few thousand houses and every page load
    revalidates it, so rebuilding it per request would make the cheapest
    response in the app the most expensive one.
    """
    store: dict[str, tuple[tuple[int, ...], _Payload]]
    store = current_app.extensions.setdefault("housemaster_payloads", {})
    version = _data_version()
    hit = store.get(key)
    if hit is None or hit[0] != version:
        hit = (version, _Payload(build()))
        store[key] = hit
    return hit[1]


def _respond(payload: _Payload) -> Response:
    """Conditional and compressed. A 304 is the common case on a warm client."""
    if payload.etag in request.if_none_match:
        response = Response(status=304)
    elif request.accept_encodings.quality("gzip") > 0:
        response = Response(payload.gzipped, mimetype="application/json")
        response.headers["Content-Encoding"] = "gzip"
    else:
        response = Response(payload.body, mimetype="application/json")
    response.set_etag(payload.etag)
    response.headers["Vary"] = "Accept-Encoding"
    # Always revalidate: the ETag makes that one cheap round trip.
    response.headers["Cache-Control"] = "no-cache"
    return response


# --- the API ---------------------------------------------------------------


def _epoch(timestamp: str | None) -> int | None:
    """ISO timestamp to epoch seconds. Shorter on the wire, and sortable as a
    number; the browser formats it in Dutch local time."""
    if not timestamp:
        return None
    try:
        moment = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if moment.tzinfo is None:
        # A bare date predates keeping the clock time. Midnight UTC keeps it on
        # the right day in Amsterdam, which is all such a row ever claimed.
        return int(datetime.fromisoformat(timestamp[:10] + "T00:00:00+00:00").timestamp())
    return int(moment.timestamp())


def _index(offering: Offering) -> IndexPayload:
    conn = _conn()
    rows = db.browse_index(conn, offering)
    counts = db.counts(conn, offering)
    columns = cast("list[IndexColumn]", list(db.INDEX_COLUMNS))
    time_columns = {columns.index(c) for c in ("published", "first_seen_at")}
    delisted = columns.index("delisted_at")

    def encode(row: sqlite3.Row) -> list[Any]:
        values = list(row)
        for i in time_columns:
            values[i] = _epoch(values[i])
        # The browser only needs to know *whether*; the date is on the detail.
        values[delisted] = 1 if values[delisted] else 0
        return values

    run = db.latest_run(conn)
    scale = db.neighbourhood_price_scale(conn)
    return {
        "offering": offering,
        # Columnar: names once, then bare arrays. Half the size of an array of
        # objects before gzip, and still a fifth smaller after it.
        "columns": columns,
        "rows": [encode(row) for row in rows],
        "meta": {
            "counts": {
                "total": counts["total"],
                "active": counts["active"],
                "enriched": counts["enriched"],
                "boundaries": counts["boundaries"],
            },
            "last_run": _epoch(run["started_at"]) if run else None,
            "hood_scale": list(scale) if scale else None,
        },
    }


@bp.route("/api/index/<offering>")
def index(offering: str) -> Response:
    """One offering type's whole browse index. See `db.browse_index`."""
    offering = _offering(offering)
    return _respond(_cached(f"index:{offering}", lambda: _index(offering)))


@bp.route("/api/search/<offering>")
def search(offering: str) -> Response:
    """Ids whose address, buurt or description contains `q`.

    The browser matches address and buurt itself, instantly, and merges these
    in when they arrive -- descriptions are too large to ship in the index.
    """
    offering = _offering(offering)
    q = (request.args.get("q") or "").strip()[:MAX_QUERY]
    ids = db.search_ids(_conn(), offering, q) if q else []
    payload: SearchPayload = {"q": q, "ids": ids}
    return jsonify(payload)


@bp.route("/api/house/<int:listing_id>")
def house(listing_id: int) -> Response:
    """Everything the detail page shows beyond what the index already holds."""
    conn = _conn()
    listing = db.get_listing(conn, listing_id)
    if listing is None:
        abort(404)
    payload: HousePayload = {
        # Every column, as stored: the contract test holds `Listing` to it.
        "listing": cast("Listing", dict(listing)),
        "features": [
            {
                "group": row["group_title"],
                "label": row["label"],
                "value": row["value"],
            }
            for row in db.get_features(conn, listing_id)
        ],
        "photos": [row["image_id"] for row in db.get_photos(conn, listing_id)],
        "history": [
            {
                "id": row["id"],
                "observed_at": row["observed_at"],
                "price": row["price"],
                "status": row["status"],
            }
            for row in db.get_price_history(conn, listing_id)
        ],
    }
    return jsonify(payload)


def _shapes() -> ShapesPayload:
    """Buurt outlines, coloured by funda's own price level for that buurt.

    Unfiltered on purpose: this is the map's context layer. Outlines that
    appeared and vanished as you moved a price slider would read as data rather
    than as geography.

    `geometry` is already-serialised GeoJSON from the database, so it is spliced
    in with `json.loads` rather than rebuilt -- funda's ring order and winding
    are preserved exactly as fetched.
    """
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": index,
                "geometry": json.loads(row["geometry"]),
                "properties": {"name": row["name"], "price_m2": row["price_m2"]},
            }
            for index, row in enumerate(db.neighbourhood_shapes(_conn()))
        ],
    }


@bp.route("/api/neighbourhoods.geojson")
def neighbourhoods_geojson() -> Response:
    return _respond(_cached("shapes", _shapes))


@bp.route("/api/<path:_rest>")
def api_not_found(_rest: str) -> Response:
    """An unknown API path is a JSON 404, never the app shell -- a client that
    got HTML back from a typo'd endpoint would fail somewhere far less obvious."""
    payload: ErrorPayload = {"error": "not found"}
    response = jsonify(payload)
    response.status_code = 404
    return response


# --- the app shell ---------------------------------------------------------


def _asset(path: Path) -> tuple[bytes, bytes | None, str]:
    """A built file, read once per mtime, with a gzip beside it if worth it."""
    store: dict[Path, tuple[int, tuple[bytes, bytes | None, str]]]
    store = current_app.extensions.setdefault("housemaster_assets", {})
    mtime = path.stat().st_mtime_ns
    hit = store.get(path)
    if hit is None or hit[0] != mtime:
        body = path.read_bytes()
        mimetype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        compress = mimetype.startswith(_COMPRESSIBLE) and len(body) >= _MIN_GZIP
        hit = (mtime, (body, gzip.compress(body, 6) if compress else None, mimetype))
        store[path] = hit
    return hit[1]


def _serve(path: Path, *, immutable: bool) -> Response:
    body, gzipped, mimetype = _asset(path)
    if gzipped is not None and request.accept_encodings.quality("gzip") > 0:
        response = Response(gzipped, mimetype=mimetype)
        response.headers["Content-Encoding"] = "gzip"
    else:
        response = Response(body, mimetype=mimetype)
    response.headers["Vary"] = "Accept-Encoding"
    # Vite puts a content hash in every asset name, so an asset URL never
    # changes meaning and can be cached for good. The shell cannot: it is what
    # points at the current hashes.
    response.headers["Cache-Control"] = (
        "public, max-age=31536000, immutable" if immutable else "no-cache"
    )
    if not immutable:
        response.add_etag()
        response.make_conditional(request)
    return response


@bp.route("/", defaults={"path": ""})
@bp.route("/<path:path>")
def shell(path: str) -> Response:
    dist: Path = current_app.config["DIST_DIR"]
    shell_file = dist / "index.html"
    if not shell_file.is_file():
        return Response(
            "The web app is not built. Run `npm run build` in frontend/, "
            "or `npm run dev` there and open the Vite URL.\n",
            status=503,
            mimetype="text/plain",
        )

    if path:
        candidate = (dist / path).resolve()
        # resolve() then a containment check: `..` in the path must not walk
        # out of the build directory.
        if candidate.is_relative_to(dist) and candidate.is_file():
            return _serve(candidate, immutable=path.startswith("assets/"))
        if path.startswith("assets/"):
            abort(404)  # a stale hash, not a client-side route
    return _serve(shell_file, immutable=False)
