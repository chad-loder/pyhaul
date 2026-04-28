"""aiohttp-backed async transport adapter."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from typing import TypedDict

import aiohttp
import aiohttp.client_exceptions as _aiohttp_errors

from pyhaul._types import Url
from pyhaul.transport._http_common import transport_header_pairs
from pyhaul.transport.errors import (
    TransportConnectionError,
    TransportError,
    TransportHTTPError,
    TransportTLSError,
    TransportUnsupportedError,
)
from pyhaul.transport.protocols import AsyncTransportResponse, AsyncTransportSession
from pyhaul.transport.types import TransportHeaders, TransportRequestOptions


def headers_from_aiohttp_response(resp: aiohttp.ClientResponse) -> TransportHeaders:
    """Build :class:`TransportHeaders` from an aiohttp response (multi-value safe)."""
    # CIMultiDictProxy.items() preserves both multi-value headers and wire order.
    return TransportHeaders.from_pairs(transport_header_pairs(resp.headers.items()))


_TLS_ERRORS = (
    _aiohttp_errors.ClientConnectorCertificateError,
    _aiohttp_errors.ClientConnectorSSLError,
    _aiohttp_errors.ServerFingerprintMismatch,
)
_TIMEOUT_ERRORS = (
    _aiohttp_errors.ConnectionTimeoutError,
    _aiohttp_errors.SocketTimeoutError,
    _aiohttp_errors.ServerTimeoutError,
)
_CONN_ERRORS = (
    _aiohttp_errors.ClientConnectorError,
    _aiohttp_errors.ClientConnectionError,
    _aiohttp_errors.ServerDisconnectedError,
    _aiohttp_errors.ClientPayloadError,
)


def _translate_error(exc: _aiohttp_errors.ClientError) -> TransportError:
    """Map an aiohttp exception to the corresponding pyhaul transport error."""
    if isinstance(exc, _aiohttp_errors.ClientResponseError):
        return TransportHTTPError(str(exc), status_code=exc.status)
    if isinstance(exc, _TLS_ERRORS):
        return TransportTLSError(str(exc))
    if isinstance(exc, (*_TIMEOUT_ERRORS, *_CONN_ERRORS)):
        return TransportConnectionError(str(exc))
    if isinstance(exc, _aiohttp_errors.NonHttpUrlClientError):
        return TransportUnsupportedError(str(exc))
    return TransportError(str(exc))


@contextmanager
def map_aiohttp_transport_errors() -> Iterator[None]:
    """Map :mod:`aiohttp` failures to :mod:`pyhaul.transport.errors` (sync)."""
    try:
        yield
    except TransportError:
        raise
    except _aiohttp_errors.ClientError as e:
        raise _translate_error(e) from e


@asynccontextmanager
async def map_aiohttp_transport_errors_async() -> AsyncIterator[None]:
    """Map :mod:`aiohttp` failures to :mod:`pyhaul.transport.errors` (async)."""
    try:
        yield
    except TransportError:
        raise
    except _aiohttp_errors.ClientError as e:
        raise _translate_error(e) from e


class _AiohttpRequestKwargs(TypedDict, total=False):
    timeout: aiohttp.ClientTimeout
    allow_redirects: bool
    ssl: bool


def _request_options_to_aiohttp_kwargs(
    options: TransportRequestOptions | None,
) -> _AiohttpRequestKwargs:
    if options is None:
        return {}
    kw: _AiohttpRequestKwargs = {}
    if options.timeout is not None:
        t = options.timeout
        if isinstance(t, tuple):
            kw["timeout"] = aiohttp.ClientTimeout(sock_connect=t[0], sock_read=t[1])
        else:
            kw["timeout"] = aiohttp.ClientTimeout(total=t)
    if options.allow_redirects is not None:
        kw["allow_redirects"] = options.allow_redirects
    if options.verify is not None and options.verify is False:
        kw["ssl"] = False
    return kw


class AiohttpTransportResponse(AsyncTransportResponse):
    """Async transport view over an :class:`aiohttp.ClientResponse`."""

    __slots__ = ("_headers", "_resp")

    def __init__(self, resp: aiohttp.ClientResponse) -> None:
        self._resp = resp
        self._headers: TransportHeaders | None = None

    @property
    def status_code(self) -> int:
        """HTTP status code of the response."""
        return int(self._resp.status)

    @property
    def headers(self) -> TransportHeaders:
        """Response headers, lazily parsed on first access."""
        if self._headers is None:
            self._headers = headers_from_aiohttp_response(self._resp)
        return self._headers

    def raise_for_status(self) -> None:
        """Raise :exc:`~pyhaul.transport.errors.TransportHTTPError` for 4xx/5xx responses."""
        with map_aiohttp_transport_errors():
            self._resp.raise_for_status()

    async def aiter_raw_bytes(self, *, chunk_size: int) -> AsyncIterator[bytes]:
        """Yield raw response body chunks without decoding."""
        async with map_aiohttp_transport_errors_async():
            async for chunk in self._resp.content.iter_chunked(chunk_size):
                if chunk:
                    yield chunk


class AsyncAiohttpAdapter:
    """Wrap an :class:`aiohttp.ClientSession` as an :class:`AsyncTransportSession`."""

    __slots__ = ("_session",)

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self._session = session

    @asynccontextmanager
    async def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> AsyncIterator[AsyncTransportResponse]:
        """Open a streaming GET request and yield the response."""
        kwargs = _request_options_to_aiohttp_kwargs(options)
        async with (
            map_aiohttp_transport_errors_async(),
            self._session.get(
                str(url),
                headers=dict(headers),
                auto_decompress=False,
                raise_for_status=False,
                **kwargs,
            ) as resp,
        ):
            yield AiohttpTransportResponse(resp)


def async_aiohttp_transport(session: aiohttp.ClientSession) -> AsyncTransportSession:
    """Shorthand: ``AsyncAiohttpAdapter(session)``."""
    return AsyncAiohttpAdapter(session)
