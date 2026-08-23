"""Funda's image CDN: turning a stored `photo_image_id` into a URL.

Photos are **hotlinked, never downloaded**. The `photo_image_id` that already
comes back in every search result is a complete CDN path, so a house's images
cost nothing to record and nothing to serve -- the browser fetches them
straight from funda, negotiating format and size on its own.

That leaves this module with no I/O at all: a pure URL builder, importing
nothing, which is why the web app can use it without ever gaining a way to
reach the network.
"""

from __future__ import annotations

CDN_BASE = "https://cloud.funda.nl"

WIDTHS = (228, 464, 720, 1080, 1440, 2160)
"""The CDN's ladder. Any other width rounds *up* to one of these."""
DEFAULT_WIDTH = 720


def photo_url(image_id: str, width: int | None = DEFAULT_WIDTH) -> str:
    """Build a CDN URL from a stored image id.

    `image_id` is a complete path (`tiara-media/<uuid>/<uuid>`) with no
    extension -- appending `.jpg` gives a 404. Omitting the width returns
    funda's 2160px master.
    """
    base = f"{CDN_BASE}/{image_id.lstrip('/')}"
    return f"{base}?options=width={width}" if width else base
