"""Tests for fetching and extraction.

Nothing here touches the network except the test marked `network`, which is
skipped unless HOUSEMASTER_NETWORK_TESTS is set.
"""

from __future__ import annotations

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

    monkeypatch.setattr(funda.requests, "get", fake_get)
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
        published="2026-08-21",
        agent="Elzenaar NVM Makelaars & Hypotheken",
        url=(
            "https://www.funda.nl/detail/koop/den-haag"
            "/appartement-camera-obscurastraat-253/44561281/"
        ),
        photo_count=3,
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


def test_publish_timestamp_is_truncated_to_a_date(raw_listing: dict[str, Any]) -> None:
    assert to_listing(raw_listing).published == "2026-08-21"


def test_house_number_may_carry_a_suffix(raw_listing: dict[str, Any]) -> None:
    raw_listing["address"]["house_number"] = "18 A"
    assert to_listing(raw_listing).address == "Camera Obscurastraat 18 A"


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
