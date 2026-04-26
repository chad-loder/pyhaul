"""httpx-backed transport adapters (sync and async)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from typing import TypedDict

import httpx

from pyhaul._types import Url
from pyhaul.transport._http_common import transport_header_pairs
from pyhaul.transport.errors import (
    TransportConnectionError,
    TransportError,
    TransportHTTPError,
    TransportUnsupportedError,
)
from pyhaul.transport.protocols import (
    AsyncTransportResponse,
    AsyncTransportSession,
    TransportResponse,
    TransportSession,
)
from pyhaul.transport.types import TransportHeaders, TransportRequestOptions


def headers_from_httpx_response(resp: httpx.Response) -> TransportHeaders:
    """Build :class:`TransportHeaders` from an httpx response (multi-value safe)."""
    return TransportHeaders.from_pairs(transport_header_pairs(resp.headers.multi_items()))


@contextmanager
def map_httpx_transport_errors() -> Iterator[None]:
    """Map :mod:`httpx` failures to :mod:`pyhaul.transport.errors`."""
    try:
        yield
    except httpx.HTTPStatusError as e:
        sc = int(e.response.status_code)
        raise TransportHTTPError(str(e), status_code=sc) from e
    except httpx.UnsupportedProtocol as e:
        raise TransportUnsupportedError(str(e)) from e
    except httpx.LocalProtocolError as e:
        raise TransportError(f"local HTTP protocol error: {e}") from e
    except httpx.RemoteProtocolError as e:
        raise TransportConnectionError(str(e)) from e
    except (
        httpx.ConnectError,
        httpx.ReadError,
        httpx.WriteError,
        httpx.CloseError,
        httpx.ProxyError,
    ) as e:
        raise TransportConnectionError(str(e)) from e
    except httpx.TimeoutException as e:
        raise TransportConnectionError(str(e)) from e
    except httpx.ProtocolError as e:
        raise TransportConnectionError(str(e)) from e
    except httpx.RequestError as e:
        raise TransportError(str(e)) from e


class _HttpxRequestKwargs(TypedDict, total=False):
    timeout: float | tuple[float | None, float | None, float | None, float | None] | httpx.Timeout
    follow_redirects: bool


def _request_options_to_httpx_kwargs(
    options: TransportRequestOptions | None,
) -> _HttpxRequestKwargs:
    if options is None:
        return {}
    kw: _HttpxRequestKwargs = {}
    if options.timeout is not None:
        t = options.timeout
        kw["timeout"] = (t[0], t[1], None, None) if isinstance(t, tuple) else t
    if options.allow_redirects is not None:
        kw["follow_redirects"] = options.allow_redirects
    return kw


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------


class HttpxTransportResponse(TransportResponse):
    """Transport view over an :class:`httpx.Response`."""

    __slots__ = ("_headers", "_resp")

    def __init__(self, resp: httpx.Response) -> None:
        self._resp = resp
        self._headers: TransportHeaders | None = None

    @property
    def status_code(self) -> int:
        """HTTP status code of the response."""
        return int(self._resp.status_code)

    @property
    def headers(self) -> TransportHeaders:
        """Response headers, lazily parsed on first access."""
        if self._headers is None:
            self._headers = headers_from_httpx_response(self._resp)
        return self._headers

    def raise_for_status(self) -> None:
        """Raise :exc:`~pyhaul.transport.errors.TransportHTTPError` for 4xx/5xx responses."""
        with map_httpx_transport_errors():
            self._resp.raise_for_status()

    def iter_raw_bytes(self, *, chunk_size: int) -> Iterator[bytes]:
        """Yield raw response body chunks without decoding."""
        with map_httpx_transport_errors():
            yield from self._resp.iter_raw(chunk_size=chunk_size)


class HttpxAdapter:
    """Wrap a synchronous :class:`httpx.Client` as a :class:`TransportSession`."""

    __slots__ = ("_client",)

    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    @contextmanager
    def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> Iterator[TransportResponse]:
        """Open a streaming GET request and yield the response."""
        kwargs = _request_options_to_httpx_kwargs(options)
        with (
            map_httpx_transport_errors(),
            self._client.stream("GET", str(url), headers=dict(headers), **kwargs) as resp,
        ):
            yield HttpxTransportResponse(resp)


def httpx_transport(client: httpx.Client) -> TransportSession:
    """Shorthand: ``HttpxAdapter(client)``."""
    return HttpxAdapter(client)


# ---------------------------------------------------------------------------
# Async
# ---------------------------------------------------------------------------


class AsyncHttpxTransportResponse(AsyncTransportResponse):
    """Async transport view over an :class:`httpx.Response`."""

    __slots__ = ("_headers", "_resp")

    def __init__(self, resp: httpx.Response) -> None:
        self._resp = resp
        self._headers: TransportHeaders | None = None

    @property
    def status_code(self) -> int:
        """HTTP status code of the response."""
        return int(self._resp.status_code)

    @property
    def headers(self) -> TransportHeaders:
        """Response headers, lazily parsed on first access."""
        if self._headers is None:
            self._headers = headers_from_httpx_response(self._resp)
        return self._headers

    def raise_for_status(self) -> None:
        """Raise :exc:`~pyhaul.transport.errors.TransportHTTPError` for 4xx/5xx responses."""
        with map_httpx_transport_errors():
            self._resp.raise_for_status()

    async def aiter_raw_bytes(self, *, chunk_size: int) -> AsyncIterator[bytes]:
        """Yield raw response body chunks without decoding."""
        with map_httpx_transport_errors():
            async for chunk in self._resp.aiter_raw(chunk_size=chunk_size):
                yield chunk


class AsyncHttpxAdapter:
    """Wrap an :class:`httpx.AsyncClient` as an :class:`AsyncTransportSession`."""

    __slots__ = ("_client",)

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    @asynccontextmanager
    async def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> AsyncIterator[AsyncTransportResponse]:
        """Open a streaming GET request and yield the response."""
        kwargs = _request_options_to_httpx_kwargs(options)
        with map_httpx_transport_errors():
            async with self._client.stream("GET", str(url), headers=dict(headers), **kwargs) as resp:
                yield AsyncHttpxTransportResponse(resp)


def async_httpx_transport(client: httpx.AsyncClient) -> AsyncTransportSession:
    """Shorthand: ``AsyncHttpxAdapter(client)``."""
    return AsyncHttpxAdapter(client)
