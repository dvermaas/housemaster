"""Tests for the SQLite cache.

These use real file databases under `tmp_path` rather than `:memory:`, because
WAL mode and separate reader/writer connections -- the two behaviours most worth
testing -- do not exist for in-memory databases.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from housemaster import db
from housemaster.models import Detail, Feature, FetchReport, StoredPhoto

from .test_models import make_listing


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()


# --- schema and migrations -------------------------------------------------


def test_connect_creates_the_schema(conn: sqlite3.Connection) -> None:
    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"listings", "features", "photos", "price_history", "fetch_runs"} <= tables


def test_migrations_are_idempotent(conn: sqlite3.Connection) -> None:
    # Re-running must not raise "table already exists".
    assert db.migrate(conn) == len(db.MIGRATIONS)
    assert db.migrate(conn) == len(db.MIGRATIONS)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == len(db.MIGRATIONS)


def test_wal_is_enabled_so_serve_can_read_during_a_fetch(
    conn: sqlite3.Connection,
) -> None:
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_a_reader_sees_committed_rows_while_the_writer_stays_open(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1))
    reader = db.connect(tmp_path / "test.db", read_only=True)
    try:
        assert db.get_listing(reader, 1) is not None
    finally:
        reader.close()


def test_a_read_only_connection_cannot_write(
    tmp_path: Path, conn: sqlite3.Connection
) -> None:
    reader = db.connect(tmp_path / "test.db", read_only=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            reader.execute("DELETE FROM listings")
    finally:
        reader.close()


def test_price_per_m2_is_computed_by_the_database(conn: sqlite3.Connection) -> None:
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1, price=289_500, living_area=84))
    assert db.get_listing(conn, 1)["price_per_m2"] == 3446


def test_price_per_m2_is_null_when_area_is_missing(conn: sqlite3.Connection) -> None:
    # The generated column must not blow up on a divide by zero.
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1, living_area=None))
    assert db.get_listing(conn, 1)["price_per_m2"] is None


# --- upsert and change tracking --------------------------------------------


def test_first_sight_of_a_listing_is_new(conn: sqlite3.Connection) -> None:
    with conn:
        assert db.upsert_listing(conn, make_listing(listing_id=1)) == "new"
    assert db.counts(conn)["total"] == 1


def test_seeing_an_unchanged_listing_again_adds_no_history(
    conn: sqlite3.Connection,
) -> None:
    listing = make_listing(listing_id=1)
    with conn:
        db.upsert_listing(conn, listing)
    with conn:
        assert db.upsert_listing(conn, listing) == "seen"
    # One row from first sight, and nothing more -- the history is a log of
    # changes, not of runs.
    assert len(db.get_price_history(conn, 1)) == 1
    assert db.counts(conn)["total"] == 1


def test_a_price_drop_is_recorded(conn: sqlite3.Connection) -> None:
    with conn:
        db.upsert_listing(
            conn, make_listing(listing_id=1, price=300_000), now="2026-01-01"
        )
    with conn:
        outcome = db.upsert_listing(
            conn, make_listing(listing_id=1, price=289_500), now="2026-02-01"
        )
    assert outcome == "price"
    history = db.get_price_history(conn, 1)
    assert [row["price"] for row in history] == [300_000, 289_500]
    assert db.get_listing(conn, 1)["price"] == 289_500


def test_a_status_change_is_recorded(conn: sqlite3.Connection) -> None:
    with conn:
        db.upsert_listing(
            conn, make_listing(listing_id=1, status="none"), now="2026-01-01"
        )
    with conn:
        outcome = db.upsert_listing(
            conn, make_listing(listing_id=1, status="under_bid"), now="2026-02-01"
        )
    assert outcome == "status"
    assert [row["status"] for row in db.get_price_history(conn, 1)] == [
        "none",
        "under_bid",
    ]


def test_tiny_id_is_taken_from_the_url(conn: sqlite3.Connection) -> None:
    # globalId and the URL number are different id spaces; both are stored.
    url = "https://www.funda.nl/detail/koop/den-haag/appartement-x/44561281/"
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=8116828, url=url))
    row = db.get_listing(conn, 8116828)
    assert row["listing_id"] == 8116828
    assert row["tiny_id"] == "44561281"


def test_photo_ids_are_registered_on_insert(conn: sqlite3.Connection) -> None:
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1, photo_ids=("a", "b", "c")))
    photos = db.get_photos(conn, 1)
    assert [row["image_id"] for row in photos] == ["a", "b", "c"]
    assert all(row["local_path"] is None for row in photos)


def test_reregistering_photos_does_not_clear_downloaded_ones(
    conn: sqlite3.Connection,
) -> None:
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1, photo_ids=("a", "b")))
        db.record_photo(
            conn,
            StoredPhoto(
                listing_id=1, position=0, width=720, local_path="1/00.jpg", size_bytes=99
            ),
        )
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1, photo_ids=("a", "b")))
    assert db.get_photos(conn, 1)[0]["local_path"] == "1/00.jpg"


# --- detail ----------------------------------------------------------------


def detail_for(listing_id: int, *, features: tuple[Feature, ...] = ()) -> Detail:
    return Detail(
        listing_id=listing_id,
        description="Ruim appartement.",
        lat=52.05,
        lng=4.31,
        neighbourhood_price_m2=3929,
        neighbourhood_inhabitants=3820,
        features=features,
    )


def test_save_detail_marks_the_listing_enriched(conn: sqlite3.Connection) -> None:
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1))
    assert db.listings_needing_detail(conn)
    with conn:
        db.save_detail(conn, detail_for(1))
    assert db.listings_needing_detail(conn) == []
    assert db.get_listing(conn, 1)["neighbourhood_price_m2"] == 3929


def test_saving_detail_twice_does_not_duplicate_features(
    conn: sqlite3.Connection,
) -> None:
    features = (Feature("bouw", "Bouw", 0, "Bouwjaar", "1931-1944"),)
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1))
        db.save_detail(conn, detail_for(1, features=features))
    with conn:
        db.save_detail(conn, detail_for(1, features=features))
    assert len(db.get_features(conn, 1)) == 1


def test_listings_needing_detail_skips_delisted(conn: sqlite3.Connection) -> None:
    # No point spending a request on a house that is gone.
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1))
    delist(conn, set())
    delist(conn, set())
    assert db.listings_needing_detail(conn) == []


# --- delisting -------------------------------------------------------------


def delist(conn: sqlite3.Connection, seen: set[int], now: str = "2026-06-01") -> int:
    with conn:
        return db.reconcile_presence(conn, seen, now=now)


def test_one_absence_is_not_enough_to_delist(conn: sqlite3.Connection) -> None:
    # Funda pages a live result set, so a listing can be skipped at a page
    # boundary through no fault of ours. One miss proves nothing.
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1))
    assert delist(conn, set()) == 0
    row = db.get_listing(conn, 1)
    assert row["delisted_at"] is None
    assert row["missed_runs"] == 1


def test_two_consecutive_absences_delist(conn: sqlite3.Connection) -> None:
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1))
    delist(conn, set())
    assert delist(conn, set()) == 1
    assert db.get_listing(conn, 1)["delisted_at"] is not None


def test_reappearing_resets_the_miss_counter(conn: sqlite3.Connection) -> None:
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1))
    delist(conn, set())  # missed once
    delist(conn, {1})  # seen again -> counter clears
    assert db.get_listing(conn, 1)["missed_runs"] == 0
    assert delist(conn, set()) == 0  # so one later miss still is not enough


def test_a_seen_listing_is_never_delisted(conn: sqlite3.Connection) -> None:
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1))
        db.upsert_listing(conn, make_listing(listing_id=2))
    delist(conn, {1, 2})
    delist(conn, {1, 2})
    assert db.get_listing(conn, 1)["delisted_at"] is None
    assert db.get_listing(conn, 2)["delisted_at"] is None


def test_delisting_never_deletes_data(conn: sqlite3.Connection) -> None:
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1))
    delist(conn, set())
    delist(conn, set())
    assert db.get_listing(conn, 1) is not None
    assert db.counts(conn) == {"total": 1, "active": 0, "enriched": 0, "photos": 0}


def test_a_relisted_house_is_un_delisted(conn: sqlite3.Connection) -> None:
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1))
    delist(conn, set())
    delist(conn, set())
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1))
    delist(conn, {1})
    assert db.get_listing(conn, 1)["delisted_at"] is None


def test_a_dead_photo_stops_being_retried(conn: sqlite3.Connection) -> None:
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1, photo_ids=("a",)))
    for _ in range(db.MAX_PHOTO_ATTEMPTS):
        assert db.photos_needing_download(conn, per_listing=5)
        with conn:
            db.record_photo_failure(conn, 1, 0, "404")
    assert db.photos_needing_download(conn, per_listing=5) == []


# --- queries ---------------------------------------------------------------


@pytest.fixture
def seeded(conn: sqlite3.Connection) -> sqlite3.Connection:
    rows = [
        make_listing(
            listing_id=1, price=260_000, living_area=60, energy_label="E",
            neighbourhood="Spoorwijk", rooms=3, bedrooms=2, address="Aaastraat 1",
        ),
        make_listing(
            listing_id=2, price=300_000, living_area=100, energy_label="B",
            neighbourhood="Centrum", rooms=5, bedrooms=4, address="Beeklaan 2",
        ),
        make_listing(
            listing_id=3, price=345_000, living_area=80, energy_label="C",
            neighbourhood="Spoorwijk", rooms=4, bedrooms=3, address="Cederlaan 3",
        ),
    ]  # fmt: skip
    with conn:
        for index, listing in enumerate(rows):
            db.upsert_listing(conn, listing, now=f"2026-0{index + 1}-01")
    return conn


def ids(rows: list[sqlite3.Row]) -> list[int]:
    return [row["listing_id"] for row in rows]


def test_no_filters_returns_everything(seeded: sqlite3.Connection) -> None:
    assert len(db.query_listings(seeded, db.Filters())) == 3


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (db.Filters(price_max=290_000), [1]),
        (db.Filters(price_min=290_000), [2, 3]),
        (db.Filters(area_min=80), [2, 3]),
        (db.Filters(rooms_min=4), [2, 3]),
        (db.Filters(beds_min=4), [2]),
        (db.Filters(labels=("B", "C")), [2, 3]),
        (db.Filters(hoods=("Spoorwijk",)), [1, 3]),
        (db.Filters(q="Beeklaan"), [2]),
        (db.Filters(price_min=290_000, area_min=90), [2]),
    ],
)
def test_filters(
    seeded: sqlite3.Connection, filters: db.Filters, expected: list[int]
) -> None:
    assert sorted(ids(db.query_listings(seeded, filters))) == expected


def test_delisted_are_hidden_by_default(seeded: sqlite3.Connection) -> None:
    with seeded:
        seeded.execute(
            "UPDATE listings SET delisted_at = '2026-05-01' WHERE listing_id = 1"
        )
    assert sorted(ids(db.query_listings(seeded, db.Filters()))) == [2, 3]
    assert len(db.query_listings(seeded, db.Filters(include_delisted=True))) == 3


@pytest.mark.parametrize(
    ("sort", "expected"),
    [("price_asc", [1, 2, 3]), ("price_desc", [3, 2, 1]), ("area_desc", [2, 3, 1])],
)
def test_sorting(seeded: sqlite3.Connection, sort: str, expected: list[int]) -> None:
    assert ids(db.query_listings(seeded, db.Filters(sort=sort))) == expected


def test_an_unknown_sort_falls_back_instead_of_raising(
    seeded: sqlite3.Connection,
) -> None:
    # The sort key arrives from a query string, so it cannot be trusted.
    assert (
        len(db.query_listings(seeded, db.Filters(sort="'; DROP TABLE listings--"))) == 3
    )


def test_listings_without_a_price_sort_last(conn: sqlite3.Connection) -> None:
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=1, price=None))
        db.upsert_listing(conn, make_listing(listing_id=2, price=250_000))
    assert ids(db.query_listings(conn, db.Filters(sort="price_asc"))) == [2, 1]


def test_a_quote_in_the_search_text_is_bound_not_interpolated(
    seeded: sqlite3.Connection,
) -> None:
    assert db.query_listings(seeded, db.Filters(q="' OR 1=1 --")) == []


def test_count_matches_the_query(seeded: sqlite3.Connection) -> None:
    filters = db.Filters(hoods=("Spoorwijk",))
    assert db.count_listings(seeded, filters) == len(db.query_listings(seeded, filters))


def test_pagination_does_not_repeat_rows(seeded: sqlite3.Connection) -> None:
    first = ids(db.query_listings(seeded, db.Filters(sort="price_asc"), limit=2))
    second = ids(
        db.query_listings(seeded, db.Filters(sort="price_asc"), limit=2, offset=2)
    )
    assert first == [1, 2]
    assert second == [3]


def test_facets_come_from_the_data(seeded: sqlite3.Connection) -> None:
    assert db.price_bounds(seeded) == (260_000, 345_000)
    assert db.area_bounds(seeded) == (60, 100)
    assert db.label_counts(seeded) == {"E": 1, "B": 1, "C": 1}
    assert db.distinct_neighbourhoods(seeded) == ["Centrum", "Spoorwijk"]


# --- run bookkeeping -------------------------------------------------------


def test_run_records_are_written(conn: sqlite3.Connection) -> None:
    run_id = db.start_run(conn, "https://example.test/zoeken", "2026-01-01T00:00:00")
    report = FetchReport(search_url="https://example.test/zoeken", pages_read=3, seen=45)
    report.complete = True
    db.finish_run(conn, run_id, report)

    row = db.latest_run(conn)
    assert row["pages_read"] == 3
    assert row["seen"] == 45
    assert row["complete"] == 1
    assert row["finished_at"] is not None
