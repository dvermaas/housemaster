"""Tests for photo caching.

`download_photo` takes an injectable `fetch`, so none of this monkeypatches
anything or touches the network.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from housemaster import media
from housemaster.media import MediaError, PhotoRequest

JPEG = b"\xff\xd8\xff" + b"\x00" * 2000
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2000
WEBP = b"RIFF" + b"\x00" * 4 + b"WEBP" + b"\x00" * 2000
AVIF = b"\x00" * 4 + b"ftypavif" + b"\x00" * 2000


def fetcher(body: bytes, content_type: str = "image/jpeg") -> media.Fetcher:
    def fetch(_url: str) -> tuple[bytes, str]:
        return body, content_type

    return fetch


def exploding_fetch(_url: str) -> tuple[bytes, str]:
    raise ConnectionError("network is down")


# --- URL building ----------------------------------------------------------


def test_photo_url_uses_the_id_as_a_complete_path() -> None:
    # The id already is the CDN path; appending .jpg would 404.
    assert media.photo_url("tiara-media/abc/def", 720) == (
        "https://cloud.funda.nl/tiara-media/abc/def?options=width=720"
    )


def test_omitting_the_width_requests_the_master() -> None:
    assert media.photo_url("tiara-media/abc/def", None) == (
        "https://cloud.funda.nl/tiara-media/abc/def"
    )


def test_a_leading_slash_does_not_double_up() -> None:
    assert "funda.nl//" not in media.photo_url("/tiara-media/abc/def")


# --- format detection ------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [(JPEG, "jpg"), (PNG, "png"), (WEBP, "webp"), (AVIF, "avif")],
)
def test_detect_format(body: bytes, expected: str) -> None:
    assert media.detect_format(body) == expected


def test_detect_format_rejects_non_images() -> None:
    assert media.detect_format(b"<!doctype html><html>blocked") is None


def test_validate_returns_the_extension() -> None:
    assert media.validate(JPEG, "image/jpeg") == "jpg"


def test_validate_rejects_a_non_image_content_type() -> None:
    # The CDN reports a missing image as text/plain.
    with pytest.raises(MediaError, match="not an image"):
        media.validate(b"ERROR 9404: Could not fetch", "text/plain")


def test_validate_rejects_a_suspiciously_small_body() -> None:
    with pytest.raises(MediaError, match="too small"):
        media.validate(b"\xff\xd8\xff tiny", "image/jpeg")


def test_validate_rejects_image_content_type_with_junk_bytes() -> None:
    # A block page served as image/jpeg would otherwise be cached as a photo.
    with pytest.raises(MediaError, match="unrecognised"):
        media.validate(b"<html>" + b"x" * 2000, "image/jpeg")


# --- downloading -----------------------------------------------------------


def test_a_successful_download_writes_the_file_and_reports_it(tmp_path: Path) -> None:
    result = media.download_photo(
        tmp_path, PhotoRequest(1, 0, "tiara-media/a/b"), fetch=fetcher(JPEG)
    )
    assert result.ok
    assert result.photo is not None
    assert result.photo.local_path == "1/00.jpg"
    assert (tmp_path / "1" / "00.jpg").read_bytes() == JPEG


def test_a_png_keeps_its_own_extension(tmp_path: Path) -> None:
    # Funda really does serve PNGs; calling one .jpg would be a lie on disk.
    result = media.download_photo(
        tmp_path, PhotoRequest(1, 0, "x"), fetch=fetcher(PNG, "image/png")
    )
    assert result.photo is not None
    assert result.photo.local_path == "1/00.png"
    assert (tmp_path / "1" / "00.png").exists()


def test_no_part_file_survives_a_success(tmp_path: Path) -> None:
    media.download_photo(tmp_path, PhotoRequest(1, 0, "x"), fetch=fetcher(JPEG))
    assert list(tmp_path.rglob("*.part")) == []


def test_no_part_file_survives_a_failure(tmp_path: Path) -> None:
    # A truncated leftover would look like a cached photo to the next run.
    media.download_photo(tmp_path, PhotoRequest(1, 0, "x"), fetch=exploding_fetch)
    assert list(tmp_path.rglob("*.part")) == []


def test_a_failure_is_reported_not_raised(tmp_path: Path) -> None:
    result = media.download_photo(
        tmp_path, PhotoRequest(1, 0, "x"), fetch=exploding_fetch
    )
    assert not result.ok
    assert result.error is not None
    assert "network is down" in result.error


def test_a_rejected_image_leaves_nothing_on_disk(tmp_path: Path) -> None:
    media.download_photo(
        tmp_path, PhotoRequest(1, 0, "x"), fetch=fetcher(b"nope" * 500, "text/plain")
    )
    assert list(tmp_path.rglob("*")) in ([], [tmp_path / "1"])


def test_an_already_cached_photo_is_not_refetched(tmp_path: Path) -> None:
    def refuse(_url: str) -> tuple[bytes, str]:
        raise AssertionError("should not have been fetched")

    media.download_photo(tmp_path, PhotoRequest(1, 0, "x"), fetch=fetcher(JPEG))
    result = media.download_photo(tmp_path, PhotoRequest(1, 0, "x"), fetch=refuse)
    assert result.ok


def test_a_cached_photo_is_found_whatever_extension_it_has(tmp_path: Path) -> None:
    media.download_photo(
        tmp_path, PhotoRequest(1, 0, "x"), fetch=fetcher(PNG, "image/png")
    )
    found = media.find_existing(tmp_path, 1, 0)
    assert found is not None
    assert found.name == "00.png"


def test_a_truncated_leftover_is_not_mistaken_for_a_cached_photo(tmp_path: Path) -> None:
    (tmp_path / "1").mkdir()
    (tmp_path / "1" / "00.jpg").write_bytes(b"\xff\xd8\xff")  # under MIN_BYTES
    assert media.find_existing(tmp_path, 1, 0) is None


def test_download_photos_returns_one_result_per_input_in_order(tmp_path: Path) -> None:
    results = media.download_photos(
        tmp_path, 7, [(0, "a"), (1, "b"), (2, "c")], fetch=fetcher(JPEG)
    )
    assert [r.position for r in results] == [0, 1, 2]
    assert all(r.ok for r in results)
