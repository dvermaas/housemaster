from __future__ import annotations

import pytest

from housemaster.models import Listing, SearchPage


def make_listing(**overrides: object) -> Listing:
    defaults: dict[str, object] = {
        "listing_id": 1,
        "address": "Teststraat 1",
        "postal_code": "1000AA",
        "city": "Den Haag",
        "neighbourhood": "Centrum",
        "price": 300_000,
        "price_condition": "kosten_koper",
        "living_area": 75,
        "rooms": 3,
        "bedrooms": 2,
        "energy_label": "C",
        "object_type": "apartment",
        "construction_type": "resale",
        "status": "none",
        "published": "2026-08-21",
        "agent": "Test Makelaars",
        "url": "https://www.funda.nl/detail/koop/den-haag/x/1/",
        "photo_ids": tuple(f"tiara-media/x/{i}" for i in range(10)),
    }
    return Listing(**{**defaults, **overrides})  # type: ignore[arg-type]


def test_price_per_m2_rounds() -> None:
    assert make_listing(price=289_500, living_area=84).price_per_m2 == 3446


@pytest.mark.parametrize(
    ("price", "living_area"),
    [(None, 75), (300_000, None), (0, 75), (300_000, 0)],
)
def test_price_per_m2_is_none_when_undefined(
    price: int | None, living_area: int | None
) -> None:
    # A zero area must not raise ZeroDivisionError.
    assert make_listing(price=price, living_area=living_area).price_per_m2 is None


def test_as_dict_materialises_the_derived_field() -> None:
    data = make_listing().as_dict()
    assert data["price_per_m2"] == 4000
    assert data["address"] == "Teststraat 1"


def test_listing_is_immutable() -> None:
    with pytest.raises(AttributeError):
        make_listing().price = 1  # type: ignore[misc]


@pytest.mark.parametrize(
    ("total", "expected"),
    [(0, 0), (1, 1), (15, 1), (16, 2), (521, 35), (525, 35)],
)
def test_total_pages_rounds_up(total: int, expected: int) -> None:
    assert SearchPage(page=1, total_results=total, listings=[]).total_pages == expected
