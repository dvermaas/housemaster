"""Tests for CDN photo URLs.

Photos are hotlinked, so there is nothing here about downloading, validating
or caching bytes -- the module is a pure URL builder.
"""

from __future__ import annotations

import ast
import inspect

from housemaster import photos


def test_photo_url_uses_the_id_as_a_complete_path() -> None:
    # The id already is the CDN path; appending .jpg would 404.
    assert photos.photo_url("tiara-media/abc/def", 720) == (
        "https://cloud.funda.nl/tiara-media/abc/def?options=width=720"
    )


def test_omitting_the_width_requests_the_master() -> None:
    assert photos.photo_url("tiara-media/abc/def", None) == (
        "https://cloud.funda.nl/tiara-media/abc/def"
    )


def test_a_leading_slash_does_not_double_up() -> None:
    assert "funda.nl//" not in photos.photo_url("/tiara-media/abc/def")


def test_the_default_width_is_on_the_cdn_ladder() -> None:
    # An off-ladder width is served by rounding up, so asking for one wastes
    # bytes on a size we then scale down again.
    assert photos.DEFAULT_WIDTH in photos.WIDTHS


def test_the_module_imports_nothing() -> None:
    """The web app depends on this, so it must stay unable to reach anything.

    A dependency-free module cannot grow a download path by accident: adding
    one would mean adding an import, and this fails the moment that happens.
    """
    tree = ast.parse(inspect.getsource(photos))
    imported = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        # `from __future__ import annotations` is syntax, not a dependency.
        and getattr(node, "module", None) != "__future__"
    ]
    assert imported == []
