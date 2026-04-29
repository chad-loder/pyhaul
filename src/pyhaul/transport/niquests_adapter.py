"""Niquests-backed transport adapters (sync and async)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from typing import cast

import niquests
import niquests.exceptions as _niquests_exceptions

from pyhaul._types import Url
from pyhaul.transport._http_common import request_options_to_requests_like_kwargs, transport_header_pairs
from pyhaul.transport._requests_like_error_map import map_requests_like_transport_errors
from pyhaul.transport.protocols import (
    AsyncTransportResponse,
    AsyncTransportSession,
    TransportResponse,
    TransportSession,
)
from pyhaul.transport.types import TransportHeaders, TransportRequestOptions


def headers_from_niquests_response(
    resp: niquests.Response | niquests.AsyncResponse,
) -> TransportHeaders:
    """Build :class:`TransportHeaders` from a niquests sync or async response."""
    # resp.headers is CaseInsensitiveDict (collapses multi-value headers).
    # Reach through to the urllib3(-future) raw response for multi-value
    # fidelity; order is grouped-by-name (HTTPHeaderDict limitation).
    raw = getattr(resp, "raw", None)
    raw_headers = getattr(raw, "headers", None) if raw is not None else None
    if raw_headers is not None and hasattr(raw_headers, "iteritems"):
        return TransportHeaders.from_pairs(transport_header_pairs(raw_headers.iteritems()))
    hdr = resp.headers
    pairs = transport_header_pairs((k, hdr[k]) for k in hdr)
    return TransportHeaders.from_pairs(pairs)


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


class NiquestsTransportResponse(TransportResponse):
    """Transport view over a niquests :class:`~niquests.Response`."""

    __slots__ = ("_headers", "_resp")

    def __init__(self, resp: niquests.Response) -> None:
        self._resp = resp
        self._headers: TransportHeaders | None = None

    @property
    def status_code(self) -> int:
        """HTTP status code of the response."""
        return cast("int", self._resp.status_code)

    @property
    def headers(self) -> TransportHeaders:
        """Response headers, lazily parsed on first access."""
        if self._headers is None:
            self._headers = headers_from_niquests_response(self._resp)
        return self._headers

    def raise_for_status(self) -> None:
        """Raise :exc:`~pyhaul.transport.errors.TransportHTTPError` for 4xx/5xx responses."""
        with map_requests_like_transport_errors(_niquests_exceptions):
            self._resp.raise_for_status()

    def iter_raw_bytes(self, *, chunk_size: int) -> Iterator[bytes]:
        """Yield raw response body chunks without decoding."""
        with map_requests_like_transport_errors(_niquests_exceptions):
            yield from self._resp.iter_raw(chunk_size=chunk_size)


class NiquestsAdapter:
    """Wrap a :class:`niquests.Session` as a :class:`TransportSession`."""

    __slots__ = ("_session",)

    def __init__(self, session: niquests.Session) -> None:
        self._session = session

    def prepare_headers(self, headers: TransportHeaders) -> TransportHeaders:
        """Optionally mutate headers before they are sent (noop)."""
        return headers

    @contextmanager
    def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> Iterator[TransportResponse]:
        """Open a streaming GET request and yield the response."""
        kwargs = request_options_to_requests_like_kwargs(options)
        with (
            map_requests_like_transport_errors(_niquests_exceptions),
            self._session.get(url, headers=dict(headers), stream=True, **kwargs) as resp,
        ):
            yield NiquestsTransportResponse(resp)


def niquests_transport(session: niquests.Session) -> TransportSession:
    """Shorthand: ``NiquestsAdapter(session)``."""
    return NiquestsAdapter(session)


# ---------------------------------------------------------------------------
# Async
# ---------------------------------------------------------------------------


class AsyncNiquestsTransportResponse(AsyncTransportResponse):
    """Async transport view over a niquests :class:`~niquests.AsyncSession` response."""

    __slots__ = ("_headers", "_resp")

    def __init__(self, resp: niquests.AsyncResponse) -> None:
        self._resp = resp
        self._headers: TransportHeaders | None = None

    @property
    def status_code(self) -> int:
        """HTTP status code of the response."""
        return cast("int", self._resp.status_code)

    @property
    def headers(self) -> TransportHeaders:
        """Response headers, lazily parsed on first access."""
        if self._headers is None:
            self._headers = headers_from_niquests_response(self._resp)
        return self._headers

    def raise_for_status(self) -> None:
        """Raise :exc:`~pyhaul.transport.errors.TransportHTTPError` for 4xx/5xx responses."""
        with map_requests_like_transport_errors(_niquests_exceptions):
            self._resp.raise_for_status()

    async def aiter_raw_bytes(self, *, chunk_size: int) -> AsyncIterator[bytes]:
        """Yield raw response body chunks without decoding."""
        with map_requests_like_transport_errors(_niquests_exceptions):
            async for chunk in await self._resp.iter_raw(chunk_size=chunk_size):
                yield chunk


class AsyncNiquestsAdapter:
    """Wrap a :class:`niquests.AsyncSession` as an :class:`AsyncTransportSession`."""

    __slots__ = ("_session",)

    def __init__(self, session: niquests.AsyncSession) -> None:
        self._session = session

    def prepare_headers(self, headers: TransportHeaders) -> TransportHeaders:
        """Optionally mutate headers before they are sent (noop)."""
        return headers

    @asynccontextmanager
    async def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> AsyncIterator[AsyncTransportResponse]:
        """Open a streaming GET request and yield the response."""
        kwargs = request_options_to_requests_like_kwargs(options)
        with map_requests_like_transport_errors(_niquests_exceptions):
            resp = await self._session.get(url, headers=dict(headers), stream=True, **kwargs)
            try:
                yield AsyncNiquestsTransportResponse(resp)
            finally:
                await resp.close()


def async_niquests_transport(session: niquests.AsyncSession) -> AsyncTransportSession:
    """Shorthand: ``AsyncNiquestsAdapter(session)``."""
    return AsyncNiquestsAdapter(session)
