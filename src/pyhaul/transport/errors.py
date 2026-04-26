"""Transport-layer errors mapped from HTTP client libraries.

Callers may ``except pyhaul.transport.TransportError`` (or subclasses) without
importing niquests, httpx, or requests. Adapters translate library-specific
exceptions at the boundary.
"""

from __future__ import annotations

from pyhaul._types import HaulError


class TransportError(HaulError):
    """Base class for failures surfaced by a :class:`pyhaul.transport.TransportSession`."""


class TransportConnectionError(TransportError, ConnectionError):
    """Network-level failure (DNS, refused connection, reset, timeout, ...).

    Subclasses builtin :exc:`ConnectionError` (and thus :exc:`OSError`) as well as
    :class:`TransportError`.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message)


class TransportTLSError(TransportError):
    """TLS / certificate verification failure."""


class TransportHTTPError(TransportError):
    """HTTP response that is treated as an error by the adapter or engine."""

    status_code: int | None

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class TransportUnsupportedError(TransportError):
    """The transport or server cannot satisfy a request (capability / policy)."""
