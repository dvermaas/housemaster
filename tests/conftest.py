"""Shared fixtures.

The raw listing fixture mirrors the shape funda actually returns, including the
quirks the extractor has to absorb: single-element lists for scalar fields, and
a `publish_date` carrying a full timestamp where only the date is wanted.
"""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

NETWORK_ENV_VAR = "HOUSEMASTER_NETWORK_TESTS"


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip `network` tests unless explicitly enabled, so the suite is offline."""
    if list(item.iter_markers(name="network")) and not os.environ.get(NETWORK_ENV_VAR):
        pytest.skip(f"set {NETWORK_ENV_VAR}=1 to run tests that hit funda.nl")


@pytest.fixture
def raw_listing() -> dict[str, Any]:
    return {
        "id": 8116828,
        "address": {
            "street_name": "Camera Obscurastraat",
            "house_number": "253",
            "postal_code": "2524TE",
            "city": "Den Haag",
            "neighbourhood": "Spoorwijk",
            "province": "Zuid-Holland",
        },
        "price": {
            "selling_price": [289500],
            "selling_price_condition": "kosten_koper",
        },
        "floor_area": [84],
        "number_of_rooms": 6,
        "number_of_bedrooms": 5,
        "energy_label": "E",
        "object_type": "apartment",
        "construction_type": "resale",
        "status": "none",
        "publish_date": "2026-08-21T12:23:46.8098511+02:00",
        "agent": [{"name": "Elzenaar NVM Makelaars & Hypotheken ", "id": 8172}],
        "object_detail_page_relative_url": (
            "/detail/koop/den-haag/appartement-camera-obscurastraat-253/44561281/"
        ),
        "photo_image_id": ["a", "b", "c"],
    }


@pytest.fixture
def search_state(raw_listing: dict[str, Any]) -> dict[str, Any]:
    return {
        "pinia": {
            "search": {
                "criteria": {"page": 1},
                "totalListingsCount": 521,
                "listings": [raw_listing],
            }
        }
    }


def wrap_payload(payload: list[Any]) -> str:
    """Embed a devalue payload the way Nuxt does, for extractor tests."""
    return (
        "<html><body>"
        '<script type="application/json" id="__NUXT_DATA__" data-ssr="true">'
        f"{json.dumps(payload)}"
        "</script></body></html>"
    )
