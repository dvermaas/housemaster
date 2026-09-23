"""The single place HouseMaster makes an HTTP request.

Everything funda serves is gated on the TLS fingerprint, so every request must
go through curl_cffi with `impersonate=`. Plain urllib gets an Akamai block
page -- served with status **200**, which is why `funda.fetch_html` checks the
body rather than the status code.

Keeping one call site means "did we impersonate a browser?" is a property that
can be tested once rather than audited per caller.
"""

from __future__ import annotations

from curl_cffi import requests

IMPERSONATE = "chrome"
DEFAULT_TIMEOUT = 30


def get_text(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    response = requests.get(url, impersonate=IMPERSONATE, timeout=timeout)
    response.raise_for_status()  # type: ignore[no-untyped-call]  # curl_cffi
    return response.text
