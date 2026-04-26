"""CDN-safe default request headers and ETag-churn validation.

These headers are designed for downloads over Tor and other hostile
transports where intermediaries (exit nodes, transparent proxies, CDN
edges) may re-encode or cache responses unpredictably.

Key principles:

* ``no-transform`` — prohibits intermediaries from modifying the payload
  (RFC 9111 §5.2.2.6).  Prevents exit-node proxies from injecting
  Content-Encoding.
* ``no-store`` — asks compliant proxies not to cache the exchange;
  low-risk privacy hygiene.
* ``Accept-Encoding: identity`` — tells the origin not to compress.
  Complement to ``no-transform`` (one is for the server, the other for
  every proxy in the chain).

We deliberately **omit** ``no-cache`` and ``Pragma: no-cache``.  From
multiple Tor exit IPs, these look like a Layer-7 DDoS cache-busting
attack to CDN WAFs and result in 403/429/dropped connections.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pyhaul._types import ServerMeta

log = logging.getLogger("pyhaul")

# ---------------------------------------------------------------------------
# Header Categories
# ---------------------------------------------------------------------------

STRUCTURAL_HEADERS = {
    "range",
    "if-range",
    "accept-encoding",
    "te",
}
"""Headers that dictate the 'shape' of the byte stream. pyhaul MUST own
these to guarantee bit-for-bit resume integrity. User overrides are
silently ignored or logged as debug warnings."""

SAFETY_HEADERS = {
    "Cache-Control": "no-store, no-transform",
}
"""Headers that pyhaul strongly recommends for protocol safety (preventing
proxy-level transformation) but which can be overridden by users who
prioritize stealth or WAF-blending."""

DEFAULT_HEADERS: dict[str, str] = {
    "Accept-Encoding": "identity",
    **SAFETY_HEADERS,
}


def merge_headers(
    user_headers: dict[str, str],
    pyhaul_headers: dict[str, str],
) -> dict[str, str]:
    """Merge user-supplied headers with pyhaul protocol requirements.

    Follows these precedence rules:
    1.  **Structural headers** (Range, If-Range) always come from pyhaul.
    2.  **Identity enforcement** (Accept-Encoding) always comes from pyhaul.
    3.  **Safety headers** (Cache-Control) come from pyhaul unless the user
        explicitly provided their own.
    4.  **Metadata headers** (UA, Auth, Cookies) always come from the user.

    Case-insensitive for keys; returns a dict with keys as provided by
    pyhaul or user (favoring user casing for metadata).
    """
    final = user_headers.copy()
    user_keys_lower = {k.lower(): k for k in user_headers}

    # 1. Apply pyhaul's required structural/safety headers
    for rk, rv in pyhaul_headers.items():
        rk_lower = rk.lower()

        # If it's a structural header or pyhaul's default identity, it MUST win.
        if rk_lower in STRUCTURAL_HEADERS:
            if rk_lower in user_keys_lower and user_headers[user_keys_lower[rk_lower]] != rv:
                log.debug(
                    "Overriding structural header %r with pyhaul value %r",
                    user_keys_lower[rk_lower],
                    rv,
                )
            # Remove any user-cased version to ensure our casing/value wins
            if rk_lower in user_keys_lower:
                final.pop(user_keys_lower[rk_lower])
            final[rk] = rv
            continue

        # If it's a safety header, only apply if the user hasn't provided one.
        if rk_lower in SAFETY_HEADERS:
            if rk_lower not in user_keys_lower:
                final[rk] = rv
            continue

        # Fallback for any other internal pyhaul defaults
        if rk_lower not in user_keys_lower:
            final[rk] = rv

    return final


def is_file_changed(old: ServerMeta, new: ServerMeta) -> bool:
    """Determine whether the remote file has actually changed.

    Tolerates ETag churn across CDN edges by falling back to
    ``total_length`` + ``Last-Modified`` when ETags disagree.  This
    matches Firefox's ``entityID`` approach.

    Either side may have empty ``etag`` / ``last_modified`` fields (e.g.
    when ``old`` is reconstructed from a tracker, which doesn't persist
    ``Last-Modified``).  In that case the fallback fails closed — the
    function returns ``True`` rather than silently claiming the file is
    unchanged based on ``total_length`` alone.
    """
    if old.etag and new.etag and old.etag == new.etag:
        return False

    return not (
        old.total_length is not None
        and old.total_length == new.total_length
        and old.last_modified
        and new.last_modified
        and old.last_modified == new.last_modified
    )
