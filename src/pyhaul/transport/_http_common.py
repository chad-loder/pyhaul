"""Shared helpers for HTTP transport adapters (header strings, request kwargs)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TypedDict

from pyhaul.transport.types import TransportRequestOptions


def header_value_to_str(value: object, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, bytes):
        return value.decode("latin-1")
    return str(value)


def transport_header_pairs(
    items: Iterable[tuple[object, object]],
) -> list[tuple[str, str]]:
    return [(str(k), header_value_to_str(v).strip()) for k, v in items]


class _RequestsLikeRequestKwargs(TypedDict, total=False):
    timeout: float | tuple[float, float]
    verify: bool
    allow_redirects: bool


def request_options_to_requests_like_kwargs(
    options: TransportRequestOptions | None,
) -> _RequestsLikeRequestKwargs:
    if options is None:
        return {}
    kw: _RequestsLikeRequestKwargs = {}
    if options.timeout is not None:
        kw["timeout"] = options.timeout
    if options.verify is not None:
        kw["verify"] = options.verify
    if options.allow_redirects is not None:
        kw["allow_redirects"] = options.allow_redirects
    return kw
