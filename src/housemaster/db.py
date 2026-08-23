"""SQLite cache for scraped listings.

Deliberately knows nothing about funda or HTTP -- it takes `Listing` / `Detail`
records and stores them. `pipeline` is the only module that touches both this
and the network.

Schema changes go through `MIGRATIONS`: append a function, never edit an
existing one. `PRAGMA user_version` records how many have run. That is enough
bookkeeping for a single-file local cache and avoids an Alembic dependency.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from housemaster.models import Boundary, Detail, Listing

DEFAULT_DB_PATH = Path("data/housemaster.db")
MAX_BOUNDARY_ATTEMPTS = 2


def utcnow() -> str:
    """Timestamps are ISO-8601 UTC strings: SQLite has no date type, and text
    in this format sorts chronologically."""
    return datetime.now(UTC).isoformat(timespec="seconds")


# --- schema ----------------------------------------------------------------


def _migration_001_initial(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE listings (
            listing_id        INTEGER PRIMARY KEY,   -- funda globalId; the stable key
            tiny_id           TEXT,                  -- the number in the detail URL
            url               TEXT NOT NULL,
            address           TEXT NOT NULL,
            postal_code       TEXT,
            city              TEXT,
            neighbourhood     TEXT,
            price             INTEGER,
            price_condition   TEXT,
            living_area       INTEGER,
            rooms             INTEGER,
            bedrooms          INTEGER,
            energy_label      TEXT,
            object_type       TEXT,
            construction_type TEXT,
            status            TEXT,
            published         TEXT,
            agent             TEXT,
            photo_count       INTEGER NOT NULL DEFAULT 0,
            -- STORED, not VIRTUAL: virtual generated columns cannot be indexed,
            -- and this is a primary sort key in the web UI.
            price_per_m2      INTEGER GENERATED ALWAYS AS (
                CASE WHEN price > 0 AND living_area > 0
                     THEN price / living_area END) STORED,
            -- detail-only; NULL until the listing has been enriched
            description               TEXT,
            lat                       REAL,
            lng                       REAL,
            neighbourhood_price_m2    INTEGER,
            neighbourhood_inhabitants INTEGER,
            detail_fetched_at         TEXT,
            first_seen_at     TEXT NOT NULL,
            last_seen_at      TEXT NOT NULL,
            missed_runs       INTEGER NOT NULL DEFAULT 0,
            -- "stopped appearing in this search", NOT "sold": a price rise past
            -- the search ceiling also triggers it. The UI must not say "sold".
            delisted_at       TEXT
        ) STRICT;

        CREATE TABLE features (
            listing_id  INTEGER NOT NULL REFERENCES listings ON DELETE CASCADE,
            group_id    TEXT NOT NULL,
            group_title TEXT NOT NULL,
            position    INTEGER NOT NULL,
            label       TEXT NOT NULL,
            value       TEXT,
            PRIMARY KEY (listing_id, group_id, position)
        ) STRICT, WITHOUT ROWID;

        CREATE TABLE photos (
            listing_id    INTEGER NOT NULL REFERENCES listings ON DELETE CASCADE,
            position      INTEGER NOT NULL,
            image_id      TEXT NOT NULL,   -- CDN path, straight from search results
            width         INTEGER,
            local_path    TEXT,            -- NULL until downloaded
            bytes         INTEGER,
            downloaded_at TEXT,
            attempts      INTEGER NOT NULL DEFAULT 0,
            last_error    TEXT,
            PRIMARY KEY (listing_id, position)
        ) STRICT, WITHOUT ROWID;

        CREATE TABLE price_history (
            -- A surrogate key, not (listing_id, observed_at): timestamps are
            -- second-granular and one run stamps every row with the same value,
            -- so a composite key would silently swallow a change observed in the
            -- same second as an earlier one.
            id          INTEGER PRIMARY KEY,
            listing_id  INTEGER NOT NULL REFERENCES listings ON DELETE CASCADE,
            observed_at TEXT NOT NULL,
            price       INTEGER,
            status      TEXT
        ) STRICT;

        CREATE TABLE fetch_runs (
            run_id            INTEGER PRIMARY KEY AUTOINCREMENT,
            search_url        TEXT NOT NULL,
            started_at        TEXT NOT NULL,
            finished_at       TEXT,
            pages_read        INTEGER NOT NULL DEFAULT 0,
            seen              INTEGER NOT NULL DEFAULT 0,
            new_listings      INTEGER NOT NULL DEFAULT 0,
            price_changes     INTEGER NOT NULL DEFAULT 0,
            status_changes    INTEGER NOT NULL DEFAULT 0,
            delisted          INTEGER NOT NULL DEFAULT 0,
            details_fetched   INTEGER NOT NULL DEFAULT 0,
            photos_downloaded INTEGER NOT NULL DEFAULT 0,
            complete          INTEGER NOT NULL DEFAULT 0,
            error             TEXT
        ) STRICT;

        -- The UI's default view hides delisted houses, so the hot indexes are
        -- partial ones scoped to that predicate.
        CREATE INDEX idx_listings_price ON listings(price)
            WHERE delisted_at IS NULL;
        CREATE INDEX idx_listings_area ON listings(living_area)
            WHERE delisted_at IS NULL;
        CREATE INDEX idx_listings_ppm2 ON listings(price_per_m2)
            WHERE delisted_at IS NULL;
        CREATE INDEX idx_listings_hood   ON listings(neighbourhood);
        CREATE INDEX idx_listings_label  ON listings(energy_label);
        CREATE INDEX idx_listings_seen   ON listings(first_seen_at DESC);
        CREATE INDEX idx_history_listing ON price_history(listing_id, observed_at DESC);
    """)


def _migration_002_drop_photo_storage(conn: sqlite3.Connection) -> None:
    """Photos are hotlinked from funda's CDN now, not downloaded.

    The image ids stay -- they are what builds a URL -- but everything that
    described a *file on disk* goes, along with the retry bookkeeping that only
    existed because a download could fail. `listings.photo_count` stays: it
    comes from the search payload, not from anything we fetched.

    Existing rows keep their ids, so an upgraded cache renders straight away.
    """
    conn.executescript("""
        ALTER TABLE photos DROP COLUMN local_path;
        ALTER TABLE photos DROP COLUMN bytes;
        ALTER TABLE photos DROP COLUMN downloaded_at;
        ALTER TABLE photos DROP COLUMN attempts;
        ALTER TABLE photos DROP COLUMN last_error;
        ALTER TABLE photos DROP COLUMN width;
        ALTER TABLE fetch_runs DROP COLUMN photos_downloaded;
    """)


def _migration_003_boundaries(conn: sqlite3.Connection) -> None:
    """Neighbourhood outlines, for the map's choropleth overlay.

    Keyed on the buurt *name* because that is what a listing carries -- funda
    never gives us an identifier, so the slug is derived and is therefore a
    property of the row rather than its key.

    `geometry` stays NULL for a buurt whose slug did not resolve; `attempts`
    stops us asking again forever. Boundaries do not move, so a fetched row is
    never refreshed.
    """
    conn.executescript("""
        CREATE TABLE boundaries (
            name       TEXT PRIMARY KEY,   -- matches listings.neighbourhood
            slug       TEXT NOT NULL,
            geometry   TEXT,               -- GeoJSON geometry; NULL until fetched
            fetched_at TEXT,
            attempts   INTEGER NOT NULL DEFAULT 0,
            last_error TEXT
        ) STRICT, WITHOUT ROWID;

        ALTER TABLE fetch_runs ADD COLUMN boundaries_fetched INTEGER NOT NULL DEFAULT 0;
    """)


def _migration_004_searches(conn: sqlite3.Connection) -> None:
    """The set of funda searches this cache tracks.

    `fetch` walks all of them, so a house can be found by more than one. Nothing
    records *which* search found a house: presence is a property of the union,
    which is exactly what the delisting reconciliation needs. Anything finer
    would have to be maintained per search and would make "gone" ambiguous.
    """
    conn.executescript("""
        CREATE TABLE searches (
            search_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            url         TEXT NOT NULL UNIQUE,
            label       TEXT,
            added_at    TEXT NOT NULL,
            last_run_at TEXT
        ) STRICT;
    """)


MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (
    _migration_001_initial,
    _migration_002_drop_photo_storage,
    _migration_003_boundaries,
    _migration_004_searches,
)


def migrate(conn: sqlite3.Connection) -> int:
    """Bring a database up to the current schema. Safe to call every time."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    for index, migration in enumerate(MIGRATIONS[version:], start=version + 1):
        with conn:
            migration(conn)
            # PRAGMA cannot be parameterised, but `index` is a loop counter over
            # a module constant -- never user input.
            conn.execute(f"PRAGMA user_version = {index}")
    return len(MIGRATIONS)


def connect(
    path: str | Path = DEFAULT_DB_PATH, *, read_only: bool = False
) -> sqlite3.Connection:
    """Open the cache. Writers migrate; readers assume a migrated database.

    WAL lets `serve` read while `fetch` writes.
    """
    path = Path(path)
    if read_only:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)

    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    if not read_only:
        conn.execute("PRAGMA journal_mode = WAL")
        migrate(conn)
    return conn


# --- writing ---------------------------------------------------------------

_LISTING_COLUMNS = (
    "tiny_id", "url", "address", "postal_code", "city", "neighbourhood",
    "price", "price_condition", "living_area", "rooms", "bedrooms",
    "energy_label", "object_type", "construction_type", "status", "published",
    "agent", "photo_count",
)  # fmt: skip


def _tiny_id(url: str) -> str:
    """The number in a detail URL -- a different id space from globalId."""
    return url.rstrip("/").rsplit("/", 1)[-1] if url else ""


def upsert_listing(
    conn: sqlite3.Connection, listing: Listing, *, now: str | None = None
) -> str:
    """Insert or refresh one listing.

    Returns what happened: "new", "price", "status", or "seen". A price or
    status change also appends a `price_history` row -- an unchanged listing
    appends nothing, so the history stays a log of changes rather than of runs.
    """
    now = now or utcnow()
    previous = conn.execute(
        "SELECT price, status FROM listings WHERE listing_id = ?", (listing.listing_id,)
    ).fetchone()

    values = {
        "listing_id": listing.listing_id,
        "tiny_id": _tiny_id(listing.url),
        "url": listing.url,
        "address": listing.address,
        "postal_code": listing.postal_code,
        "city": listing.city,
        "neighbourhood": listing.neighbourhood,
        "price": listing.price,
        "price_condition": listing.price_condition,
        "living_area": listing.living_area,
        "rooms": listing.rooms,
        "bedrooms": listing.bedrooms,
        "energy_label": listing.energy_label,
        "object_type": listing.object_type,
        "construction_type": listing.construction_type,
        "status": listing.status,
        "published": listing.published,
        "agent": listing.agent,
        "photo_count": listing.photo_count,
        "now": now,
    }

    if previous is None:
        # Interpolated names come from _LISTING_COLUMNS, a module constant; every
        # value is bound by name from `values`.
        columns = ", ".join(_LISTING_COLUMNS)
        placeholders = ", ".join(f":{name}" for name in _LISTING_COLUMNS)
        conn.execute(
            f"INSERT INTO listings (listing_id, {columns}, "  # noqa: S608
            f"first_seen_at, last_seen_at) "
            f"VALUES (:listing_id, {placeholders}, :now, :now)",
            values,
        )
        _record_observation(conn, listing.listing_id, now, listing.price, listing.status)
        _replace_photo_ids(conn, listing.listing_id, listing.photo_ids)
        return "new"

    assignments = ", ".join(f"{name} = :{name}" for name in _LISTING_COLUMNS)
    conn.execute(
        f"UPDATE listings SET {assignments}, "  # noqa: S608
        "last_seen_at = :now, delisted_at = NULL WHERE listing_id = :listing_id",
        values,
    )
    _replace_photo_ids(conn, listing.listing_id, listing.photo_ids)

    if previous["price"] != listing.price:
        _record_observation(conn, listing.listing_id, now, listing.price, listing.status)
        return "price"
    if previous["status"] != listing.status:
        _record_observation(conn, listing.listing_id, now, listing.price, listing.status)
        return "status"
    return "seen"


def _record_observation(
    conn: sqlite3.Connection, listing_id: int, now: str, price: int | None, status: str
) -> None:
    # Dedupe is the caller's job: upsert_listing only calls this on an actual
    # change, so every row here is a real observation.
    conn.execute(
        "INSERT INTO price_history (listing_id, observed_at, price, status) "
        "VALUES (?, ?, ?, ?)",
        (listing_id, now, price, status),
    )


def _replace_photo_ids(
    conn: sqlite3.Connection, listing_id: int, photo_ids: Sequence[str]
) -> None:
    """Register the CDN paths without disturbing what has already been downloaded."""
    conn.executemany(
        "INSERT INTO photos (listing_id, position, image_id) VALUES (?, ?, ?) "
        "ON CONFLICT (listing_id, position) DO UPDATE SET image_id = excluded.image_id",
        [(listing_id, position, image_id) for position, image_id in enumerate(photo_ids)],
    )


def save_detail(
    conn: sqlite3.Connection, detail: Detail, *, now: str | None = None
) -> None:
    """Store the detail-page fields and replace that listing's kenmerken."""
    conn.execute(
        "UPDATE listings SET description = ?, lat = ?, lng = ?, "
        "neighbourhood_price_m2 = ?, neighbourhood_inhabitants = ?, "
        "detail_fetched_at = ? WHERE listing_id = ?",
        (
            detail.description,
            detail.lat,
            detail.lng,
            detail.neighbourhood_price_m2,
            detail.neighbourhood_inhabitants,
            now or utcnow(),
            detail.listing_id,
        ),
    )
    conn.execute("DELETE FROM features WHERE listing_id = ?", (detail.listing_id,))
    conn.executemany(
        "INSERT INTO features "
        "(listing_id, group_id, group_title, position, label, value) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [
            (detail.listing_id, f.group_id, f.group_title, f.position, f.label, f.value)
            for f in detail.features
        ],
    )


MISS_THRESHOLD = 2
"""Consecutive absences before a listing counts as gone. See reconcile_presence."""


def reconcile_presence(
    conn: sqlite3.Connection, seen: set[int], *, now: str, threshold: int = MISS_THRESHOLD
) -> int:
    """Track which listings stopped appearing, and delist after two strikes.

    Funda pages a *live* result set: `&search_result=N` re-sorts under us, so a
    listing inserted while we page shifts everything down and one listing per
    page boundary can be skipped entirely. A single absence is therefore not
    evidence of anything, which is why delisting needs two consecutive misses.

    Callers MUST only run this after a complete page walk -- doing it after a
    partial run would mark every unread page's listings as gone.

    Returns the number newly delisted. Nothing is ever deleted.
    """
    conn.execute(
        "UPDATE listings SET missed_runs = missed_runs + 1 WHERE delisted_at IS NULL"
    )
    if seen:
        placeholders = ", ".join("?" * len(seen))
        conn.execute(
            # Names are placeholders, values are bound.
            f"UPDATE listings SET last_seen_at = ?, missed_runs = 0, "  # noqa: S608
            f"delisted_at = NULL WHERE listing_id IN ({placeholders})",
            [now, *seen],
        )
    cursor = conn.execute(
        "UPDATE listings SET delisted_at = ? "
        "WHERE delisted_at IS NULL AND missed_runs >= ?",
        (now, threshold),
    )
    return cursor.rowcount


# --- tracked searches ------------------------------------------------------


def add_search(
    conn: sqlite3.Connection, url: str, label: str | None = None
) -> tuple[int, bool]:
    """Track a search URL. Returns `(search_id, was_new)`.

    Idempotent on the URL, so re-adding one is a no-op that reports the existing
    id rather than an error -- the useful outcome either way is "this is tracked".
    """
    existing = conn.execute(
        "SELECT search_id FROM searches WHERE url = ?", (url,)
    ).fetchone()
    if existing is not None:
        return int(existing["search_id"]), False
    with conn:
        cursor = conn.execute(
            "INSERT INTO searches (url, label, added_at) VALUES (?, ?, ?)",
            (url, label, utcnow()),
        )
    return int(cursor.lastrowid or 0), True


def remove_search(conn: sqlite3.Connection, search_id: int) -> str | None:
    """Stop tracking a search. Returns the URL removed, or None if unknown.

    Listings it found are **kept**. They stop being seen, so the normal
    two-strike reconciliation delists them -- which is what `delisted_at` has
    always meant: "stopped matching any tracked search", not "sold".
    """
    row = conn.execute(
        "SELECT url FROM searches WHERE search_id = ?", (search_id,)
    ).fetchone()
    if row is None:
        return None
    with conn:
        conn.execute("DELETE FROM searches WHERE search_id = ?", (search_id,))
    return str(row["url"])


def list_searches(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM searches ORDER BY search_id").fetchall()


def mark_search_run(conn: sqlite3.Connection, search_id: int, now: str) -> None:
    with conn:
        conn.execute(
            "UPDATE searches SET last_run_at = ? WHERE search_id = ?", (now, search_id)
        )


def save_boundary(
    conn: sqlite3.Connection, boundary: Boundary, *, now: str | None = None
) -> None:
    """Store one outline. Idempotent: re-fetching simply overwrites."""
    conn.execute(
        "INSERT INTO boundaries (name, slug, geometry, fetched_at, attempts) "
        "VALUES (?, ?, ?, ?, 0) "
        "ON CONFLICT (name) DO UPDATE SET slug = excluded.slug, "
        "geometry = excluded.geometry, fetched_at = excluded.fetched_at, "
        "attempts = 0, last_error = NULL",
        (boundary.name, boundary.slug, boundary.geometry, now or utcnow()),
    )


def record_boundary_miss(
    conn: sqlite3.Connection, name: str, slug: str, error: str
) -> None:
    """Count a failed lookup so an unresolvable buurt stops being retried."""
    conn.execute(
        "INSERT INTO boundaries (name, slug, attempts, last_error) "
        "VALUES (?, ?, 1, ?) "
        "ON CONFLICT (name) DO UPDATE SET attempts = attempts + 1, "
        "last_error = excluded.last_error",
        (name, slug, error[:200]),
    )


# --- run bookkeeping -------------------------------------------------------


def start_run(conn: sqlite3.Connection, search_url: str, started_at: str) -> int:
    with conn:
        cursor = conn.execute(
            "INSERT INTO fetch_runs (search_url, started_at) VALUES (?, ?)",
            (search_url, started_at),
        )
    return int(cursor.lastrowid or 0)


def finish_run(conn: sqlite3.Connection, run_id: int, report: Any) -> None:
    with conn:
        conn.execute(
            "UPDATE fetch_runs SET finished_at = ?, pages_read = ?, seen = ?, "
            "new_listings = ?, price_changes = ?, status_changes = ?, delisted = ?, "
            "details_fetched = ?, boundaries_fetched = ?, complete = ?, error = ? "
            "WHERE run_id = ?",
            (
                utcnow(),
                report.pages_read,
                report.seen,
                report.new_listings,
                report.price_changes,
                report.status_changes,
                report.delisted,
                report.details_fetched,
                report.boundaries_fetched,
                int(report.complete),
                report.error,
                run_id,
            ),
        )


# --- reading ---------------------------------------------------------------


def listings_needing_detail(
    conn: sqlite3.Connection, limit: int | None = None
) -> list[sqlite3.Row]:
    """Driven by what the database lacks, not by what this run saw -- which is
    what makes an interrupted fetch resumable by simply running it again."""
    return conn.execute(
        "SELECT listing_id, url FROM listings "
        "WHERE detail_fetched_at IS NULL AND delisted_at IS NULL "
        "ORDER BY first_seen_at LIMIT ?",
        (-1 if limit is None else limit,),  # SQLite reads a negative LIMIT as "all"
    ).fetchall()


def neighbourhoods_needing_boundary(
    conn: sqlite3.Connection, limit: int | None = None
) -> list[sqlite3.Row]:
    """Buurten that appear on a live listing but have no outline yet.

    A SQL predicate over stored state, like every other queue here -- so an
    interrupted run resumes by simply running again.
    """
    return conn.execute(
        "SELECT DISTINCT l.neighbourhood AS name FROM listings l "
        "LEFT JOIN boundaries b ON b.name = l.neighbourhood "
        "WHERE l.neighbourhood <> '' AND l.delisted_at IS NULL "
        "  AND b.geometry IS NULL AND COALESCE(b.attempts, 0) < ? "
        "ORDER BY l.neighbourhood LIMIT ?",
        (MAX_BOUNDARY_ATTEMPTS, -1 if limit is None else limit),
    ).fetchall()


def neighbourhood_shapes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every outline we hold, with the buurt price level that colours it.

    Deliberately **not** filtered: this is a context layer, so the outlines stay
    put while the houses inside them come and go. `neighbourhood_price_m2` is a
    per-buurt constant from funda, not an average of our sample, so MAX() just
    picks that constant off any row carrying it.
    """
    return conn.execute(
        "SELECT b.name, b.geometry, MAX(l.neighbourhood_price_m2) AS price_m2 "
        "FROM boundaries b JOIN listings l ON l.neighbourhood = b.name "
        "WHERE b.geometry IS NOT NULL "
        "GROUP BY b.name, b.geometry ORDER BY b.name"
    ).fetchall()


CHOROPLETH_BINS = 5
"""Colour steps in the buurt overlay. Five reads clearly; more does not."""


def neighbourhood_price_scale(
    conn: sqlite3.Connection, bins: int = CHOROPLETH_BINS
) -> tuple[int, ...] | None:
    """Quantile edges for the choropleth: `(lo, b1, ... b(n-1), hi)`.

    **Quantiles, not a linear ramp.** Den Haag's buurt prices are strongly
    right-skewed -- 3 533 to 7 641 with the mass under 5 500 -- so splitting the
    range evenly put 69 of 102 buurten in the bottom two colours and 7 in the
    top two, which draws as one flat wash. Equal-count bins put ~20 buurten in
    each colour and the map actually differentiates.

    Returns None when there is nothing to draw.
    """
    values = [
        row["p"]
        for row in conn.execute(
            "SELECT MAX(l.neighbourhood_price_m2) p FROM boundaries b "
            "JOIN listings l ON l.neighbourhood = b.name "
            "WHERE b.geometry IS NOT NULL GROUP BY b.name ORDER BY p"
        )
        if row["p"] is not None
    ]
    if not values:
        return None
    edges = [values[len(values) * i // bins] for i in range(1, bins)]
    return (values[0], *edges, values[-1])


def get_listing(conn: sqlite3.Connection, listing_id: int) -> sqlite3.Row | None:
    return conn.execute(
        f"SELECT *, {_CARD_EXTRAS} FROM listings WHERE listing_id = ?",  # noqa: S608
        (listing_id,),
    ).fetchone()


def get_features(conn: sqlite3.Connection, listing_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM features WHERE listing_id = ? ORDER BY group_id, position",
        (listing_id,),
    ).fetchall()


def get_photos(conn: sqlite3.Connection, listing_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM photos WHERE listing_id = ? ORDER BY position", (listing_id,)
    ).fetchall()


def get_price_history(conn: sqlite3.Connection, listing_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM price_history WHERE listing_id = ? ORDER BY observed_at, id",
        (listing_id,),
    ).fetchall()


def latest_run(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM fetch_runs ORDER BY run_id DESC LIMIT 1"
    ).fetchone()


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute("""
        SELECT COUNT(*) AS total,
               SUM(delisted_at IS NULL)      AS active,
               SUM(detail_fetched_at IS NOT NULL) AS enriched
        FROM listings
    """).fetchone()
    shapes = conn.execute(
        "SELECT COUNT(*) AS n FROM boundaries WHERE geometry IS NOT NULL"
    ).fetchone()
    return {
        "total": row["total"] or 0,
        "active": row["active"] or 0,
        "enriched": row["enriched"] or 0,
        "boundaries": shapes["n"] or 0,
    }


def distinct_neighbourhoods(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT neighbourhood FROM listings "
        "WHERE neighbourhood <> '' AND delisted_at IS NULL ORDER BY neighbourhood"
    ).fetchall()
    return [row["neighbourhood"] for row in rows]


# --- the web UI's query ----------------------------------------------------

# (column, direction). Kept apart so the NULLs-last prefix can reference the
# bare column -- `ORDER BY price ASC IS NULL` is a syntax error.
SORTS: dict[str, tuple[str, str]] = {
    # Funda's publication date, not first_seen_at: every house in a freshly
    # built cache shares one first_seen_at, so that would order them randomly.
    "newest": ("published", "DESC"),
    "oldest": ("published", "ASC"),
    "added": ("first_seen_at", "DESC"),
    "price_asc": ("price", "ASC"),
    "price_desc": ("price", "DESC"),
    "ppm2_asc": ("price_per_m2", "ASC"),
    "ppm2_desc": ("price_per_m2", "DESC"),
    "area_desc": ("living_area", "DESC"),
    "area_asc": ("living_area", "ASC"),
}
DEFAULT_SORT = "newest"


@dataclass(frozen=True, slots=True)
class Filters:
    """Everything the browse page can narrow by. All fields optional."""

    q: str = ""
    price_min: int | None = None
    price_max: int | None = None
    area_min: int | None = None
    area_max: int | None = None
    rooms_min: int | None = None
    beds_min: int | None = None
    labels: tuple[str, ...] = ()
    hoods: tuple[str, ...] = ()
    status: str = ""
    include_delisted: bool = False
    sort: str = DEFAULT_SORT


_CARD_EXTRAS = """
    (SELECT p.image_id FROM photos p
      WHERE p.listing_id = listings.listing_id
      ORDER BY p.position LIMIT 1) AS first_image,
    (SELECT h.price FROM price_history h
      WHERE h.listing_id = listings.listing_id AND h.price > listings.price
      ORDER BY h.price DESC LIMIT 1) AS price_was
"""
"""Per-card extras: the CDN id of the first photo, and the highest earlier
price if this house has come down."""


def _where(filters: Filters) -> tuple[str, list[Any]]:
    """Build the WHERE clause. Every value is bound, never interpolated."""
    clauses: list[str] = []
    params: list[Any] = []

    if not filters.include_delisted:
        clauses.append("delisted_at IS NULL")
    if filters.q:
        clauses.append("(address LIKE ? OR neighbourhood LIKE ? OR description LIKE ?)")
        params += [f"%{filters.q}%"] * 3

    for column, value, op in (
        ("price", filters.price_min, ">="),
        ("price", filters.price_max, "<="),
        ("living_area", filters.area_min, ">="),
        ("living_area", filters.area_max, "<="),
        ("rooms", filters.rooms_min, ">="),
        ("bedrooms", filters.beds_min, ">="),
    ):
        if value is not None:
            clauses.append(f"{column} {op} ?")
            params.append(value)

    for column, values in (
        ("energy_label", filters.labels),
        ("neighbourhood", filters.hoods),
    ):
        if values:
            clauses.append(f"{column} IN ({', '.join('?' * len(values))})")
            params += list(values)

    if filters.status:
        clauses.append("status = ?")
        params.append(filters.status)

    return (" WHERE " + " AND ".join(clauses) if clauses else ""), params


def query_listings(
    conn: sqlite3.Connection, filters: Filters, *, limit: int = 30, offset: int = 0
) -> list[sqlite3.Row]:
    where, params = _where(filters)
    # Both interpolations are internally controlled: `where` is assembled from a
    # fixed column list with `?` placeholders for every value, and `order` is a
    # lookup in SORTS with a default. No caller string reaches the SQL text.
    column, direction = SORTS.get(filters.sort, SORTS[DEFAULT_SORT])
    # `col IS NULL` first puts houses missing that value last in either
    # direction; listing_id breaks ties so paging can never repeat a row.
    #
    # The two subqueries give each card its photo and price-drop marker in one
    # round trip instead of N+1 lookups from the template.
    return conn.execute(
        f"SELECT *, {_CARD_EXTRAS} FROM listings{where} "  # noqa: S608
        f"ORDER BY {column} IS NULL, {column} {direction}, listing_id "
        "LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()


MAX_MAP_POINTS = 5000
"""A ceiling so a pathological filter cannot ship an unbounded payload."""


def query_map_points(
    conn: sqlite3.Connection, filters: Filters, *, limit: int = MAX_MAP_POINTS
) -> list[sqlite3.Row]:
    """Every matching house that has coordinates -- not a page of them.

    The map plots the whole filtered set at once, so this deliberately ignores
    the pagination `query_listings` applies. Only the fields the map needs to
    *render* are selected; the popup fetches its own card.
    """
    where, params = _where(filters)
    clause = f"{where} AND lat IS NOT NULL" if where else " WHERE lat IS NOT NULL"
    return conn.execute(
        f"SELECT listing_id, lat, lng, energy_label, price, price_per_m2 "  # noqa: S608
        f"FROM listings{clause} ORDER BY listing_id LIMIT ?",
        [*params, limit],
    ).fetchall()


def count_map_points(conn: sqlite3.Connection, filters: Filters) -> int:
    """How many of the filtered houses the map can actually plot.

    Coordinates only arrive with the detail page, so during a first fetch this
    trails `count_listings` badly. The UI says so rather than quietly drawing a
    subset -- a map that silently omits four fifths of the results is worse than
    one that admits it.
    """
    where, params = _where(filters)
    clause = f"{where} AND lat IS NOT NULL" if where else " WHERE lat IS NOT NULL"
    row = conn.execute(
        f"SELECT COUNT(*) FROM listings{clause}",  # noqa: S608
        params,
    ).fetchone()
    return int(row[0])


def map_bounds(conn: sqlite3.Connection, filters: Filters) -> tuple[float, ...] | None:
    """Bounding box of the filtered set, as (min_lng, min_lat, max_lng, max_lat).

    Computed here so the map can fit its view from one number quartet instead of
    downloading the whole GeoJSON a second time just to measure it.
    """
    where, params = _where(filters)
    clause = f"{where} AND lat IS NOT NULL" if where else " WHERE lat IS NOT NULL"
    row = conn.execute(
        f"SELECT MIN(lng) x1, MIN(lat) y1, MAX(lng) x2, MAX(lat) y2 "  # noqa: S608
        f"FROM listings{clause}",
        params,
    ).fetchone()
    if row is None or row["x1"] is None:
        return None
    return (row["x1"], row["y1"], row["x2"], row["y2"])


def count_listings(conn: sqlite3.Connection, filters: Filters) -> int:
    where, params = _where(filters)
    row = conn.execute(f"SELECT COUNT(*) FROM listings{where}", params).fetchone()  # noqa: S608
    return int(row[0])


def price_bounds(conn: sqlite3.Connection) -> tuple[int, int]:
    """Range for the filter sliders, from the data rather than hardcoded."""
    row = conn.execute(
        "SELECT MIN(price) AS lo, MAX(price) AS hi FROM listings "
        "WHERE price > 0 AND delisted_at IS NULL"
    ).fetchone()
    return (row["lo"] or 0, row["hi"] or 0)


def area_bounds(conn: sqlite3.Connection) -> tuple[int, int]:
    row = conn.execute(
        "SELECT MIN(living_area) AS lo, MAX(living_area) AS hi FROM listings "
        "WHERE living_area > 0 AND delisted_at IS NULL"
    ).fetchone()
    return (row["lo"] or 0, row["hi"] or 0)


def label_counts(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT energy_label, COUNT(*) AS n FROM listings "
        "WHERE delisted_at IS NULL GROUP BY energy_label"
    ).fetchall()
    return {row["energy_label"]: row["n"] for row in rows}
