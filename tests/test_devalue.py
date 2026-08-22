"""Tests for the Nuxt devalue decoder.

The format's defining trait is that integers inside containers are *indices*,
not values, so most of these cases are about reference resolution: sharing,
cycles, sentinels and tagged wrappers.
"""

from __future__ import annotations

import math

import pytest

from housemaster.devalue import parse


def test_accepts_json_text_and_parsed_list() -> None:
    assert parse('["hello"]') == "hello"
    assert parse(["hello"]) == "hello"


def test_resolves_object_field_references() -> None:
    # Slot 0 is the root; its values point at slots 1 and 2.
    payload = [{"name": 1, "price": 2}, "Camera Obscurastraat", 289500]
    assert parse(payload) == {"name": "Camera Obscurastraat", "price": 289500}


def test_a_referenced_slot_holding_an_integer_is_a_literal() -> None:
    # Only integers *inside a container* are references. Once a lookup lands on
    # a slot, an integer there is the value -- it is not a further hop.
    assert parse([{"page": 1}, 2]) == {"page": 2}


def test_array_elements_are_indices_not_values() -> None:
    payload = [[1, 2, 1], "a", "b"]
    assert parse(payload) == ["a", "b", "a"]


def test_shared_reference_appears_in_both_places() -> None:
    payload = [{"left": 1, "right": 1}, {"label": 2}, "shared"]
    result = parse(payload)
    assert result == {"left": {"label": "shared"}, "right": {"label": "shared"}}


def test_nested_containers() -> None:
    payload = [{"listings": 1}, [2], {"id": 3}, 42]
    assert parse(payload) == {"listings": [{"id": 42}]}


@pytest.mark.parametrize(
    ("sentinel", "expected"),
    [(-1, None), (-2, None), (-6, -0.0)],
)
def test_sentinels_map_to_python_values(sentinel: int, expected: object) -> None:
    assert parse([{"value": sentinel}]) == {"value": expected}


def test_nan_and_infinity_sentinels() -> None:
    result = parse([{"nan": -3, "pos": -4, "neg": -5}])
    assert math.isnan(result["nan"])
    assert result["pos"] == math.inf
    assert result["neg"] == -math.inf


@pytest.mark.parametrize("tag", ["Ref", "Reactive", "ShallowRef", "ShallowReactive"])
def test_reactivity_wrappers_are_transparent(tag: str) -> None:
    payload = [{"state": 1}, [tag, 2], "unwrapped"]
    assert parse(payload) == {"state": "unwrapped"}


def test_empty_ref_becomes_none() -> None:
    assert parse([{"state": 1}, ["EmptyRef", 2], "ignored"]) == {"state": None}


def test_date_tag_yields_its_literal() -> None:
    payload = [{"published": 1}, ["Date", "2026-08-21T12:23:46.000Z"]]
    assert parse(payload) == {"published": "2026-08-21T12:23:46.000Z"}


def test_set_tag() -> None:
    payload = [["Set", 1, 2], "a", "b"]
    assert parse(payload) == ["a", "b"]


def test_map_tag() -> None:
    payload = [["Map", 1, 2, 3, 4], "city", "Den Haag", "label", "E"]
    assert parse(payload) == {"city": "Den Haag", "label": "E"}


def test_unknown_tag_is_preserved_rather_than_dropped() -> None:
    # Silently discarding an unrecognised tag would hide a format change.
    payload = [{"error": 1}, ["NuxtError", 2], "boom"]
    assert parse(payload) == {"error": {"__NuxtError": ["boom"]}}


def test_cycle_terminates() -> None:
    # Slot 1 refers back to slot 0; the back-edge must not recurse forever.
    payload = [{"child": 1}, {"parent": 0}]
    assert parse(payload) == {"child": {"parent": None}}


def test_string_first_element_is_a_tag_not_data() -> None:
    # A genuine array of strings is stored as indices, never as raw strings,
    # which is what makes the tag check unambiguous.
    payload = [[1, 2], "Set", "Map"]
    assert parse(payload) == ["Set", "Map"]
