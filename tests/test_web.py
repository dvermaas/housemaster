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
from housemaster.models import Boundary, Detail, Feature
from housemaster.web import create_app

from .test_models import make_listing

HX = {"HX-Request": "true"}


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    """Three houses: one cheap, one large, one under offer with a price drop."""
    db_path = tmp_path / "test.db"
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
    # One buurt outline, so the overlay has something to draw.
    with conn:
        conn.execute(
            "UPDATE listings SET neighbourhood_price_m2 = 3929 WHERE listing_id = 1"
        )
        db.save_boundary(
            conn,
            Boundary(
                "Spoorwijk",
                "spoorwijk",
                '{"type":"Polygon","coordinates":'
                "[[[4.1,52.0],[4.2,52.0],[4.2,52.1],[4.1,52.0]]]}",
            ),
        )

    # A price drop on house 3.
    with conn:
        db.upsert_listing(
            conn,
            make_listing(listing_id=3, address="Cederlaan 3", price=275_000),
            now="2026-04-01T00:00:00+00:00",
        )
    conn.close()
    return db_path


@pytest.fixture
def client(cache: Path) -> Iterator[FlaskClient]:
    app = create_app(cache)
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


# --- theme and lightbox ----------------------------------------------------


def test_every_page_carries_the_theme_toggle(client: FlaskClient) -> None:
    for path in ("/", "/?view=map", "/house/1"):
        assert 'id="theme-toggle"' in body(client.get(path)), path


def test_the_theme_is_applied_before_first_paint(client: FlaskClient) -> None:
    # A stored preference read after the stylesheet would flash the wrong theme.
    page = body(client.get("/"))
    head = page[: page.index("</head>")]
    assert "housemaster-theme" in head
    assert head.index("housemaster-theme") < head.index("app.css")


def test_gallery_photos_no_longer_open_a_new_tab(client: FlaskClient) -> None:
    gallery = body(client.get("/house/1"))
    start = gallery.index('class="gallery"')
    assert 'target="_blank"' not in gallery[start : start + 900]


def test_gallery_links_still_work_without_javascript(client: FlaskClient) -> None:
    # The href is the fallback; lightbox.js only intercepts the click.
    page = body(client.get("/house/1"))
    assert "cloud.funda.nl" in page[page.index('class="gallery"') :]


def test_the_lightbox_shell_is_present_and_hidden(client: FlaskClient) -> None:
    page = body(client.get("/house/1"))
    assert 'id="lightbox"' in page
    assert 'aria-modal="true"' in page
    at = page.index('id="lightbox"')
    assert "hidden" in page[at : at + 200]


def test_pale_energy_chips_carry_their_label_for_contrast(client: FlaskClient) -> None:
    # White on the yellow end of the NEN scale is unreadable; the CSS keys off
    # data-label to give C and D dark ink instead.
    assert 'data-label="B"' in body(client.get("/"))


# --- the map view ----------------------------------------------------------


def test_the_map_view_replaces_the_grid_but_keeps_the_rail(client: FlaskClient) -> None:
    page = body(client.get("/?view=map"))
    assert 'id="map"' in page
    assert 'class="rail"' in page  # filters stay
    assert 'class="grid"' not in page  # cards do not


def test_the_map_node_is_preserved_across_swaps(client: FlaskClient) -> None:
    # Without hx-preserve the MapLibre instance is rebuilt on every filter
    # change and the viewport the user panned to is lost.
    assert "hx-preserve" in body(client.get("/?view=map"))


def test_the_view_travels_with_the_filter_form(client: FlaskClient) -> None:
    # The form serialises everything inside it; without this hidden field,
    # changing a filter on the map drops you back to the grid.
    page = body(client.get("/?view=map"))
    assert '<input type="hidden" name="view" value="map">' in page


def test_filtering_on_the_map_stays_on_the_map(client: FlaskClient) -> None:
    page = body(client.get("/?view=map&price_max=280000", headers=HX))
    assert 'data-view="map"' in page
    assert 'id="map"' in page


def test_the_map_carries_its_data_url_and_bounds(client: FlaskClient) -> None:
    page = body(client.get("/?view=map"))
    assert "houses.geojson" in page
    assert "data-bounds=" in page


def test_geojson_is_valid_and_filtered(client: FlaskClient) -> None:
    everything = client.get("/houses.geojson").get_json()
    assert everything["type"] == "FeatureCollection"
    assert len(everything["features"]) == 1  # only house 1 has coordinates

    feature = everything["features"][0]
    assert feature["geometry"]["type"] == "Point"
    # GeoJSON is lng,lat -- the reverse of how the rest of the code says it.
    assert feature["geometry"]["coordinates"] == [4.31, 52.05]
    assert feature["properties"]["id"] == 1

    none = client.get("/houses.geojson?price_min=9000000").get_json()
    assert none["features"] == []


def test_houses_without_coordinates_are_omitted(client: FlaskClient) -> None:
    # Houses 2 and 3 were never enriched, so they have no lat/lng.
    payload = client.get("/houses.geojson").get_json()
    ids = {f["properties"]["id"] for f in payload["features"]}
    assert ids == {1}


def test_bounds_are_none_when_nothing_matches(cache: Path) -> None:
    conn = db.connect(cache, read_only=True)
    try:
        assert db.map_bounds(conn, db.Filters(price_min=9_000_000)) is None
    finally:
        conn.close()


def test_the_popup_card_renders_a_house(client: FlaskClient) -> None:
    page = body(client.get("/house/1/card"))
    assert "<!doctype" not in page.lower()  # a fragment, not a page
    assert "Aaastraat 1" in page
    assert "€ 260.000" in page
    assert "cloud.funda.nl/tiara/a" in page  # hotlinked straight from funda


def test_an_unknown_popup_card_is_a_404(client: FlaskClient) -> None:
    assert client.get("/house/999999/card").status_code == 404


def test_an_unknown_view_falls_back_to_the_grid(client: FlaskClient) -> None:
    page = body(client.get("/?view=../../etc/passwd"))
    assert 'data-view="grid"' in page


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


def test_every_gallery_photo_is_hotlinked(client: FlaskClient) -> None:
    # Nothing is stored, so every photo on the page is a CDN URL and the app
    # serves no image bytes of its own.
    page = body(client.get("/house/1"))
    assert "cloud.funda.nl/tiara/a" in page
    assert "cloud.funda.nl/tiara/b" in page
    assert "/media/" not in page


# --- the app itself --------------------------------------------------------


def test_a_relative_db_path_is_resolved(
    cache: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Flask resolves a relative path against its own package directory, so
    # `serve` run from a project root would look for the cache in the wrong
    # place entirely.
    monkeypatch.chdir(cache.parent)
    app = create_app(Path("test.db"))
    app.config["TESTING"] = True
    assert app.config["DB_PATH"].is_absolute()
    with app.test_client() as relative_client:
        assert relative_client.get("/").status_code == 200


# --- the buurt overlay -----------------------------------------------------


def test_the_outline_feed_is_geojson_with_the_price_level(client: FlaskClient) -> None:
    payload = client.get("/neighbourhoods.geojson").get_json()
    assert payload["type"] == "FeatureCollection"
    feature = payload["features"][0]
    assert feature["geometry"]["type"] == "Polygon"
    assert feature["properties"] == {"name": "Spoorwijk", "price_m2": 3929}
    # The id is what MapLibre feature-state hover keys on.
    assert feature["id"] == 0


def test_the_outline_feed_ignores_filters(client: FlaskClient) -> None:
    # Geography, not data: narrowing the price range must not remove outlines.
    wide = client.get("/neighbourhoods.geojson").get_json()
    narrow = client.get("/neighbourhoods.geojson?price_max=1").get_json()
    assert len(narrow["features"]) == len(wide["features"]) == 1


def test_the_map_view_carries_what_the_overlay_needs(client: FlaskClient) -> None:
    page = body(client.get("/?view=map"))
    assert 'data-shapes="/neighbourhoods.geojson"' in page
    assert 'data-hood-scale="3929,3929,3929,3929,3929,3929"' in page
    assert 'id="hood-toggle"' in page


def test_the_toggle_is_outside_the_filter_form(client: FlaskClient) -> None:
    # It changes what the map draws, not what gets submitted. If it ever became
    # a named control inside #results it would start round-tripping as a filter.
    page = body(client.get("/?view=map"))
    toggle = page[page.index('id="hood-toggle"') - 200 : page.index('id="hood-toggle"')]
    assert "name=" not in toggle


def test_the_ramp_legend_reads_from_the_same_range(client: FlaskClient) -> None:
    page = body(client.get("/?view=map"))
    assert 'id="hood-ramp"' in page
    assert "3.929" in page  # compact-formatted, matching the layer's domain


def test_the_grid_view_does_not_ship_the_overlay(client: FlaskClient) -> None:
    assert 'id="hood-toggle"' not in body(client.get("/"))
