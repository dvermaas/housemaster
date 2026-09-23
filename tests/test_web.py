"""Tests for the web app: the JSON API, and the shell that serves the SPA.

Flask's built-in test client, against a seeded temporary cache. No new
dependency, no network, no running server. The front end has its own tests
(`npm test` in frontend/); these cover what the browser is handed.
"""

from __future__ import annotations

import gzip
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest
from flask.testing import FlaskClient

from housemaster import db
from housemaster.models import Boundary, Detail, Feature
from housemaster.web import create_app

from .test_models import make_listing

FAVICON = Path(__file__).resolve().parents[1] / "frontend/public/favicon.svg"


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
                published="2026-09-04T06:30:00+00:00",
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
                "Den Haag",
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
def dist(tmp_path: Path) -> Path:
    """A stand-in for `npm run build`: a shell and one hashed asset."""
    root = tmp_path / "dist"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text('<!doctype html><div id="root"></div>' + " " * 2048)
    (root / "assets" / "index-abc123.js").write_text("console.log(1);" * 200)
    (root / "favicon.svg").write_text("<svg/>")
    return root


@pytest.fixture
def client(cache: Path, dist: Path) -> Iterator[FlaskClient]:
    app = create_app(cache, dist)
    app.config["TESTING"] = True
    with app.test_client() as test_client:
        yield test_client


def index_rows(client: FlaskClient, offering: str = "buy") -> dict[int, dict[str, Any]]:
    payload = client.get(f"/api/index/{offering}").get_json()
    columns = payload["columns"]
    return {row[0]: dict(zip(columns, row, strict=True)) for row in payload["rows"]}


# --- the index -------------------------------------------------------------


def test_the_index_holds_every_house_of_one_offering(client: FlaskClient) -> None:
    payload = client.get("/api/index/buy").get_json()
    assert payload["offering"] == "buy"
    assert payload["columns"] == list(db.INDEX_COLUMNS)
    assert sorted(index_rows(client)) == [1, 2, 3]


def test_the_index_is_scoped_to_one_offering(client: FlaskClient) -> None:
    # Price is euros to buy and euros per month to rent; one index must never
    # hold both, or a sort would cut across two scales.
    assert index_rows(client, "rent") == {}


def test_an_unknown_offering_is_a_404(client: FlaskClient) -> None:
    assert client.get("/api/index/lease").status_code == 404


def test_the_index_carries_card_extras(client: FlaskClient) -> None:
    rows = index_rows(client)
    assert rows[1]["first_image"] == "tiara/a"
    assert rows[3]["price_was"] == 300_000  # came down to 275 000
    assert rows[2]["price_was"] is None


def test_times_travel_as_epoch_seconds(client: FlaskClient) -> None:
    rows = index_rows(client)
    assert rows[1]["published"] == 1_788_503_400  # 2026-09-04T06:30:00Z
    assert isinstance(rows[1]["first_seen_at"], int)


def test_delisting_travels_as_a_flag(cache: Path, client: FlaskClient) -> None:
    conn = db.connect(cache)
    with conn:
        conn.execute(
            "UPDATE listings SET delisted_at = '2026-05-01' WHERE listing_id = 2"
        )
    conn.close()
    rows = index_rows(client)
    assert rows[2]["delisted_at"] == 1
    assert rows[1]["delisted_at"] == 0


def test_the_index_carries_the_map_scale_and_counts(client: FlaskClient) -> None:
    meta = client.get("/api/index/buy").get_json()["meta"]
    assert meta["counts"]["total"] == 3
    assert meta["counts"]["boundaries"] == 1
    assert meta["hood_scale"] == [3929] * 6


def test_the_index_is_gzipped_when_asked(client: FlaskClient) -> None:
    response = client.get("/api/index/buy", headers={"Accept-Encoding": "gzip"})
    assert response.headers["Content-Encoding"] == "gzip"
    assert json.loads(gzip.decompress(response.data))["offering"] == "buy"


def test_a_warm_client_gets_a_304(client: FlaskClient) -> None:
    first = client.get("/api/index/buy")
    assert first.headers["Cache-Control"] == "no-cache"
    again = client.get("/api/index/buy", headers={"If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304
    assert again.data == b""


def test_a_write_changes_the_etag(cache: Path, client: FlaskClient) -> None:
    # The whole point of the in-process cache is that it is never stale: the
    # next fetch has to reach a client that revalidates.
    before = client.get("/api/index/buy").headers["ETag"]
    conn = db.connect(cache)
    with conn:
        db.upsert_listing(conn, make_listing(listing_id=4, address="Dijkweg 4"))
    conn.close()
    after = client.get("/api/index/buy", headers={"If-None-Match": before})
    assert after.status_code == 200
    assert 4 in index_rows(client)


# --- search ----------------------------------------------------------------


def test_search_reaches_descriptions(client: FlaskClient) -> None:
    # Descriptions are not in the index, so this is the only way to them.
    assert client.get("/api/search/buy?q=licht").get_json()["ids"] == [1]


def test_search_matches_addresses_too(client: FlaskClient) -> None:
    assert client.get("/api/search/buy?q=beeklaan").get_json()["ids"] == [2]


def test_an_empty_search_matches_nothing(client: FlaskClient) -> None:
    assert client.get("/api/search/buy?q=%20").get_json()["ids"] == []


def test_search_is_scoped_to_one_offering(client: FlaskClient) -> None:
    assert client.get("/api/search/rent?q=licht").get_json()["ids"] == []


# --- one house -------------------------------------------------------------


def test_a_house_carries_its_detail(client: FlaskClient) -> None:
    payload = client.get("/api/house/1").get_json()
    assert payload["listing"]["address"] == "Aaastraat 1"
    assert payload["listing"]["description"] == "Ruim en licht appartement."
    assert payload["features"] == [
        {"group": "Bouw", "label": "Bouwjaar", "value": "1931-1944"}
    ]


def test_photos_are_cdn_ids_not_urls(client: FlaskClient) -> None:
    # Hotlinked, never downloaded: the browser builds the CDN URL itself.
    assert client.get("/api/house/1").get_json()["photos"] == ["tiara/a", "tiara/b"]


def test_price_history_keeps_its_surrogate_key(client: FlaskClient) -> None:
    history = client.get("/api/house/3").get_json()["history"]
    assert [row["price"] for row in history] == [300_000, 275_000]
    assert len({row["id"] for row in history}) == 2


def test_an_unknown_house_is_a_404(client: FlaskClient) -> None:
    assert client.get("/api/house/999").status_code == 404


# --- the buurt overlay -----------------------------------------------------


def test_the_outline_feed_is_geojson_with_the_price_level(client: FlaskClient) -> None:
    payload = client.get("/api/neighbourhoods.geojson").get_json()
    assert payload["type"] == "FeatureCollection"
    feature = payload["features"][0]
    assert feature["geometry"]["type"] == "Polygon"
    assert feature["properties"] == {"name": "Spoorwijk", "price_m2": 3929}
    # The id is what MapLibre feature-state hover keys on.
    assert feature["id"] == 0


def test_the_outline_feed_ignores_delisting(cache: Path, client: FlaskClient) -> None:
    # Geography, not data: outlines must not blink out as houses come and go.
    conn = db.connect(cache)
    with conn:
        conn.execute("UPDATE listings SET delisted_at = '2026-05-01'")
    conn.close()
    assert len(client.get("/api/neighbourhoods.geojson").get_json()["features"]) == 1


# --- the app shell ---------------------------------------------------------


@pytest.mark.parametrize("path", ["/", "/house/1", "/house/999", "/anything/else"])
def test_every_client_route_gets_the_shell(client: FlaskClient, path: str) -> None:
    # A reload or a shared link on any client-side route must land on the app.
    response = client.get(path)
    assert response.status_code == 200
    assert b'id="root"' in response.data
    assert response.headers["Cache-Control"] == "no-cache"


def test_filters_in_the_query_string_still_get_the_shell(client: FlaskClient) -> None:
    assert client.get("/?label=A&label=B&view=map").status_code == 200


def test_hashed_assets_are_cached_for_good(client: FlaskClient) -> None:
    response = client.get("/assets/index-abc123.js")
    assert response.status_code == 200
    assert "immutable" in response.headers["Cache-Control"]
    assert response.mimetype in {"text/javascript", "application/javascript"}


def test_assets_are_gzipped_when_asked(client: FlaskClient) -> None:
    response = client.get("/assets/index-abc123.js", headers={"Accept-Encoding": "gzip"})
    assert response.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(response.data).startswith(b"console.log")


def test_a_stale_asset_is_a_404_not_the_shell(client: FlaskClient) -> None:
    # An old tab asking for last build's chunk must fail loudly, not be handed
    # HTML that then fails to parse as JavaScript.
    assert client.get("/assets/index-old999.js").status_code == 404


def test_an_unknown_api_path_is_a_json_404(client: FlaskClient) -> None:
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert response.get_json() == {"error": "not found"}


def test_the_shell_cannot_be_walked_out_of(client: FlaskClient, cache: Path) -> None:
    response = client.get("/../test.db")
    assert b"SQLite" not in response.data


def test_an_unbuilt_app_says_how_to_build_it(cache: Path, tmp_path: Path) -> None:
    app = create_app(cache, tmp_path / "nowhere")
    response = app.test_client().get("/")
    assert response.status_code == 503
    assert b"npm run build" in response.data


def test_a_relative_db_path_is_resolved(
    cache: Path, dist: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Flask resolves a relative path against its own package directory, so
    # `serve` run from a project root would look for the cache in the wrong
    # place entirely.
    monkeypatch.chdir(cache.parent)
    app = create_app(Path("test.db"), dist)
    app.config["TESTING"] = True
    assert app.config["DB_PATH"].is_absolute()
    with app.test_client() as relative_client:
        assert relative_client.get("/api/index/buy").status_code == 200


# --- favicon ---------------------------------------------------------------


def test_the_favicon_is_well_formed_xml() -> None:
    """An invalid SVG favicon fails silently as a broken image.

    The first version shipped a double hyphen inside an XML comment, which is
    illegal and which nothing warns about until you look at a tab.
    """
    # S314 is about untrusted input; this file is in the repository.
    ElementTree.parse(FAVICON)  # noqa: S314 - raises ParseError if malformed
    assert "--" not in FAVICON.read_text(encoding="utf-8").split("<style>")[0].replace(
        "<!--", ""
    ).replace("-->", "")


def test_the_favicon_is_served(client: FlaskClient) -> None:
    response = client.get("/favicon.svg")
    assert response.status_code == 200
    assert b"<svg" in response.data
