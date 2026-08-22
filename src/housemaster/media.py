"""Downloading and caching listing photos.

Funda's image CDN takes the `photo_image_id` that already comes back in search
results, so photos need no detail request at all. Three verified behaviours
shape this module:

* `Vary: accept` -- curl_cffi's Chrome impersonation would otherwise get AVIF
  bytes written into a `.jpg`, silently. We ask for JPEG and then check.
* TLS impersonation is required for the CDN too (plain requests get 403).
* URLs are content-addressed and immutable, and conditional requests never
  return 304 -- so a cached file is never revalidated, only skipped.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from housemaster import net
from housemaster.models import StoredPhoto

CDN_BASE = "https://cloud.funda.nl"
ACCEPT = "image/jpeg,*/*"
"""Explicit, or the CDN honours Chrome's Accept header and returns AVIF."""

WIDTHS = (228, 464, 720, 1080, 1440, 2160)
"""The CDN's ladder. Any other width rounds *up* to one of these."""
DEFAULT_WIDTH = 720

JPEG_MAGIC = b"\xff\xd8\xff"
MIN_BYTES = 1024

# Funda serves mostly JPEG but genuinely mixes in PNG (and the CDN can return
# WebP/AVIF). Detecting the real format matters twice over: it proves the bytes
# are an image at all, and it keeps the file extension honest.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "jpg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF8", "gif"),
)

Fetcher = Callable[[str], tuple[bytes, str]]


class MediaError(RuntimeError):
    """The bytes that came back were not a usable image."""


def detect_format(body: bytes) -> str | None:
    """Return a file extension, or None if these bytes are not an image."""
    for magic, extension in _MAGIC:
        if body.startswith(magic):
            return extension
    if body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return "webp"
    if body[4:8] == b"ftyp":  # ISO-BMFF container: AVIF / HEIC
        return "avif" if body[8:12] in (b"avif", b"avis") else "heic"
    return None


def photo_url(image_id: str, width: int | None = DEFAULT_WIDTH) -> str:
    """Build a CDN URL from a stored image id.

    `image_id` is a complete path (`tiara-media/<uuid>/<uuid>`) with no
    extension -- appending `.jpg` gives a 404. Omitting the width returns
    funda's 2160px master.
    """
    base = f"{CDN_BASE}/{image_id.lstrip('/')}"
    return f"{base}?options=width={width}" if width else base


def _default_fetch(url: str) -> tuple[bytes, str]:
    return net.get_bytes(url, accept=ACCEPT)


def validate(body: bytes, content_type: str) -> str:
    """Check the bytes really are an image and return their extension.

    The CDN reports a 404 as `text/plain`, and a block page renders as HTML, so
    neither the status code nor the declared content type is proof on its own.
    The magic bytes are -- this is the image-pipeline twin of the
    `__NUXT_DATA__` check, and it is also what catches the AVIF trap if the
    Accept header ever stops being honoured.
    """
    if not content_type.startswith("image/"):
        raise MediaError(f"content-type {content_type!r} is not an image")
    if len(body) < MIN_BYTES:
        raise MediaError(f"only {len(body)} bytes -- too small to be a photo")
    extension = detect_format(body)
    if extension is None:
        raise MediaError(f"unrecognised image data (starts with {body[:8]!r})")
    return extension


def photo_path(
    root: Path, listing_id: int, position: int, extension: str = "jpg"
) -> Path:
    return root / str(listing_id) / f"{position:02d}.{extension}"


def relative_path(listing_id: int, position: int, extension: str = "jpg") -> str:
    """Stored in the database, so the cache can be moved without rewriting rows."""
    return f"{listing_id}/{position:02d}.{extension}"


def find_existing(root: Path, listing_id: int, position: int) -> Path | None:
    """An already-cached photo, whatever extension it landed with."""
    directory = root / str(listing_id)
    if not directory.is_dir():
        return None
    for candidate in sorted(directory.glob(f"{position:02d}.*")):
        if candidate.suffix != ".part" and candidate.stat().st_size >= MIN_BYTES:
            return candidate
    return None


@dataclass(frozen=True, slots=True)
class Download:
    """The outcome of one photo. Exactly one of `photo` / `error` is set."""

    listing_id: int
    position: int
    photo: StoredPhoto | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.photo is not None


@dataclass(frozen=True, slots=True)
class PhotoRequest:
    """One photo to fetch: which listing, which slot, which CDN path."""

    listing_id: int
    position: int
    image_id: str


def download_photo(
    root: Path,
    request: PhotoRequest,
    *,
    width: int = DEFAULT_WIDTH,
    fetch: Fetcher = _default_fetch,
) -> Download:
    """Fetch one photo to disk. Never raises -- failures come back as `error`.

    Writes to a `.part` file and renames, so a killed run can never leave a
    truncated image that a later run would mistake for a cached one.
    """
    listing_id, position = request.listing_id, request.position
    cached = find_existing(root, listing_id, position)
    if cached is not None:
        return Download(
            listing_id,
            position,
            StoredPhoto(
                listing_id=listing_id,
                position=position,
                width=width,
                local_path=f"{listing_id}/{cached.name}",
                size_bytes=cached.stat().st_size,
            ),
        )

    partial = root / str(listing_id) / f"{position:02d}.part"
    try:
        body, content_type = fetch(photo_url(request.image_id, width))
        extension = validate(body, content_type)
        destination = photo_path(root, listing_id, position, extension)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(body)
        partial.replace(destination)  # atomic on Windows and POSIX alike
    except Exception as exc:  # one bad image must never stop a whole run
        partial.unlink(missing_ok=True)
        return Download(listing_id, position, error=f"{type(exc).__name__}: {exc}")

    return Download(
        listing_id,
        position,
        StoredPhoto(
            listing_id=listing_id,
            position=position,
            width=width,
            local_path=relative_path(listing_id, position, extension),
            size_bytes=len(body),
        ),
    )


def download_photos(
    root: Path,
    listing_id: int,
    photos: Sequence[tuple[int, str]],
    *,
    width: int = DEFAULT_WIDTH,
    fetch: Fetcher = _default_fetch,
) -> list[Download]:
    """Fetch several photos for one listing, in order. One result per input."""
    return [
        download_photo(
            root, PhotoRequest(listing_id, position, image_id), width=width, fetch=fetch
        )
        for position, image_id in photos
    ]
