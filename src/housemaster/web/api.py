"""The JSON API's response shapes: the one definition both sides read.

`views.py` builds these. `frontend/src/lib/api.gen.ts` is generated from them
by `housemaster.web.typescript`:

    uv run python -m housemaster.web.typescript frontend/src/lib/api.gen.ts

and `tests/test_api_contract.py` fails when that file is stale, or when a real
response stops matching its shape -- so a renamed field breaks a test here and
`tsc` there, rather than rendering `undefined` in the browser.

Plain `TypedDict`s, no new dependency. The generator knows exactly the types
used here and refuses anything else.
"""

from __future__ import annotations

from typing import Any, Literal, TypedDict

Offering = Literal["buy", "rent"]

IndexColumn = Literal[
    "listing_id", "address", "postal_code", "city", "neighbourhood", "price",
    "price_per_m2", "living_area", "rooms", "bedrooms", "energy_label", "status",
    "agent", "published", "first_seen_at", "delisted_at", "lat", "lng",
    "first_image", "price_was",
]  # fmt: skip
"""`db.INDEX_COLUMNS`, as a type, so the browser's decoder can only ask for a
column that exists. A test keeps the two equal."""


# --- /api/index/<offering> -----------------------------------------------------


class Counts(TypedDict):
    total: int
    active: int
    enriched: int
    boundaries: int


class IndexMeta(TypedDict):
    counts: Counts
    last_run: int | None
    """Epoch seconds."""
    hood_scale: list[int] | None
    """Quantile edges for the buurt choropleth: (lo, b1..b4, hi)."""


class IndexPayload(TypedDict):
    offering: Offering
    columns: list[IndexColumn]
    rows: list[list[Any]]
    """Columnar: one array per house, in `columns` order."""
    meta: IndexMeta


# --- /api/search/<offering> ----------------------------------------------------


class SearchPayload(TypedDict):
    q: str
    ids: list[int]


# --- /api/house/<id> -----------------------------------------------------------


class Listing(TypedDict):
    """A `listings` row as stored, plus the two per-card extras."""

    listing_id: int
    tiny_id: str | None
    url: str
    address: str
    postal_code: str | None
    city: str | None
    neighbourhood: str | None
    price: int | None
    """Euros to buy, euros per month to rent."""
    price_condition: str | None
    living_area: int | None
    rooms: int | None
    bedrooms: int | None
    energy_label: str | None
    object_type: str | None
    construction_type: str | None
    status: str | None
    published: str | None
    agent: str | None
    photo_count: int
    price_per_m2: int | None
    description: str | None
    lat: float | None
    lng: float | None
    neighbourhood_price_m2: int | None
    neighbourhood_inhabitants: int | None
    detail_fetched_at: str | None
    first_seen_at: str
    last_seen_at: str
    missed_runs: int
    delisted_at: str | None
    """Stopped matching every tracked search. Not the same as sold."""
    offering_type: Offering
    first_image: str | None
    price_was: int | None


class Feature(TypedDict):
    group: str
    label: str
    value: str


class HistoryRow(TypedDict):
    id: int
    observed_at: str
    price: int | None
    status: str | None


class HousePayload(TypedDict):
    listing: Listing
    features: list[Feature]
    photos: list[str]
    """CDN image ids; `photoUrl` in the browser builds the URL."""
    history: list[HistoryRow]


# --- /api/neighbourhoods.geojson -------------------------------------------------


class ShapeProperties(TypedDict):
    name: str
    price_m2: int | None


class ShapeFeature(TypedDict):
    type: Literal["Feature"]
    id: int
    geometry: dict[str, Any]
    properties: ShapeProperties


class ShapesPayload(TypedDict):
    type: Literal["FeatureCollection"]
    features: list[ShapeFeature]


class ErrorPayload(TypedDict):
    error: str


EXPORTED: tuple[type, ...] = (
    Counts,
    IndexMeta,
    IndexPayload,
    SearchPayload,
    Listing,
    Feature,
    HistoryRow,
    HousePayload,
    ShapeProperties,
    ShapeFeature,
    ShapesPayload,
    ErrorPayload,
)
"""What the generator writes, in this order, after the aliases."""

ALIASES: dict[str, object] = {"Offering": Offering, "IndexColumn": IndexColumn}
