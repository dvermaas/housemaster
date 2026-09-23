"""Tests for fetching and extraction.

Nothing here touches the network except the test marked `network`, which is
skipped unless HOUSEMASTER_NETWORK_TESTS is set.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from housemaster import funda
from housemaster.funda import (
    BlockedError,
    PayloadError,
    extract_state,
    fetch_html,
    fetch_search_page,
    iter_all_listings,
    search_url,
    to_detail,
    to_listing,
    to_search_page,
)
from housemaster.models import Listing, SearchPage

from .conftest import wrap_payload


class FakeResponse:
    def __init__(self, text: str) -> None:
        self.text = text

    def raise_for_status(self) -> None:
        return None


@pytest.fixture
def captured_get(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Replace curl_cffi's get, recording how it was called."""
    calls: dict[str, Any] = {}

    def fake_get(url: str, **kwargs: Any) -> FakeResponse:
        calls["url"] = url
        calls["kwargs"] = kwargs
        return FakeResponse(calls.get("body", ""))

    monkeypatch.setattr("housemaster.net.requests.get", fake_get)
    return calls


# --- search_url ------------------------------------------------------------


@pytest.mark.parametrize("page", [1, 0, -3])
def test_first_page_url_is_unchanged(page: int) -> None:
    base = "https://www.funda.nl/zoeken/koop?selected_area=den-haag"
    assert search_url(base, page) == base


def test_pagination_appends_to_an_existing_query() -> None:
    base = "https://www.funda.nl/zoeken/koop?selected_area=den-haag"
    assert search_url(base, 2) == f"{base}&search_result=2"


def test_pagination_starts_a_query_when_there_is_none() -> None:
    base = "https://www.funda.nl/zoeken/koop"
    assert search_url(base, 7) == f"{base}?search_result=7"


# --- fetch_html ------------------------------------------------------------


def test_fetch_html_returns_body_when_payload_present(
    captured_get: dict[str, Any],
) -> None:
    captured_get["body"] = wrap_payload(["ok"])
    assert "__NUXT_DATA__" in fetch_html("https://www.funda.nl/zoeken/koop")


def test_fetch_html_impersonates_a_browser(captured_get: dict[str, Any]) -> None:
    # Passing the bot wall depends entirely on the TLS fingerprint.
    captured_get["body"] = wrap_payload(["ok"])
    fetch_html("https://www.funda.nl/zoeken/koop")
    assert captured_get["kwargs"]["impersonate"] == "chrome"


def test_interstitial_is_rejected_despite_http_200(captured_get: dict[str, Any]) -> None:
    # Akamai serves its block page with a 200, so only the body can tell us.
    captured_get["body"] = (
        "<html><head><title>Je bent bijna op de pagina die je zoekt [funda]</title>"
        "</head><body>captcha</body></html>"
    )
    with pytest.raises(BlockedError, match="Akamai"):
        fetch_html("https://www.funda.nl/zoeken/koop")


# --- extract_state ---------------------------------------------------------


def test_extract_state_decodes_the_embedded_payload() -> None:
    html = wrap_payload([{"page": 1}, 15])
    assert extract_state(html) == {"page": 15}


def test_extract_state_rejects_a_page_without_the_script_tag() -> None:
    with pytest.raises(PayloadError, match="script tag"):
        extract_state("<html><body>nothing here</body></html>")


# --- to_listing ------------------------------------------------------------


def test_to_listing_maps_every_field(raw_listing: dict[str, Any]) -> None:
    listing = to_listing(raw_listing)
    assert listing == Listing(
        listing_id=8116828,
        address="Camera Obscurastraat 253",
        postal_code="2524TE",
        city="Den Haag",
        neighbourhood="Spoorwijk",
        price=289500,
        price_condition="kosten_koper",
        living_area=84,
        rooms=6,
        bedrooms=5,
        energy_label="E",
        object_type="apartment",
        construction_type="resale",
        status="none",
        published="2026-08-21T10:23:46+00:00",
        agent="Elzenaar NVM Makelaars & Hypotheken",
        url=(
            "https://www.funda.nl/detail/koop/den-haag"
            "/appartement-camera-obscurastraat-253/44561281/"
        ),
        photo_ids=("a", "b", "c"),
    )


def test_scalar_fields_arrive_wrapped_in_lists(raw_listing: dict[str, Any]) -> None:
    assert to_listing(raw_listing).living_area == 84


def test_empty_list_fields_become_none(raw_listing: dict[str, Any]) -> None:
    raw_listing["floor_area"] = []
    raw_listing["price"]["selling_price"] = []
    listing = to_listing(raw_listing)
    assert listing.living_area is None
    assert listing.price is None


def test_missing_fields_do_not_raise() -> None:
    listing = to_listing({})
    assert listing.listing_id is None
    assert listing.address == ""
    assert listing.energy_label == "?"
    assert listing.url == ""
    assert listing.photo_count == 0


def test_listing_without_an_agent(raw_listing: dict[str, Any]) -> None:
    raw_listing["agent"] = []
    assert to_listing(raw_listing).agent == ""


def test_publish_time_is_kept_to_the_second_in_utc(raw_listing: dict[str, Any]) -> None:
    # 12:23:46 in Amsterdam (CEST, +02:00) is 10:23:46 UTC. The clock time is the
    # point: agents' feeds stamp many listings on the hour.
    assert to_listing(raw_listing).published == "2026-08-21T10:23:46+00:00"


def test_publish_time_normalises_across_the_dst_offset(
    raw_listing: dict[str, Any],
) -> None:
    # Winter is +01:00, so the same wall-clock time is a different UTC instant.
    # UTC is what keeps text order chronological across the change.
    raw_listing["publish_date"] = "2026-01-15T08:00:03.2687499+01:00"
    assert to_listing(raw_listing).published == "2026-01-15T07:00:03+00:00"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ""),
        ("", ""),
        ("2026-08-21", "2026-08-21"),  # a bare date has no offset: kept as is
        ("2026-08-21T12:23:46", "2026-08-21"),  # naive: no instant to convert
        ("not a date", "not a date"),
    ],
)
def test_publish_time_falls_back_to_the_date(
    raw_listing: dict[str, Any], raw: str | None, expected: str
) -> None:
    raw_listing["publish_date"] = raw
    assert to_listing(raw_listing).published == expected


def test_house_number_may_carry_a_suffix(raw_listing: dict[str, Any]) -> None:
    raw_listing["address"]["house_number"] = "18 A"
    assert to_listing(raw_listing).address == "Camera Obscurastraat 18 A"


def test_photo_ids_are_kept_not_just_counted(raw_listing: dict[str, Any]) -> None:
    # The search page carries every photo id, so keeping them means photos need
    # no detail request at all.
    listing = to_listing(raw_listing)
    assert listing.photo_ids == ("a", "b", "c")
    assert listing.photo_count == 3


def test_as_dict_reports_photo_count_not_the_id_list() -> None:
    # 39 CDN paths would be noise in a CSV column.
    data = to_listing({"photo_image_id": ["a", "b"]}).as_dict()
    assert data["photo_count"] == 2
    assert "photo_ids" not in data


# --- to_detail -------------------------------------------------------------


def test_to_detail_reads_the_listing_store(detail_state: dict[str, Any]) -> None:
    detail = to_detail(detail_state)
    assert detail.listing_id == 8116828
    assert detail.description.startswith("Spoorwijk.")
    assert (detail.lat, detail.lng) == (52.050217, 4.3105555)


def test_to_detail_finds_local_insights_by_prefix(detail_state: dict[str, Any]) -> None:
    # The key is `localInsights-den-haag/spoorwijk` -- it cannot be looked up
    # directly without reimplementing funda's slug rules.
    detail = to_detail(detail_state)
    assert detail.neighbourhood_price_m2 == 3929
    assert detail.neighbourhood_inhabitants == 3820


def test_to_detail_flattens_kenmerken_groups(detail_state: dict[str, Any]) -> None:
    detail = to_detail(detail_state)
    assert len(detail.features) == 3
    first = detail.features[0]
    assert (first.group_id, first.group_title) == ("overdracht", "Overdracht")
    assert (first.label, first.position) == ("Vraagprijs", 0)
    # Position restarts within each group, so (group_id, position) is the key.
    assert [(f.group_id, f.position) for f in detail.features] == [
        ("overdracht", 0),
        ("overdracht", 1),
        ("bouw", 0),
    ]


def test_to_detail_finds_the_listing_by_prefix(detail_state: dict[str, Any]) -> None:
    # funda suffixed the key with the tinyId on 2026-09-09; before that it was
    # the bare `cachedListingData_nl`. Both must keep working.
    data = detail_state["data"]
    bare = {"data": {"cachedListingData_nl": data.pop("cachedListingData_nl_80955639")}}
    assert to_detail(bare).listing_id == 8116828


def test_to_detail_tolerates_a_listing_with_no_insights_block() -> None:
    detail = to_detail({"data": {"cachedListingData_nl_1": {"globalId": 1}}})
    assert detail.neighbourhood_price_m2 is None
    assert detail.features == ()
    assert detail.description == ""


@pytest.mark.parametrize("state", [{}, {"data": {}}, None])
def test_to_detail_rejects_an_unexpected_shape(state: Any) -> None:
    with pytest.raises(PayloadError):
        to_detail(state)


# --- to_search_page --------------------------------------------------------


def test_to_search_page_reads_the_store(search_state: dict[str, Any]) -> None:
    page = to_search_page(search_state)
    assert page.page == 1
    assert page.total_results == 521
    assert page.total_pages == 35
    assert len(page.listings) == 1


@pytest.mark.parametrize(
    "state",
    [{}, {"pinia": {}}, {"pinia": {"search": {}}}, None],
)
def test_unexpected_state_shape_raises_payload_error(state: Any) -> None:
    with pytest.raises(PayloadError):
        to_search_page(state)


# --- page walking ----------------------------------------------------------


@pytest.fixture
def paged_source(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Serve 3 pages of one listing each, recording the pages requested."""
    requested: list[int] = []

    def fake_fetch(base_url: str, page: int = 1) -> SearchPage:
        requested.append(page)
        listing = to_listing({"id": page, "object_type": "apartment"})
        return SearchPage(page=page, total_results=45, listings=[listing])

    monkeypatch.setattr(funda, "fetch_search_page", fake_fetch)
    return requested


def test_iter_all_listings_walks_every_page_in_order(paged_source: list[int]) -> None:
    listings = list(iter_all_listings("https://example.test/zoeken"))
    assert paged_source == [1, 2, 3]
    assert [x.listing_id for x in listings] == [1, 2, 3]


def test_max_pages_caps_the_walk(paged_source: list[int]) -> None:
    listings = list(iter_all_listings("https://example.test/zoeken", max_pages=2))
    assert paged_source == [1, 2]
    assert len(listings) == 2


def test_max_pages_above_the_total_does_not_overfetch(paged_source: list[int]) -> None:
    list(iter_all_listings("https://example.test/zoeken", max_pages=99))
    assert paged_source == [1, 2, 3]


def test_fetch_search_page_end_to_end(
    monkeypatch: pytest.MonkeyPatch, search_state: dict[str, Any]
) -> None:
    monkeypatch.setattr(funda, "fetch_html", lambda *_args, **_kwargs: "<html>")
    monkeypatch.setattr(funda, "extract_state", lambda *_args: search_state)
    page = fetch_search_page("https://example.test/zoeken", 1)
    assert page.listings[0].city == "Den Haag"


# --- live site -------------------------------------------------------------


@pytest.mark.network
def test_target_search_is_still_reachable() -> None:
    """Canary: catches funda changing its payload shape or tightening Akamai."""
    page = fetch_search_page(funda.DEFAULT_SEARCH_URL, 1)
    assert len(page.listings) == funda.PAGE_SIZE
    assert page.total_results > 0
    assert all(x.url.startswith("https://www.funda.nl/detail/") for x in page.listings)


# --- neighbourhood outlines ------------------------------------------------


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Bezuidenhout-Oost", "bezuidenhout-oost"),
        ("Groente- en Fruitmarkt", "groente-en-fruitmarkt"),
        ("Zijden, Steden en Zichten", "zijden-steden-en-zichten"),
        # Periods are deleted, not hyphenated: funda writes `e.o.` as `eo`.
        ("Koningsplein e.o.", "koningsplein-eo"),
        ("Van Hoytemastraat e.o.", "van-hoytemastraat-eo"),
    ],
)
def test_neighbourhood_slug(name: str, expected: str) -> None:
    assert funda.neighbourhood_slug(name) == expected


def _area_state(area: dict[str, Any] | None) -> dict[str, Any]:
    return {"pinia": {"search": {"criteria": {"selected_area": [area] if area else []}}}}


def test_a_single_ring_becomes_a_polygon() -> None:
    ring = [[[4.1, 52.0], [4.2, 52.0], [4.2, 52.1], [4.1, 52.0]]]
    boundary = funda.to_boundary(
        _area_state(
            {
                "areaType": "neighborhood",
                "name": "Spoorwijk",
                "geographicalArea": {"0": {"type": "polygon", "coordinates": ring}},
            }
        ),
        "Den Haag",
        "Spoorwijk",
        "spoorwijk",
    )
    assert boundary is not None
    assert json.loads(boundary.geometry) == {"type": "Polygon", "coordinates": ring}


def test_several_rings_become_a_multipolygon() -> None:
    # Den Haag has buurten split by water, so this really happens.
    a = [[[4.1, 52.0], [4.2, 52.0], [4.2, 52.1], [4.1, 52.0]]]
    b = [[[4.3, 52.0], [4.4, 52.0], [4.4, 52.1], [4.3, 52.0]]]
    boundary = funda.to_boundary(
        _area_state(
            {
                "areaType": "neighborhood",
                "name": "Laak",
                "geographicalArea": {"0": {"coordinates": a}, "1": {"coordinates": b}},
            }
        ),
        "Den Haag",
        "Laak",
        "laak",
    )
    assert boundary is not None
    assert json.loads(boundary.geometry) == {
        "type": "MultiPolygon",
        "coordinates": [a, b],
    }


def test_a_slug_that_missed_returns_none_rather_than_raising() -> None:
    # funda answers an unresolvable buurt slug with the *city* search, not a
    # 404 -- so the areaType is the only way to tell, and a miss is normal.
    assert (
        funda.to_boundary(
            _area_state({"areaType": "city", "name": "Den Haag"}),
            "Den Haag",
            "Nowhere",
            "nowhere",
        )
        is None
    )
    assert funda.to_boundary(_area_state(None), "Den Haag", "Nowhere", "nowhere") is None


# --- buy vs rent -----------------------------------------------------------


def test_a_purchase_reads_the_selling_price() -> None:
    listing = to_listing(
        {
            "offering_type": ["buy"],
            "price": {
                "selling_price": [575000],
                "selling_price_condition": "kosten_koper",
            },
        }
    )
    assert (listing.offering_type, listing.price) == ("buy", 575000)
    assert listing.price_condition == "kosten_koper"


def test_a_rental_reads_the_rent_price() -> None:
    # funda names the two cases differently, so reading `selling_price` off a
    # rental silently yields None -- which is what happened before this split.
    listing = to_listing(
        {
            "offering_type": ["rent"],
            "price": {"rent_price": [1725], "rent_price_condition": "per_month"},
        }
    )
    assert (listing.offering_type, listing.price) == ("rent", 1725)
    assert listing.price_condition == "per_month"


def test_a_yearly_rent_is_normalised_to_monthly() -> None:
    """One listing in sixty is quoted per year (they are parking spaces).

    Leaving both scales in one column would make every rental comparison
    silently wrong; funda's own wording survives verbatim in the kenmerken.
    """
    listing = to_listing(
        {
            "offering_type": ["rent"],
            "price": {"rent_price": [18000], "rent_price_condition": "per_year"},
        }
    )
    assert listing.price == 1500
    assert listing.price_condition == "per_month"


def test_offering_type_defaults_to_buy() -> None:
    # An absent or unfamiliar offering_type must not be guessed as rent.
    assert to_listing({}).offering_type == "buy"
    assert to_listing({"offering_type": []}).offering_type == "buy"


def test_a_city_slug_follows_the_same_rule_as_a_buurt_slug() -> None:
    # funda writes Rijswijk (ZH) as rijswijk-zh, which the buurt rule already
    # produces -- so fetch_boundary can slugify both halves the same way.
    assert funda.neighbourhood_slug("Rijswijk (ZH)") == "rijswijk-zh"
    assert funda.neighbourhood_slug("Den Haag") == "den-haag"
    assert funda.neighbourhood_slug("Voorburg") == "voorburg"
