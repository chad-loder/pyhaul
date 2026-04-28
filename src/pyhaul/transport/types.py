"""Transport-layer value types (headers, per-request options).

Adapters normalize client-specific representations into these types for the
download engine.
"""

from __future__ import annotations

from dataclasses import dataclass

from pyhaul.transport._headers import TransportHeaders

__all__ = ["TransportHeaders", "TransportRequestOptions"]


@dataclass(frozen=True, slots=True, kw_only=True)
class TransportRequestOptions:
    """Per-request knobs forwarded by adapters to the underlying client.

    Mirrors what :class:`pyhaul.downloader.PieceDownloader` threads through
    ``_request_kwargs()`` today. ``None`` means "do not pass; use client default".
    """

    timeout: float | tuple[float, float] | None = None
    verify: bool | None = None
    allow_redirects: bool | None = None
