"""Domain records shared by the extraction and rendering layers."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

PAGE_SIZE = 15
"""Results per funda search page. Not configurable server-side."""


@dataclass(frozen=True, slots=True)
class Listing:
    """The fields worth comparing houses on, flattened out of the SSR payload."""

    listing_id: int | None
    address: str
    postal_code: str
    city: str
    neighbourhood: str
    price: int | None
    price_condition: str
    living_area: int | None
    rooms: int | None
    bedrooms: int | None
    energy_label: str
    object_type: str
    construction_type: str
    status: str
    published: str
    agent: str
    url: str
    photo_ids: tuple[str, ...] = ()
    """CDN paths for every photo, in funda's order. Verified to be the full set."""

    @property
    def photo_count(self) -> int:
        return len(self.photo_ids)

    @property
    def price_per_m2(self) -> int | None:
        """None rather than a crash when either side is missing or zero."""
        if not self.price or not self.living_area:
            return None
        return round(self.price / self.living_area)

    def as_dict(self) -> dict[str, Any]:
        """Flat serialisable form for json/csv output.

        `photo_ids` is dropped in favour of its count -- a list of 39 CDN paths
        is noise in a spreadsheet, and the pipeline reads the field directly.
        """
        data = asdict(self)
        data.pop("photo_ids", None)
        data["photo_count"] = self.photo_count
        data["price_per_m2"] = self.price_per_m2
        return data


@dataclass(frozen=True, slots=True)
class SearchPage:
    """One page of search results plus the totals reported alongside it."""

    page: int
    total_results: int
    listings: list[Listing]

    @property
    def total_pages(self) -> int:
        return -(-self.total_results // PAGE_SIZE)  # ceiling division


@dataclass(frozen=True, slots=True)
class Feature:
    """One row of a funda *kenmerken* table, e.g. 'Bouwjaar' / '1931-1944'."""

    group_id: str
    group_title: str
    position: int
    label: str
    value: str


@dataclass(frozen=True, slots=True)
class Detail:
    """The extra fields a listing's own page carries beyond the search result."""

    listing_id: int
    description: str
    lat: float | None
    lng: float | None
    neighbourhood_price_m2: int | None
    neighbourhood_inhabitants: int | None
    features: tuple[Feature, ...]


@dataclass(frozen=True, slots=True)
class Boundary:
    """One neighbourhood outline, as funda draws it.

    `geometry` is a serialised GeoJSON geometry (Polygon or MultiPolygon) ready
    to drop straight into a FeatureCollection -- the web layer never has to know
    how funda shaped it.
    """

    name: str
    slug: str
    geometry: str


@dataclass(slots=True)
class FetchReport:
    """Counts for one `fetch` run. Mutable: the pipeline accumulates into it."""

    search_url: str
    pages_read: int = 0
    seen: int = 0
    new_listings: int = 0
    price_changes: int = 0
    status_changes: int = 0
    delisted: int = 0
    details_fetched: int = 0
    boundaries_fetched: int = 0
    complete: bool = False
    """True only when every search page was read. Gates delisted-marking."""
    error: str | None = None
