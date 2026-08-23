"""Fetching and field extraction for funda.nl.

Funda server-renders every page and ships the full state as JSON, so there is no
HTML parsing here -- we pull `__NUXT_DATA__` out of the markup and decode it.

Akamai gates the site on the TLS fingerprint, which is why every request goes
through curl_cffi with `impersonate=`. A blocked request still returns HTTP 200
with an interstitial page, so `fetch_html` validates the body instead.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterator
from typing import Any

from housemaster import net
from housemaster.devalue import parse
from housemaster.models import (
    PAGE_SIZE,
    Boundary,
    Detail,
    Feature,
    Listing,
    SearchPage,
)

BASE_URL = "https://www.funda.nl"
DEFAULT_SEARCH_URL = (
    "https://www.funda.nl/zoeken/koop"
    "?selected_area=den-haag,rijswijk-zh,voorburg&floor_area=50-"
)
"""Only a default for the ad-hoc `search` probe. `fetch` walks the tracked
set instead, so this is a starting point rather than the project's scope."""
# Re-exported so callers keep importing them from here.
IMPERSONATE = net.IMPERSONATE
DEFAULT_TIMEOUT = net.DEFAULT_TIMEOUT

_NUXT_RE = re.compile(r'<script[^>]*id="__NUXT_DATA__"[^>]*>(.*?)</script>', re.DOTALL)


class FundaError(RuntimeError):
    """Base class for every failure this module raises."""


class BlockedError(FundaError):
    """Akamai served its interstitial instead of the real page."""


class PayloadError(FundaError):
    """The page loaded but did not carry the state we expect."""


def fetch_html(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    """Fetch a funda page, rejecting the bot-wall interstitial.

    Raises:
        BlockedError: the response lacks the SSR payload, so it is the Akamai
            interstitial -- which is served with HTTP 200, hence this check.
    """
    html = net.get_text(url, timeout=timeout)
    # The bot wall answers 200, so the status code proves nothing -- the SSR
    # payload only exists on a genuine page.
    if "__NUXT_DATA__" not in html:
        raise BlockedError(
            f"no __NUXT_DATA__ in {len(html)} bytes from {url} "
            "-- likely the Akamai interstitial"
        )
    return html


def extract_state(html: str) -> Any:
    """Decode the Nuxt SSR state embedded in a funda page."""
    match = _NUXT_RE.search(html)
    if not match:
        raise PayloadError("no __NUXT_DATA__ script tag found")
    return parse(match.group(1))


def search_url(base: str, page: int = 1) -> str:
    """Add funda's pagination parameter to a search URL."""
    if page <= 1:
        return base
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}search_result={page}"


def _first(value: Any) -> Any:
    """Several fields arrive as single-element lists (project listings vary)."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


MONTHS_PER_YEAR = 12

BUY, RENT = "buy", "rent"


def offering_type_of(raw: dict[str, Any]) -> str:
    """`buy` or `rent`, taken from the payload rather than from the search URL.

    Self-classifying is the point: the same physical property can be listed both
    ways, as two listings with two globalIds, and each one knows what it is.
    """
    return RENT if RENT in (raw.get("offering_type") or ()) else BUY


def _price(raw_price: dict[str, Any], offering: str) -> tuple[int | None, str]:
    """The asking price and its unit.

    funda names the two cases differently -- `selling_price` / `rent_price` --
    so this is the one place the difference lives. Rent is normalised to
    **monthly**: `rent_price_condition` is `per_month` for all but a handful of
    listings (parking spaces quoted `per_year`), and leaving both scales in one
    column would make every comparison silently wrong. The kenmerken keep
    funda's own wording verbatim, so nothing is lost by normalising here.
    """
    if offering == BUY:
        return (
            _first(raw_price.get("selling_price")),
            raw_price.get("selling_price_condition") or "",
        )

    amount = _first(raw_price.get("rent_price"))
    condition = raw_price.get("rent_price_condition") or ""
    if amount is not None and condition == "per_year":
        return round(amount / MONTHS_PER_YEAR), "per_month"
    return amount, condition


def to_listing(raw: dict[str, Any]) -> Listing:
    """Flatten one raw search-result object into a `Listing`."""
    address = raw.get("address") or {}
    price = raw.get("price") or {}
    agents = raw.get("agent") or []
    relative_url = raw.get("object_detail_page_relative_url") or ""

    street = " ".join(
        part for part in (address.get("street_name"), address.get("house_number")) if part
    )
    offering = offering_type_of(raw)
    amount, condition = _price(price, offering)

    return Listing(
        listing_id=raw.get("id"),
        address=street,
        postal_code=address.get("postal_code") or "",
        city=address.get("city") or "",
        neighbourhood=address.get("neighbourhood") or "",
        price=amount,
        price_condition=condition,
        living_area=_first(raw.get("floor_area")),
        rooms=raw.get("number_of_rooms"),
        bedrooms=raw.get("number_of_bedrooms"),
        energy_label=raw.get("energy_label") or "?",
        object_type=raw.get("object_type") or "",
        construction_type=raw.get("construction_type") or "",
        status=raw.get("status") or "",
        published=(raw.get("publish_date") or "")[:10],
        agent=(agents[0].get("name", "").strip() if agents else ""),
        url=f"{BASE_URL}{relative_url}" if relative_url else "",
        offering_type=offering,
        # Search results carry every photo id, in order -- no detail request is
        # needed for photos. See photos.photo_url for how these become URLs.
        photo_ids=tuple(raw.get("photo_image_id") or ()),
    )


def to_search_page(state: Any) -> SearchPage:
    """Read the search store out of a decoded SSR state tree."""
    try:
        search = state["pinia"]["search"]
        listings = search["listings"]
        total = search["totalListingsCount"]
        page = search["criteria"]["page"]
    except (KeyError, TypeError) as exc:
        raise PayloadError(f"unexpected search state shape: {exc}") from exc

    return SearchPage(
        page=page,
        total_results=total,
        listings=[to_listing(raw) for raw in listings],
    )


def _local_insights(state: dict[str, Any]) -> dict[str, Any]:
    """Find the neighbourhood block, whose key embeds the city and neighbourhood.

    The key looks like `localInsights-den-haag/spoorwijk`, so it cannot be
    looked up directly without reconstructing funda's slug rules.
    """
    data = state.get("data") or {}
    for key, value in data.items():
        if key.startswith("localInsights-") and isinstance(value, dict):
            return value
    return {}


def to_detail(state: Any) -> Detail:
    """Read the listing store out of a decoded detail-page state tree."""
    try:
        listing = state["data"]["cachedListingData_nl"]
    except (KeyError, TypeError) as exc:
        raise PayloadError(f"unexpected detail state shape: {exc}") from exc

    coordinates = listing.get("coordinates") or {}
    insights = _local_insights(state)

    features = tuple(
        Feature(
            group_id=group.get("Id") or "",
            group_title=group.get("Title") or "",
            position=position,
            label=row.get("Label") or "",
            value=row.get("Value") or "",
        )
        for group in listing.get("features") or ()
        for position, row in enumerate(group.get("KenmerkenList") or ())
    )

    return Detail(
        listing_id=listing.get("globalId"),
        description=(listing.get("description") or {}).get("content") or "",
        lat=coordinates.get("lat"),
        lng=coordinates.get("lng"),
        neighbourhood_price_m2=insights.get("averageAskingPricePerM2"),
        neighbourhood_inhabitants=insights.get("inhabitants"),
        features=features,
    )


# --- neighbourhood outlines ------------------------------------------------

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def neighbourhood_slug(name: str) -> str:
    """funda's URL form of a buurt name.

    `Groente- en Fruitmarkt` becomes `groente-en-fruitmarkt`.

    Derived rather than looked up, because the search payload gives us buurt
    *names* on every listing but never their identifiers. Verified against
    deliberately awkward names -- accents, commas, hyphens and multi-word
    connectives all round-trip. It is not perfect (`Van Hoytemastraat e.o.`
    does not resolve), so callers must treat a miss as normal.
    """
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    # Periods and apostrophes are deleted, not hyphenated: funda writes
    # `Koningsplein e.o.` as `koningsplein-eo`, so `e.o.` collapses to one word.
    squashed = folded.lower().replace("'", "").replace(".", "")
    return _SLUG_STRIP.sub("-", squashed).strip("-")


def area_url(city_slug: str, buurt_slug: str) -> str:
    return f"{BASE_URL}/zoeken/koop?selected_area={city_slug}/{buurt_slug}"


def to_boundary(state: Any, city: str, name: str, slug: str) -> Boundary | None:
    """Pull the buurt outline out of a search page scoped to that buurt.

    funda echoes the resolved area back in `criteria.selected_area`, carrying a
    `geographicalArea` map of rings. One entry is a Polygon; several are a
    MultiPolygon -- Den Haag has buurten split by water, so both occur.

    Returns None when the slug did not resolve to a neighbourhood, which is a
    normal outcome rather than an error: funda answers with the unfiltered
    search rather than a 404.
    """
    try:
        selected = state["pinia"]["search"]["criteria"]["selected_area"]
    except KeyError, TypeError:
        return None
    if not selected:
        return None

    area = selected[0]
    if area.get("areaType") != "neighborhood":
        return None  # the slug missed; funda fell back to a wider area

    rings = [
        entry["coordinates"]
        for _, entry in sorted((area.get("geographicalArea") or {}).items())
        if entry.get("coordinates")
    ]
    if not rings:
        return None

    geometry = (
        {"type": "Polygon", "coordinates": rings[0]}
        if len(rings) == 1
        else {"type": "MultiPolygon", "coordinates": rings}
    )
    return Boundary(
        city=city,
        name=area.get("name") or name,
        slug=slug,
        # separators= keeps 100+ polygons from carrying a KB of whitespace each.
        geometry=json.dumps(geometry, separators=(",", ":")),
    )


def fetch_boundary(city: str, name: str) -> Boundary | None:
    """Fetch one buurt outline. None means the slug did not resolve.

    Both halves are slugified the same way, because funda's city identifiers
    follow the same rule as its buurt ones -- `Rijswijk (ZH)` is `rijswijk-zh`.
    A buurt only resolves under its own city: `den-haag/cromvliet` answers with
    the unfiltered search, not a neighbourhood.
    """
    slug = neighbourhood_slug(name)
    city_slug = neighbourhood_slug(city)
    if not slug or not city_slug:
        return None
    state = extract_state(fetch_html(area_url(city_slug, slug)))
    return to_boundary(state, city, name, slug)


def fetch_search_page(base_url: str, page: int = 1) -> SearchPage:
    """Fetch and decode one page of a funda search."""
    return to_search_page(extract_state(fetch_html(search_url(base_url, page))))


def fetch_detail(url: str) -> Detail:
    """Fetch and decode one listing's own page."""
    return to_detail(extract_state(fetch_html(url)))


def iter_all_listings(base_url: str, max_pages: int | None = None) -> Iterator[Listing]:
    """Walk every result page in order. Callers should pace this politely."""
    first = fetch_search_page(base_url, 1)
    yield from first.listings

    last_page = first.total_pages
    if max_pages is not None:
        last_page = min(last_page, max_pages)

    for page in range(2, last_page + 1):
        yield from fetch_search_page(base_url, page).listings


__all__ = [
    "BASE_URL",
    "DEFAULT_SEARCH_URL",
    "PAGE_SIZE",
    "BlockedError",
    "FundaError",
    "PayloadError",
    "extract_state",
    "fetch_detail",
    "fetch_html",
    "fetch_search_page",
    "iter_all_listings",
    "search_url",
    "to_detail",
    "to_listing",
    "to_search_page",
]
