"""wreq-backed transport adapters, sync and async.

Wraps :class:`wreq.blocking.Client` and :class:`wreq.Client` (Python
bindings around the Rust ``wreq`` library: HTTP/1.1 + HTTP/2, BoringSSL,
JA3/JA4 fingerprint impersonation, native ``socks5h://`` URL-scheme
support that makes DNS-leak misconfiguration structurally impossible).

:class:`SyncWreqAdapter` implements
:class:`pyhaul.transport.protocols.TransportSession` and
:class:`AsyncWreqAdapter` implements
:class:`pyhaul.transport.protocols.AsyncTransportSession`. Both share the
request-option translation, header decoding, and error mapping below.

**Raw bytes.** A default wreq client decompresses gzip, brotli, deflate,
and zstd bodies and drops ``Content-Encoding``. Every request these
adapters make turns that off, so body chunks are the bytes as the server
framed them and byte ranges stay consistent across resumes, whatever the
caller's client is configured to do.

**Connections.** Responses are closed when the caller's ``with`` block
exits. On wreq 0.13+ a fully read response returns its connection to the
pool; on 0.11 closing never reuses the connection. An unread or partly
read body always closes its connection instead of draining it.
"""

from __future__ import annotations

import datetime
from collections.abc import AsyncGenerator, AsyncIterator, Buffer, Generator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from typing import TypedDict

import wreq
import wreq.blocking
from wreq import exceptions as _wreq_errors

from pyhaul._types import Url
from pyhaul.transport._http_common import transport_header_pairs
from pyhaul.transport.errors import (
    TransportConnectionError,
    TransportError,
    TransportHTTPError,
    TransportTLSError,
    TransportUnsupportedError,
)
from pyhaul.transport.protocols import (
    AsyncTransportResponse,
    AsyncTransportSession,
    TransportResponse,
    TransportSession,
)
from pyhaul.transport.types import TransportHeaders, TransportRequestOptions


def headers_from_wreq_response(resp: wreq.Response | wreq.blocking.Response) -> TransportHeaders:
    """Build :class:`TransportHeaders` from a wreq response (multi-value safe).

    ``wreq.HeaderMap.keys()`` and ``get_all(name)`` yield ``bytes`` on 0.11
    and ``memoryview`` on 0.13; ``bytes(...)`` covers both. Names and values
    decode as ``latin-1`` (HTTP's wire encoding), and ``get_all`` keeps
    repeated headers such as ``Set-Cookie`` as separate pairs.
    """
    hm = resp.headers
    pairs: list[tuple[str, str]] = []
    for raw_name in hm.keys():  # noqa: SIM118 — get_all() per name keeps repeated values separate
        name = bytes(raw_name).decode("latin-1")
        pairs.extend((name, bytes(raw_value).decode("latin-1")) for raw_value in hm.get_all(name))
    return TransportHeaders.from_pairs(transport_header_pairs(pairs))


# wreq raises one flat hierarchy from `wreq.exceptions` for both clients
# (TlsError, ConnectionError, ProxyConnectionError, ConnectionResetError,
# TimeoutError, BodyError, DecodingError, StatusError, RequestError,
# RedirectError, BuilderError, UpgradeError, WebSocketError, RustPanic).
_TLS_ERRORS = (_wreq_errors.TlsError,)
# BodyError is a mid-body read failure, including a read timeout on 0.13+.
# DecodingError is a framing or truncation failure: decompression is off.
_CONN_ERRORS = (
    _wreq_errors.ConnectionError,
    _wreq_errors.ProxyConnectionError,
    _wreq_errors.ConnectionResetError,
    _wreq_errors.TimeoutError,
    _wreq_errors.BodyError,
    _wreq_errors.DecodingError,
)
_HTTP_ERRORS = (_wreq_errors.StatusError,)
_UNSUPPORTED_ERRORS = (_wreq_errors.BuilderError,)
_HTTP_ERROR_MIN = 400
_HTTP_ERROR_MAX = 600
_OTHER_MAPPED_ERRORS = (
    _wreq_errors.RequestError,
    _wreq_errors.RedirectError,
)
_MAPPED_ERRORS = _TLS_ERRORS + _CONN_ERRORS + _HTTP_ERRORS + _UNSUPPORTED_ERRORS + _OTHER_MAPPED_ERRORS


def _translate_error(exc: Exception) -> TransportError:
    """Map a ``wreq`` exception to the corresponding pyhaul transport error.

    ``StatusError`` carries no status attribute in 0.11 or 0.13; the
    ``getattr`` lookups pick one up if a later release adds it.
    """
    if isinstance(exc, _HTTP_ERRORS):
        status_code = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        return TransportHTTPError(str(exc), status_code=status_code)
    if isinstance(exc, _TLS_ERRORS):
        return TransportTLSError(str(exc))
    if isinstance(exc, _CONN_ERRORS):
        return TransportConnectionError(str(exc))
    if isinstance(exc, _UNSUPPORTED_ERRORS):
        return TransportUnsupportedError(str(exc))
    return TransportError(str(exc))


@contextmanager
def map_wreq_transport_errors() -> Generator[None]:
    """Map :mod:`wreq` failures to :mod:`pyhaul.transport.errors`."""
    try:
        yield
    except TransportError:
        raise
    except Exception as e:
        if isinstance(e, _wreq_errors.RustPanic):
            raise  # Rust panic = bug, don't bury it
        if isinstance(e, _MAPPED_ERRORS):
            raise _translate_error(e) from e
        raise


@asynccontextmanager
async def map_wreq_transport_errors_async() -> AsyncGenerator[None]:
    """Async variant of :func:`map_wreq_transport_errors`."""
    try:
        yield
    except TransportError:
        raise
    except Exception as e:
        if isinstance(e, _wreq_errors.RustPanic):
            raise
        if isinstance(e, _MAPPED_ERRORS):
            raise _translate_error(e) from e
        raise


class _WreqRequestKwargs(TypedDict, total=False):
    timeout: datetime.timedelta
    read_timeout: datetime.timedelta
    redirect: wreq.redirect.Policy
    gzip: bool
    brotli: bool
    deflate: bool
    zstd: bool


def _request_options_to_wreq_kwargs(
    options: TransportRequestOptions | None,
) -> _WreqRequestKwargs:
    """Translate pyhaul's ``TransportRequestOptions`` to ``wreq``-shaped kwargs.

    A scalar timeout maps to wreq's total ``timeout``. A ``(connect, read)``
    tuple maps only its read half to ``read_timeout``: wreq sets connect
    timeouts at Client-build time, not per request. wreq ignores unknown
    kwargs, so ``allow_redirects`` must become a ``redirect`` policy.
    Decompression is always off per request, overriding the client.
    """
    kw: _WreqRequestKwargs = {"gzip": False, "brotli": False, "deflate": False, "zstd": False}
    if options is None:
        return kw
    if options.timeout is not None:
        t = options.timeout
        if isinstance(t, tuple):
            kw["read_timeout"] = datetime.timedelta(seconds=t[1])
        else:
            kw["timeout"] = datetime.timedelta(seconds=t)
    if options.allow_redirects is not None:
        kw["redirect"] = wreq.redirect.Policy.limited() if options.allow_redirects else wreq.redirect.Policy.none()
    # `options.verify` is intentionally unwired: TLS verification in wreq
    # is configured at Client-build time (not per-request), so caller
    # owns it via the wreq client they pass us. Documented constraint.
    return kw


class _WreqResponseView[R: (wreq.Response, wreq.blocking.Response)]:
    """Status, headers, and status check shared by the sync and async responses."""

    __slots__ = ("_headers", "_resp")

    def __init__(self, resp: R) -> None:
        self._resp: R = resp
        self._headers: TransportHeaders | None = None

    @property
    def status_code(self) -> int:
        """HTTP status code of the response.

        ``wreq.StatusCode`` defines no ``__int__``, so ``int()`` raises
        ``TypeError``; ``as_int()`` is the integer view.
        """
        code: int = self._resp.status.as_int()
        return code

    @property
    def headers(self) -> TransportHeaders:
        """Response headers, lazily parsed on first access."""
        if self._headers is None:
            self._headers = headers_from_wreq_response(self._resp)
        return self._headers

    def raise_for_status(self) -> None:
        """Raise :exc:`~pyhaul.transport.errors.TransportHTTPError` for 4xx/5xx responses."""
        code = self.status_code
        if _HTTP_ERROR_MIN <= code < _HTTP_ERROR_MAX:
            raise TransportHTTPError(
                f"HTTP {code}: {self._resp.url}",
                status_code=code,
            )


class WreqSyncTransportResponse(_WreqResponseView[wreq.blocking.Response], TransportResponse):
    """Sync transport view over a :class:`wreq.blocking.Response`."""

    __slots__ = ()

    def iter_raw_bytes(self, *, chunk_size: int) -> Iterator[Buffer]:
        """Yield raw response body chunks without decoding or copying.

        Body chunks are ``bytes`` before wreq 0.13 and read-only
        ``memoryview`` from 0.13; ``HeaderMap`` frames carry HTTP trailers
        and are skipped. Chunk sizes follow wreq's framing, not ``chunk_size``.
        """
        del chunk_size
        with map_wreq_transport_errors(), self._resp.stream() as streamer:
            for chunk in streamer:
                if not isinstance(chunk, wreq.HeaderMap) and chunk:
                    yield chunk


class WreqTransportResponse(_WreqResponseView[wreq.Response], AsyncTransportResponse):
    """Async transport view over a :class:`wreq.Response`."""

    __slots__ = ()

    async def aiter_raw_bytes(self, *, chunk_size: int) -> AsyncIterator[Buffer]:
        """Async version of :meth:`WreqSyncTransportResponse.iter_raw_bytes`."""
        del chunk_size
        async with map_wreq_transport_errors_async(), self._resp.stream() as streamer:
            async for chunk in streamer:
                if not isinstance(chunk, wreq.HeaderMap) and chunk:
                    yield chunk


class SyncWreqAdapter:
    """Wrap a :class:`wreq.blocking.Client` as a :class:`TransportSession`.

    One client may be shared across threads: wreq's blocking client is
    thread-safe and releases the GIL during network I/O. Calls block until
    they return, so set a read timeout to bound a stalled server.
    """

    __slots__ = ("_client",)

    def __init__(self, client: wreq.blocking.Client) -> None:
        self._client = client

    def prepare_headers(self, headers: TransportHeaders) -> TransportHeaders:
        """Return *headers* unchanged; wreq emulation settings live on the client."""
        return headers

    @contextmanager
    def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> Generator[TransportResponse]:
        """Open a streaming GET request and yield the response."""
        kwargs = _request_options_to_wreq_kwargs(options)
        with map_wreq_transport_errors():
            resp = self._client.get(str(url), headers=dict(headers), **kwargs)
            with resp:
                yield WreqSyncTransportResponse(resp)

    @contextmanager
    def stream_head(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> Generator[TransportResponse]:
        """Open a HEAD request and yield the response."""
        kwargs = _request_options_to_wreq_kwargs(options)
        with map_wreq_transport_errors():
            resp = self._client.head(str(url), headers=dict(headers), **kwargs)
            with resp:
                yield WreqSyncTransportResponse(resp)


class AsyncWreqAdapter:
    """Wrap a :class:`wreq.Client` as an :class:`AsyncTransportSession`."""

    __slots__ = ("_client",)

    def __init__(self, client: wreq.Client) -> None:
        self._client = client

    def prepare_headers(self, headers: TransportHeaders) -> TransportHeaders:
        """Return *headers* unchanged; wreq emulation settings live on the client."""
        return headers

    @asynccontextmanager
    async def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> AsyncGenerator[AsyncTransportResponse]:
        """Open a streaming GET request and yield the response."""
        kwargs = _request_options_to_wreq_kwargs(options)
        async with map_wreq_transport_errors_async():
            resp = await self._client.get(str(url), headers=dict(headers), **kwargs)
            async with resp:
                yield WreqTransportResponse(resp)

    @asynccontextmanager
    async def stream_head(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> AsyncGenerator[AsyncTransportResponse]:
        """Open a HEAD request and yield the response."""
        kwargs = _request_options_to_wreq_kwargs(options)
        async with map_wreq_transport_errors_async():
            resp = await self._client.head(str(url), headers=dict(headers), **kwargs)
            async with resp:
                yield WreqTransportResponse(resp)


def wreq_transport(client: wreq.blocking.Client) -> TransportSession:
    """Shorthand: ``SyncWreqAdapter(client)``."""
    return SyncWreqAdapter(client)


def async_wreq_transport(client: wreq.Client) -> AsyncTransportSession:
    """Shorthand: ``AsyncWreqAdapter(client)``."""
    return AsyncWreqAdapter(client)
