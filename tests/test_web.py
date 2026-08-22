"""Tests for the browse app.

Flask's built-in test client, against a seeded temporary cache. No new
dependency, no network, no running server.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from flask.testing import FlaskClient

from housemaster import db
from housemaster.models import Detail, Feature, StoredPhoto
from housemaster.web import create_app

from .test_models import make_listing

HX = {"HX-Request": "true"}


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    """Three houses: one cheap, one large, one under offer with a price drop."""
    db_path = tmp_path / "test.db"
    media_root = tmp_path / "media"
    conn = db.connect(db_path)
    with conn:
        db.upsert_listing(
            conn,
            make_listing(
                listing_id=1, address="Aaastraat 1", price=260_000, living_area=60,
                energy_label="E", neighbourhood="Spoorwijk", rooms=3, bedrooms=2,
                photo_ids=("tiara/a", "tiara/b"),
            ),
            now="2026-01-01T00:00:00+00:00",
        )  # fmt: skip
        db.upsert_listing(
            conn,
            make_listing(
                listing_id=2, address="Beeklaan 2", price=340_000, living_area=110,
                energy_label="B", neighbourhood="Centrum", rooms=5, bedrooms=4,
            ),
            now="2026-02-01T00:00:00+00:00",
        )  # fmt: skip
        db.upsert_listing(
            conn,
            make_listing(listing_id=3, address="Cederlaan 3", price=300_000),
            now="2026-03-01T00:00:00+00:00",
        )
        db.save_detail(
            conn,
            Detail(
                listing_id=1,
                description="Ruim en licht appartement.",
                lat=52.05,
                lng=4.31,
                neighbourhood_price_m2=3929,
                neighbourhood_inhabitants=3820,
                features=(Feature("bouw", "Bouw", 0, "Bouwjaar", "1931-1944"),),
            ),
        )
    # A price drop on house 3, and one cached photo for house 1.
    with conn:
        db.upsert_listing(
            conn,
            make_listing(listing_id=3, address="Cederlaan 3", price=275_000),
            now="2026-04-01T00:00:00+00:00",
        )
        db.record_photo(
            conn,
            StoredPhoto(
                listing_id=1, position=0, width=720, local_path="1/00.jpg", size_bytes=99
            ),
        )
    conn.close()

    (media_root / "1").mkdir(parents=True)
    (media_root / "1" / "00.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 2000)
    (tmp_path / "secret.txt").write_text("not for the web", encoding="utf-8")
    return db_path


@pytest.fixture
def client(cache: Path) -> Iterator[FlaskClient]:
    app = create_app(cache, cache.parent / "media")
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def body(response: object) -> str:
    return response.get_data(as_text=True)  # type: ignore[attr-defined]


# --- the browse page -------------------------------------------------------


def test_the_index_renders_every_house(client: FlaskClient) -> None:
    page = body(client.get("/"))
    assert "Aaastraat 1" in page
    assert "Beeklaan 2" in page
    assert "Cederlaan 3" in page


def test_prices_are_formatted_the_way_the_cli_formats_them(client: FlaskClient) -> None:
    assert "€ 260.000" in body(client.get("/"))


def test_the_energy_scale_shows_empty_steps_too(client: FlaskClient) -> None:
    # The NEN ladder keeps its meaning only if unused steps still render.
    page = body(client.get("/"))
    assert "A+++++" in page
    assert "disabled" in page


# --- filtering -------------------------------------------------------------


def test_a_price_filter_excludes_non_matching_houses(client: FlaskClient) -> None:
    page = body(client.get("/?price_max=280000"))
    assert "Aaastraat 1" in page
    # A negative assertion catches a broken WHERE that a positive one misses.
    assert "Beeklaan 2" not in page


@pytest.mark.parametrize(
    ("query", "present", "absent"),
    [
        ("?area_min=100", "Beeklaan 2", "Aaastraat 1"),
        ("?label=B", "Beeklaan 2", "Aaastraat 1"),
        ("?hood=Spoorwijk", "Aaastraat 1", "Beeklaan 2"),
        ("?q=Beeklaan", "Beeklaan 2", "Aaastraat 1"),
        ("?beds_min=4", "Beeklaan 2", "Aaastraat 1"),
    ],
)
def test_filters(client: FlaskClient, query: str, present: str, absent: str) -> None:
    page = body(client.get(f"/{query}"))
    assert present in page
    assert absent not in page


def test_sorting_reorders_the_grid(client: FlaskClient) -> None:
    page = body(client.get("/?sort=price_desc"))
    assert page.index("Beeklaan 2") < page.index("Aaastraat 1")


def test_a_junk_number_renders_instead_of_crashing(client: FlaskClient) -> None:
    # Hand-edited URLs must degrade to "no filter", not to a 500.
    response = client.get("/?price_min=abc&area_max=%20&page=xyz")
    assert response.status_code == 200
    assert "Aaastraat 1" in body(response)


def test_an_injection_attempt_in_the_sort_falls_back(client: FlaskClient) -> None:
    response = client.get("/?sort=price';DROP TABLE listings--")
    assert response.status_code == 200
    assert "Aaastraat 1" in body(response)


def test_no_matches_gives_a_useful_empty_state(client: FlaskClient) -> None:
    page = body(client.get("/?price_min=9000000"))
    assert "No houses match" in page
    assert "Widen the price range" in page


# --- htmx ------------------------------------------------------------------


def test_an_htmx_request_returns_only_the_fragment(client: FlaskClient) -> None:
    page = body(client.get("/", headers=HX))
    assert "<!doctype" not in page.lower()
    assert 'id="results"' in page
    assert "Aaastraat 1" in page


def test_a_history_restore_returns_the_full_page(client: FlaskClient) -> None:
    # htmx sends HX-Request when restoring from its cache; answering with a
    # fragment there leaves the user on a blank page.
    page = body(client.get("/", headers={**HX, "HX-History-Restore-Request": "true"}))
    assert "<!doctype" in page.lower()


def test_a_plain_request_returns_a_whole_document(client: FlaskClient) -> None:
    assert "<!doctype" in body(client.get("/")).lower()


def test_the_more_route_returns_cards_without_the_shell(client: FlaskClient) -> None:
    page = body(client.get("/more?page=1"))
    assert "<!doctype" not in page.lower()
    assert 'class="rail"' not in page


def test_the_sentinel_overrides_every_inherited_htmx_attribute(
    client: FlaskClient, cache: Path
) -> None:
    # The sentinel sits inside the filter form, and htmx attributes inherit.
    # Without these overrides it adopts the form's target/select/push-url,
    # swaps nothing, and navigates the address bar to /more.
    conn = db.connect(cache)
    with conn:
        for listing_id in range(10, 50):
            db.upsert_listing(conn, make_listing(listing_id=listing_id))
    conn.close()

    page = body(client.get("/"))
    sentinel = page[page.index('class="sentinel"') :]
    sentinel = sentinel[: sentinel.index(">")]
    assert 'hx-target="this"' in sentinel
    assert 'hx-select="unset"' in sentinel
    assert 'hx-push-url="false"' in sentinel
    assert 'hx-swap="outerHTML"' in sentinel


# --- the detail page -------------------------------------------------------


def test_a_house_page_shows_its_detail_fields(client: FlaskClient) -> None:
    page = body(client.get("/house/1"))
    assert "Ruim en licht appartement." in page
    assert "Bouwjaar" in page
    assert "1931-1944" in page
    assert "Spoorwijk" in page


def test_a_house_page_shows_neighbourhood_context(client: FlaskClient) -> None:
    assert "€ 3.929" in body(client.get("/house/1"))


def test_a_price_drop_is_shown_with_its_history(client: FlaskClient) -> None:
    page = body(client.get("/house/3"))
    assert "Price history" in page
    assert "€ 300.000" in page
    assert "€ 275.000" in page


def test_an_unknown_house_is_a_404(client: FlaskClient) -> None:
    assert client.get("/house/999999").status_code == 404


def test_a_cached_photo_is_served_locally_and_others_hotlink(
    client: FlaskClient,
) -> None:
    page = body(client.get("/house/1"))
    assert "/media/1/00.jpg" in page  # position 0 was downloaded
    assert "cloud.funda.nl/tiara/b" in page  # position 1 was not


# --- media -----------------------------------------------------------------


def test_a_relative_media_root_still_serves(
    cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Flask resolves a relative static root against its own package directory,
    # so `serve` run from a project root with `data/media` would 404 silently.
    monkeypatch.chdir(cache.parent)
    app = create_app(Path("test.db"), Path("media"))
    app.config["TESTING"] = True
    assert app.config["MEDIA_ROOT"].is_absolute()
    with app.test_client() as relative_client:
        assert relative_client.get("/media/1/00.jpg").status_code == 200


def test_a_cached_photo_is_served(client: FlaskClient) -> None:
    response = client.get("/media/1/00.jpg")
    assert response.status_code == 200
    assert response.data.startswith(b"\xff\xd8\xff")


def test_a_missing_photo_is_a_404(client: FlaskClient) -> None:
    assert client.get("/media/1/99.jpg").status_code == 404


@pytest.mark.parametrize(
    "path",
    ["/media/../secret.txt", "/media/..%2Fsecret.txt", "/media/1/../../secret.txt"],
)
def test_path_traversal_is_refused(client: FlaskClient, path: str) -> None:
    response = client.get(path)
    assert response.status_code in (404, 400, 308)
    assert b"not for the web" not in response.data
