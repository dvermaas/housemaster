"""Tests for the fetch orchestration.

The two network entry points are monkeypatched on the `pipeline` module, so
nothing here touches funda. These are the requirements restated as tests:
dedupe, change tracking, resumability, and the delisting guards.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from housemaster import db, pipeline
from housemaster.funda import BlockedError, PayloadError
from housemaster.models import PAGE_SIZE, Boundary, Detail, Feature, SearchPage

from .test_models import make_listing


@pytest.fixture
def conn(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    connection = db.connect(tmp_path / "test.db")
    yield connection
    connection.close()


class FakeFunda:
    """A scriptable stand-in for the two funda entry points."""

    def __init__(
        self, pages: list[list[int]], *, block_on_page: int | None = None
    ) -> None:
        self.pages = pages
        self.block_on_page = block_on_page
        self.detail_calls: list[str] = []
        self.page_calls: list[int] = []
        self.prices: dict[int, int] = {}
        self.statuses: dict[int, str] = {}
        self.detail_error: Exception | None = None
        self.boundary_calls: list[str] = []
        self.unresolvable: set[str] = set()
        self.url_calls: list[str] = []
        self.by_url: dict[str, Callable[[int], SearchPage]] = {}

    def serve(self, url: str, ids: list[int]) -> None:
        """Make `url` a one-page search returning exactly `ids`."""

        def page_of(page: int) -> SearchPage:
            listings = [
                make_listing(
                    listing_id=i,
                    price=self.prices.get(i, 300_000),
                    status=self.statuses.get(i, "none"),
                    url=f"https://www.funda.nl/detail/koop/x/{i}/",
                )
                for i in (ids if page == 1 else [])
            ]
            return SearchPage(page=page, total_results=len(ids), listings=listings)

        self.by_url[url] = page_of

    def search(self, url: str, page: int = 1) -> SearchPage:
        self.page_calls.append(page)
        self.url_calls.append(url)
        if url in self.by_url:
            return self.by_url[url](page)
        if page == self.block_on_page:
            raise BlockedError("no __NUXT_DATA__ -- likely the Akamai interstitial")
        listings = [
            make_listing(
                listing_id=listing_id,
                price=self.prices.get(listing_id, 300_000),
                status=self.statuses.get(listing_id, "none"),
                photo_ids=("a", "b"),
                url=f"https://www.funda.nl/detail/koop/x/{listing_id}/",
            )
            for listing_id in self.pages[page - 1]
        ]
        # Real pages hold 15; total_results is what drives total_pages, so it
        # has to imply exactly len(self.pages) pages.
        total = (len(self.pages) - 1) * PAGE_SIZE + 1
        return SearchPage(page=page, total_results=total, listings=listings)

    def detail(self, url: str) -> Detail:
        self.detail_calls.append(url)
        if self.detail_error is not None:
            raise self.detail_error
        listing_id = int(url.rstrip("/").rsplit("/", 1)[-1])
        return Detail(
            listing_id=listing_id,
            description="Mooi.",
            lat=52.0,
            lng=4.3,
            neighbourhood_price_m2=3900,
            neighbourhood_inhabitants=3800,
            features=(Feature("bouw", "Bouw", 0, "Bouwjaar", "1931"),),
        )

    def boundary(self, city: str, name: str) -> Boundary | None:
        self.boundary_calls.append(name)
        if name in self.unresolvable:
            return None  # funda answered, but not with a neighbourhood
        return Boundary(city, name, name.lower(), '{"type":"Polygon","coordinates":[]}')

    def install(self, monkeypatch: pytest.MonkeyPatch) -> FakeFunda:
        monkeypatch.setattr(pipeline, "fetch_search_page", self.search)
        monkeypatch.setattr(pipeline, "fetch_detail", self.detail)
        monkeypatch.setattr(pipeline, "fetch_boundary", self.boundary)
        monkeypatch.setattr(pipeline.time, "sleep", lambda _s: None)
        return self


@pytest.fixture
def funda(monkeypatch: pytest.MonkeyPatch) -> FakeFunda:
    """Two pages holding two listings each."""
    return FakeFunda([[1, 2], [3, 4]]).install(monkeypatch)


def run(conn: sqlite3.Connection, **kwargs: object) -> object:
    kwargs.setdefault("with_boundaries", False)
    kwargs.setdefault("search_urls", ("https://example.test/zoeken",))
    options = pipeline.FetchOptions(**kwargs)
    return pipeline.run_fetch(conn, options)


# --- the sweep -------------------------------------------------------------


def test_a_first_run_stores_every_listing(
    conn: sqlite3.Connection, funda: FakeFunda
) -> None:
    report = run(conn)
    assert report.new_listings == 4
    assert report.seen == 4
    assert report.complete
    assert db.counts(conn)["total"] == 4


def test_max_pages_stops_the_sweep(conn: sqlite3.Connection, funda: FakeFunda) -> None:
    run(conn, max_pages=1)
    assert funda.page_calls == [1]
    assert db.counts(conn)["total"] == 2


# --- dedupe and change tracking --------------------------------------------


def test_a_second_run_fetches_no_detail_pages(
    conn: sqlite3.Connection, funda: FakeFunda
) -> None:
    run(conn)
    assert len(funda.detail_calls) == 4
    funda.detail_calls.clear()

    report = run(conn)
    # The whole point of the cache: detail is paid for once per house.
    assert funda.detail_calls == []
    assert report.new_listings == 0
    assert report.details_fetched == 0


def test_running_twice_does_not_duplicate_anything(
    conn: sqlite3.Connection, funda: FakeFunda
) -> None:
    run(conn)
    before = conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0]
    run(conn)
    assert conn.execute("SELECT COUNT(*) FROM price_history").fetchone()[0] == before
    assert db.counts(conn)["total"] == 4
    assert len(db.get_features(conn, 1)) == 1


def test_a_price_drop_is_picked_up_without_a_detail_request(
    conn: sqlite3.Connection, funda: FakeFunda
) -> None:
    run(conn)
    funda.detail_calls.clear()
    funda.prices[1] = 275_000

    report = run(conn)
    assert report.price_changes == 1
    assert funda.detail_calls == []  # price comes from the search page we already load
    assert [row["price"] for row in db.get_price_history(conn, 1)] == [300_000, 275_000]


def test_a_status_change_is_picked_up(conn: sqlite3.Connection, funda: FakeFunda) -> None:
    run(conn)
    funda.statuses[2] = "under_bid"
    report = run(conn)
    assert report.status_changes == 1
    assert db.get_listing(conn, 2)["status"] == "under_bid"


# --- resumability ----------------------------------------------------------


def test_an_interrupted_detail_pass_is_picked_up_next_run(
    conn: sqlite3.Connection, funda: FakeFunda
) -> None:
    # The queue is "what the database lacks", not "what this run saw", so a run
    # that dies between the sweep and its details loses nothing.
    run(conn, max_details=1)
    assert len(db.listings_needing_detail(conn)) == 3

    run(conn)
    assert db.listings_needing_detail(conn) == []
    assert db.counts(conn)["enriched"] == 4


def test_one_unparseable_listing_does_not_abort_the_run(
    conn: sqlite3.Connection, funda: FakeFunda
) -> None:
    funda.detail_error = PayloadError("unexpected detail state shape")
    report = run(conn)
    assert report.details_fetched == 0
    assert len(funda.detail_calls) == 4  # it kept going through all four
    # Nothing was marked enriched, so they stay queued for the next run.
    assert len(db.listings_needing_detail(conn)) == 4


# --- the delisting guards --------------------------------------------------


def test_a_block_midway_keeps_earlier_pages_and_delists_nothing(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    # This is the dangerous case: naive logic would mark every listing on the
    # unread pages as gone.
    FakeFunda([[1, 2], [3, 4], [5, 6]]).install(monkeypatch)
    run(conn)

    FakeFunda([[1, 2], [3, 4], [5, 6]], block_on_page=2).install(monkeypatch)
    report = run(conn)

    assert report.error is not None
    assert not report.complete
    assert db.counts(conn)["active"] == 6
    assert (
        conn.execute(
            "SELECT COUNT(*) FROM listings WHERE delisted_at IS NOT NULL"
        ).fetchone()[0]
        == 0
    )


def test_a_partial_run_never_marks_anything_delisted(
    conn: sqlite3.Connection, funda: FakeFunda
) -> None:
    run(conn)
    report = run(conn, max_pages=1)
    assert report.delisted == 0
    assert db.counts(conn)["active"] == 4


def test_a_listing_needs_two_complete_absences_to_delist(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeFunda([[1, 2], [3, 4]]).install(monkeypatch)
    run(conn)

    FakeFunda([[1, 2], [3]]).install(monkeypatch)  # 4 vanished
    assert run(conn).delisted == 0  # paging jitter, not evidence
    assert db.get_listing(conn, 4)["delisted_at"] is None

    FakeFunda([[1, 2], [3]]).install(monkeypatch)
    assert run(conn).delisted == 1
    assert db.get_listing(conn, 4)["delisted_at"] is not None


def test_a_delisted_listing_keeps_all_its_data(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeFunda([[1, 2]]).install(monkeypatch)
    run(conn)
    FakeFunda([[1]]).install(monkeypatch)
    run(conn)
    run(conn)

    assert db.get_listing(conn, 2) is not None
    assert db.get_features(conn, 2)
    assert db.get_price_history(conn, 2)


# --- run bookkeeping -------------------------------------------------------


def test_the_run_is_recorded(conn: sqlite3.Connection, funda: FakeFunda) -> None:
    run(conn)
    row = db.latest_run(conn)
    assert row is not None
    assert row["complete"] == 1
    assert row["new_listings"] == 4
    assert row["finished_at"] is not None


def test_a_failed_run_is_recorded_as_incomplete(
    conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    FakeFunda([[1, 2], [3, 4]], block_on_page=1).install(monkeypatch)
    run(conn)
    row = db.latest_run(conn)
    assert row is not None
    assert row["complete"] == 0
    assert "Akamai" in row["error"]


def test_progress_messages_are_emitted_not_printed(
    conn: sqlite3.Connection,
    funda: FakeFunda,
    capsys: pytest.CaptureFixture[str],
) -> None:
    messages: list[str] = []
    pipeline.run_fetch(
        conn,
        pipeline.FetchOptions(search_urls=("https://example.test/z",)),
        progress=messages.append,
    )
    assert any("page 1/" in m for m in messages)
    # The pipeline is a library: printing is the CLI's job.
    assert capsys.readouterr().out == ""


# --- the outline pass ------------------------------------------------------


def test_outlines_are_fetched_once_and_then_never_again(
    conn: sqlite3.Connection, funda: FakeFunda
) -> None:
    report = run(conn, with_boundaries=True)
    assert report.boundaries_fetched == 1  # every fake listing is in "Centrum"
    assert funda.boundary_calls == ["Centrum"]

    # Boundaries do not move, so a second run must cost zero requests.
    run(conn, with_boundaries=True)
    assert funda.boundary_calls == ["Centrum"]


def test_an_unresolvable_buurt_is_retried_then_dropped(
    conn: sqlite3.Connection, funda: FakeFunda
) -> None:
    funda.unresolvable = {"Centrum"}
    for _ in range(db.MAX_BOUNDARY_ATTEMPTS):
        run(conn, with_boundaries=True)
    assert len(funda.boundary_calls) == db.MAX_BOUNDARY_ATTEMPTS

    run(conn, with_boundaries=True)
    assert len(funda.boundary_calls) == db.MAX_BOUNDARY_ATTEMPTS  # gave up


def test_the_outline_pass_can_be_skipped(
    conn: sqlite3.Connection, funda: FakeFunda
) -> None:
    run(conn, with_boundaries=False)
    assert funda.boundary_calls == []


def test_a_block_during_the_outline_pass_stops_without_losing_the_run(
    conn: sqlite3.Connection, funda: FakeFunda, monkeypatch: pytest.MonkeyPatch
) -> None:
    def blocked(_city: str, _name: str) -> Boundary | None:
        raise BlockedError("no __NUXT_DATA__ -- likely the Akamai interstitial")

    monkeypatch.setattr(pipeline, "fetch_boundary", blocked)
    report = run(conn, with_boundaries=True)
    # The sweep already committed; only the outline pass is abandoned.
    assert report.error is not None
    assert db.counts(conn)["total"] == 4


# --- several tracked searches ----------------------------------------------

URL_A = "https://example.test/a"
URL_B = "https://example.test/b"


def test_every_tracked_search_is_walked(
    conn: sqlite3.Connection, funda: FakeFunda
) -> None:
    funda.serve(URL_A, [1, 2])
    funda.serve(URL_B, [3, 4])
    report = run(conn, search_urls=(URL_A, URL_B))
    assert funda.url_calls == [URL_A, URL_B]
    assert db.counts(conn)["total"] == 4
    # pages_read describes the run, not one search.
    assert report.pages_read == 2


def test_a_house_in_two_searches_is_stored_once(
    conn: sqlite3.Connection, funda: FakeFunda
) -> None:
    funda.serve(URL_A, [1, 2])
    funda.serve(URL_B, [2, 3])
    run(conn, search_urls=(URL_A, URL_B))
    assert db.counts(conn)["total"] == 3
    assert db.get_listing(conn, 2) is not None


def test_one_search_does_not_delist_anothers_houses(
    conn: sqlite3.Connection, funda: FakeFunda
) -> None:
    """The whole reason reconciliation takes the union.

    Reconciling per search would have B's sweep decide A's houses are gone --
    twice over, and they would delist while still being perfectly present.
    """
    funda.serve(URL_A, [1, 2])
    funda.serve(URL_B, [3, 4])
    for _ in range(3):
        report = run(conn, search_urls=(URL_A, URL_B))
        assert report.delisted == 0
    for listing_id in (1, 2, 3, 4):
        assert db.get_listing(conn, listing_id)["delisted_at"] is None


def test_a_house_leaving_every_search_still_delists(
    conn: sqlite3.Connection, funda: FakeFunda
) -> None:
    funda.serve(URL_A, [1, 2])
    funda.serve(URL_B, [2, 3])
    run(conn, search_urls=(URL_A, URL_B))

    # House 1 drops out of the only search that had it.
    funda.serve(URL_A, [2])
    run(conn, search_urls=(URL_A, URL_B))
    assert db.get_listing(conn, 1)["delisted_at"] is None  # one strike
    run(conn, search_urls=(URL_A, URL_B))
    assert db.get_listing(conn, 1)["delisted_at"] is not None


def test_a_house_kept_by_the_other_search_never_delists(
    conn: sqlite3.Connection, funda: FakeFunda
) -> None:
    funda.serve(URL_A, [1, 2])
    funda.serve(URL_B, [2, 3])
    run(conn, search_urls=(URL_A, URL_B))

    funda.serve(URL_A, [1])  # house 2 leaves A but stays in B
    for _ in range(3):
        run(conn, search_urls=(URL_A, URL_B))
    assert db.get_listing(conn, 2)["delisted_at"] is None


def test_a_block_on_the_second_search_delists_nothing(
    conn: sqlite3.Connection, funda: FakeFunda, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A partial walk of the union is not evidence of absence for anyone.
    funda.serve(URL_A, [1, 2])
    funda.serve(URL_B, [3, 4])
    run(conn, search_urls=(URL_A, URL_B))

    def blocked(url: str, page: int = 1) -> SearchPage:
        if url == URL_B:
            raise BlockedError("no __NUXT_DATA__ -- likely the Akamai interstitial")
        return funda.by_url[url](page)

    monkeypatch.setattr(pipeline, "fetch_search_page", blocked)
    report = run(conn, search_urls=(URL_A, URL_B))
    assert report.error is not None
    assert report.complete is False
    assert report.delisted == 0
    for listing_id in (3, 4):
        assert db.get_listing(conn, listing_id)["delisted_at"] is None
