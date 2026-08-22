"""The single place HouseMaster makes an HTTP request.

Everything funda serves -- pages and CDN images alike -- is gated on the TLS
fingerprint, so every request must go through curl_cffi with `impersonate=`.
Plain urllib gets an Akamai block page (200 for pages, 403 for the CDN).

Keeping one call site means "did we impersonate a browser?" is a property that
can be tested once rather than audited per caller.
"""

from __future__ import annotations

from curl_cffi import requests

IMPERSONATE = "chrome"
DEFAULT_TIMEOUT = 30


def get_text(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    response = requests.get(url, impersonate=IMPERSONATE, timeout=timeout)
    response.raise_for_status()
    return response.text


def get_bytes(
    url: str, timeout: int = DEFAULT_TIMEOUT, accept: str | None = None
) -> tuple[bytes, str]:
    """Fetch binary content. Returns (body, content-type).

    `accept` matters more than it looks: funda's CDN sends `Vary: accept`, and
    curl_cffi's Chrome impersonation supplies Chrome's own Accept header, so
    without an override the CDN happily returns AVIF where JPEG was intended.
    """
    headers = {"Accept": accept} if accept else None
    response = requests.get(
        url, impersonate=IMPERSONATE, timeout=timeout, headers=headers
    )
    response.raise_for_status()
    return response.content, response.headers.get("content-type", "")
