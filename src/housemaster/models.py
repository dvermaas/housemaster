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
    photo_count: int

    @property
    def price_per_m2(self) -> int | None:
        """None rather than a crash when either side is missing or zero."""
        if not self.price or not self.living_area:
            return None
        return round(self.price / self.living_area)

    def as_dict(self) -> dict[str, Any]:
        """Serialisable form, with the derived field materialised."""
        return {**asdict(self), "price_per_m2": self.price_per_m2}


@dataclass(frozen=True, slots=True)
class SearchPage:
    """One page of search results plus the totals reported alongside it."""

    page: int
    total_results: int
    listings: list[Listing]

    @property
    def total_pages(self) -> int:
        return -(-self.total_results // PAGE_SIZE)  # ceiling division
