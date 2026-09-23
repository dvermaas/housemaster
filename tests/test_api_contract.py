"""The API contract: real responses match `web.api`, and the TypeScript the
front end compiles against is generated from the current `web.api`.

Together these close the loop that used to be two hand-kept copies. Rename a
field in `views.py` without `api.py` and a test here fails; change `api.py`
without regenerating and the staleness test fails; regenerate and `tsc` shows
every place in the front end that read the old name.
"""

from __future__ import annotations

import sqlite3
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin, get_type_hints, is_typeddict

import pytest
from flask.testing import FlaskClient

from housemaster import db
from housemaster.web import api
from housemaster.web.typescript import typescript

from .test_web import cache, client, dist  # noqa: F401 - fixtures

GENERATED = Path(__file__).resolve().parents[1] / "frontend/src/lib/api.gen.ts"

# bool is an int to isinstance(), and never what an int field means.
SCALARS: dict[object, tuple[str, Callable[[object], bool]]] = {
    type(None): ("null", lambda v: v is None),
    bool: ("bool", lambda v: isinstance(v, bool)),
    int: ("int", lambda v: isinstance(v, int) and not isinstance(v, bool)),
    float: ("a number", lambda v: isinstance(v, int | float) and not isinstance(v, bool)),
    str: ("str", lambda v: isinstance(v, str)),
}


def mismatches(value: object, tp: object, path: str = "$") -> list[str]:
    """Where `value` departs from `tp`, as JSON paths. Empty means it conforms.

    Knows the same small set of types the generator does.
    """
    if tp is Any:
        return []
    if tp in SCALARS:
        name, ok = SCALARS[tp]
        return [] if ok(value) else [f"{path}: {value!r} is not {name}"]
    if isinstance(tp, type) and is_typeddict(tp):
        return _object_mismatches(value, tp, path)
    origin, args = get_origin(tp), get_args(tp)
    if origin is Literal:
        return [] if value in args else [f"{path}: {value!r} not one of {args}"]
    if origin in (Union, types.UnionType):
        if any(not mismatches(value, arg, path) for arg in args):
            return []
        return [f"{path}: {value!r} matches none of {args}"]
    if origin is list:
        if not isinstance(value, list):
            return [f"{path}: {type(value).__name__} is not a list"]
        return [
            p
            for i, item in enumerate(value)
            for p in mismatches(item, args[0], f"{path}[{i}]")
        ]
    if origin is dict:
        if not isinstance(value, dict):
            return [f"{path}: {type(value).__name__} is not an object"]
        return [
            p for k, v in value.items() for p in mismatches(v, args[1], f"{path}.{k}")
        ]
    raise TypeError(f"mismatches() does not know {tp!r}")


def _object_mismatches(value: object, typed: type, path: str) -> list[str]:
    """A `TypedDict`, key for key: an extra key is as much drift as a missing one."""
    if not isinstance(value, dict):
        return [f"{path}: {type(value).__name__} is not {typed.__name__}"]
    hints = get_type_hints(typed)
    problems = [f"{path}.{k}: missing" for k in hints.keys() - value.keys()]
    problems += [
        f"{path}.{k}: not in {typed.__name__}" for k in value.keys() - hints.keys()
    ]
    for key, field in hints.items():
        if key in value:
            problems += mismatches(value[key], field, f"{path}.{key}")
    return problems


def test_the_generated_typescript_is_current() -> None:
    assert GENERATED.read_text(encoding="utf-8") == typescript(), (
        "frontend/src/lib/api.gen.ts is stale; regenerate it with\n"
        "    uv run python -m housemaster.web.typescript frontend/src/lib/api.gen.ts"
    )


def test_index_columns_match_the_query() -> None:
    assert get_args(api.IndexColumn) == db.INDEX_COLUMNS


def test_listing_is_every_stored_column_plus_the_card_extras() -> None:
    conn = db.connect(":memory:")
    stored = [row[1] for row in conn.execute("PRAGMA table_xinfo(listings)")]
    assert list(get_type_hints(api.Listing)) == [*stored, "first_image", "price_was"]


@pytest.mark.parametrize("offering", ["buy", "rent"])
def test_the_index_matches_its_shape(client: FlaskClient, offering: str) -> None:  # noqa: F811
    payload = client.get(f"/api/index/{offering}").get_json()
    assert mismatches(payload, api.IndexPayload) == []
    assert tuple(payload["columns"]) == db.INDEX_COLUMNS


def test_search_matches_its_shape(client: FlaskClient) -> None:  # noqa: F811
    payload = client.get("/api/search/buy?q=ruim").get_json()
    assert payload["ids"]  # the check below means nothing on an empty list
    assert mismatches(payload, api.SearchPayload) == []


def test_a_house_matches_its_shape(client: FlaskClient) -> None:  # noqa: F811
    # House 1 has a detail page; house 3 has a price history.
    for house in (1, 3):
        payload = client.get(f"/api/house/{house}").get_json()
        assert mismatches(payload, api.HousePayload) == []


def test_the_outlines_match_their_shape(client: FlaskClient) -> None:  # noqa: F811
    payload = client.get("/api/neighbourhoods.geojson").get_json()
    assert payload["features"]
    assert mismatches(payload, api.ShapesPayload) == []


def test_an_api_404_matches_its_shape(client: FlaskClient) -> None:  # noqa: F811
    response = client.get("/api/nope")
    assert response.status_code == 404
    assert mismatches(response.get_json(), api.ErrorPayload) == []


def test_the_checker_catches_drift() -> None:
    # The tests above only prove something if a mismatch is actually reported.
    good = {"q": "x", "ids": [1]}
    assert mismatches(good, api.SearchPayload) == []
    assert mismatches({"q": "x"}, api.SearchPayload) == ["$.ids: missing"]
    assert mismatches({**good, "extra": 1}, api.SearchPayload) == [
        "$.extra: not in SearchPayload"
    ]
    assert mismatches({"q": "x", "ids": ["1"]}, api.SearchPayload) == [
        "$.ids[0]: '1' is not int"
    ]


def test_sqlite_types_are_what_listing_claims(cache: Path) -> None:  # noqa: F811
    # JSON loses the difference between an INTEGER and a REAL column that
    # happens to hold a whole number; the database does not.
    conn = sqlite3.connect(cache)
    affinity = {row[1]: row[2] for row in conn.execute("PRAGMA table_xinfo(listings)")}
    expected = {"INTEGER": int, "REAL": float, "TEXT": str}
    hints = get_type_hints(api.Listing)
    for column, declared in affinity.items():
        tp = hints[column]
        if get_origin(tp) is Literal:
            continue  # a TEXT column narrowed to its known values
        if get_origin(tp) in (Union, types.UnionType):
            members = set(get_args(tp)) - {type(None)}
        else:
            members = {tp}
        assert members == {expected[declared]}, f"{column}: {declared} vs {tp}"
