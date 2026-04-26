"""Transport-layer value types (headers, per-request options).

Adapters normalize client-specific representations into these types for the
download engine.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

_TRANSPORT_HEADERS_INIT = object()


def _norm_header_name(name: str) -> str:
    return name.strip().lower()


def _norm_header_value(value: str) -> str:
    return value.strip()


@dataclass(init=False, frozen=True, slots=True)
class TransportHeaders:
    """Read-only HTTP response headers with multi-value support.

    Field names are matched case-insensitively (HTTP semantics). ``get``
    returns the **first** value for a name; ``get_all`` returns **every**
    value in wire order (as supplied when constructed).

    Construct instances only via :meth:`from_pairs` or :meth:`from_mapping`;
    the default constructor is not supported.

    Adapters should prefer :meth:`from_pairs` when the underlying library
    exposes duplicate field names; :meth:`from_mapping` cannot represent
    duplicate keys because a Python ``dict`` cannot hold them.
    """

    _items: tuple[tuple[str, str], ...]
    """``(lowercase_name, stripped_value)`` in insertion order."""

    def __init__(self, _init: object, items: tuple[tuple[str, str], ...]) -> None:
        if _init is not _TRANSPORT_HEADERS_INIT:
            msg = "TransportHeaders cannot be constructed directly; use from_pairs or from_mapping"
            raise TypeError(msg)
        object.__setattr__(self, "_items", items)

    @classmethod
    def from_pairs(cls, pairs: Sequence[tuple[str, str]]) -> TransportHeaders:
        """Build headers from an ordered sequence of ``(name, value)`` pairs.

        Preserves duplicate names and order for :meth:`get_all`.
        """
        items = tuple((_norm_header_name(n), _norm_header_value(v)) for n, v in pairs)
        return cls(_TRANSPORT_HEADERS_INIT, items)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> TransportHeaders:
        r"""Build headers from a string mapping (single value per key).

        Iteration order follows ``mapping`` iteration (Python 3.7+ dict order).
        Keys that differ only by case become separate pairs first, then share
        a normalized bucket—so ``{\"ETag\": \"a\", \"etag\": \"b\"}`` yields
        two values for ``etag`` (see tests). A true Python ``dict`` cannot hold
        duplicate identical keys; use :meth:`from_pairs` for wire-faithful duplicates.
        """
        pairs = [(str(k), str(v)) for k, v in mapping.items()]
        return cls.from_pairs(pairs)

    def get(self, name: str) -> str:
        r"""Return the first value for *name*, or ``\"\"`` if absent."""
        key = _norm_header_name(name)
        for k, v in self._items:
            if k == key:
                return v
        return ""

    def get_all(self, name: str) -> tuple[str, ...]:
        """Return every value for *name*, in insertion order."""
        key = _norm_header_name(name)
        return tuple(v for k, v in self._items if k == key)


@dataclass(frozen=True, slots=True, kw_only=True)
class TransportRequestOptions:
    """Per-request knobs forwarded by adapters to the underlying client.

    Mirrors what :class:`pyhaul.downloader.PieceDownloader` threads through
    ``_request_kwargs()`` today. ``None`` means “do not pass; use client default”.
    """

    timeout: float | tuple[float, float] | None = None
    verify: bool | None = None
    allow_redirects: bool | None = None
