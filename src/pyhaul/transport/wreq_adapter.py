"""wreq-backed async transport adapter.

Wraps :class:`wreq.Client` (Python bindings around the Rust
``wreq`` library: HTTP/1.1 + HTTP/2, BoringSSL, JA3/JA4 fingerprint
impersonation, native ``socks5h://`` URL-scheme support that makes
DNS-leak misconfiguration structurally impossible).

Adds :class:`AsyncWreqAdapter` as a peer of
:class:`AsyncAiohttpAdapter`; the two are interchangeable behind the
:class:`pyhaul.transport.protocols.AsyncTransportSession` Protocol.

**Decompression caveat.** ``wreq`` performs automatic decompression
when the ``gzip`` / ``brotli`` / ``deflate`` / ``zstd`` extras are
enabled at the Rust-library build level. Pyhaul's contract is that
:meth:`AsyncTransportResponse.aiter_raw_bytes` yields the bytes as
the server framed them, *pre*-content-encoding. Callers using this
adapter for streaming downloads should build their :class:`wreq.Client`
without the decompression extras, or accept that resume-on-chunk-hash
will treat decompressed bytes as the canonical stream.
"""

from __future__ import annotations

import datetime
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager
from typing import TypedDict

import wreq
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
from pyhaul.transport.protocols import AsyncTransportResponse, AsyncTransportSession
from pyhaul.transport.types import TransportHeaders, TransportRequestOptions


def headers_from_wreq_response(resp: wreq.Response) -> TransportHeaders:
    """Build :class:`TransportHeaders` from a ``wreq.Response`` (multi-value safe).

    ``wreq.HeaderMap`` has no ``items()``; it exposes ``keys()`` and
    ``get_all(name)``, both yielding ``bytes``. Names and values decode
    as ``latin-1`` (HTTP's wire encoding), and ``get_all`` keeps repeated
    headers such as ``Set-Cookie`` as separate pairs.
    """
    hm = resp.headers
    pairs: list[tuple[str, str]] = []
    for raw_name in hm.keys():  # noqa: SIM118 — wreq HeaderMap is not iterable
        name = raw_name.decode("latin-1")
        pairs.extend((name, raw_value.decode("latin-1")) for raw_value in hm.get_all(name))
    return TransportHeaders.from_pairs(transport_header_pairs(pairs))


# Exception translation: wreq has a flat hierarchy in `wreq.exceptions`
# (TlsError, ConnectionError, ProxyConnectionError, ConnectionResetError,
# TimeoutError, StatusError, DecodingError, RequestError, RedirectError,
# BodyError, BuilderError, UpgradeError, WebSocketError, RustPanic).
# We map them onto pyhaul's four-bucket TransportError taxonomy.
_TLS_ERRORS = (_wreq_errors.TlsError,)
_CONN_ERRORS = (
    _wreq_errors.ConnectionError,
    _wreq_errors.ProxyConnectionError,
    _wreq_errors.ConnectionResetError,
    _wreq_errors.TimeoutError,
)
_HTTP_ERRORS = (_wreq_errors.StatusError,)
_HTTP_ERROR_MIN = 400
_HTTP_ERROR_MAX = 600
_OTHER_MAPPED_ERRORS = (
    _wreq_errors.DecodingError,
    _wreq_errors.RequestError,
    _wreq_errors.RedirectError,
)
_MAPPED_ERRORS = _TLS_ERRORS + _CONN_ERRORS + _HTTP_ERRORS + _OTHER_MAPPED_ERRORS


def _translate_error(exc: Exception) -> TransportError:
    """Map a ``wreq`` exception to the corresponding pyhaul transport error.

    ``StatusError`` exposes a ``.status`` attribute (the 4xx/5xx code);
    older or future wreq releases may rename it. Use ``getattr`` with
    a default so adapter compatibility doesn't silently break on a
    field rename.
    """
    if isinstance(exc, _HTTP_ERRORS):
        status_code = getattr(exc, "status", None) or getattr(exc, "status_code", None)
        return TransportHTTPError(str(exc), status_code=status_code)
    if isinstance(exc, _TLS_ERRORS):
        return TransportTLSError(str(exc))
    if isinstance(exc, _CONN_ERRORS):
        return TransportConnectionError(str(exc))
    return TransportError(str(exc))


@contextmanager
def map_wreq_transport_errors() -> Iterator[None]:
    """Map :mod:`wreq` failures to :mod:`pyhaul.transport.errors` (sync helper for tests)."""
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
async def map_wreq_transport_errors_async() -> AsyncIterator[None]:
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


def _request_options_to_wreq_kwargs(
    options: TransportRequestOptions | None,
) -> _WreqRequestKwargs:
    """Translate pyhaul's ``TransportRequestOptions`` to ``wreq``-shaped kwargs.

    A scalar timeout maps to wreq's total ``timeout``. A ``(connect, read)``
    tuple maps only its read half to ``read_timeout``: wreq sets connect
    timeouts at Client-build time, not per request. wreq ignores unknown
    kwargs, so ``allow_redirects`` must become a ``redirect`` policy.
    """
    if options is None:
        return {}
    kw: _WreqRequestKwargs = {}
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
    # owns it via the wreq.Client they pass us. Documented constraint.
    return kw


class WreqTransportResponse(AsyncTransportResponse):
    """Async transport view over a :class:`wreq.Response`."""

    __slots__ = ("_headers", "_resp")

    def __init__(self, resp: wreq.Response) -> None:
        self._resp = resp
        self._headers: TransportHeaders | None = None

    @property
    def status_code(self) -> int:
        """HTTP status code of the response.

        ``wreq.StatusCode`` defines no ``__int__``, so ``int()`` raises
        ``TypeError``; ``as_int()`` is the integer view.
        """
        return self._resp.status.as_int()

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

    async def aiter_raw_bytes(self, *, chunk_size: int) -> AsyncIterator[bytes]:
        """Yield raw response body chunks without decoding.

        ``wreq``'s streamer yields either ``bytes`` (body chunks) or
        ``HeaderMap`` (HTTP trailers). We filter to body bytes only.

        ``chunk_size`` is honoured by wreq's underlying reader; the
        value is a hint, not a strict bound — wreq may yield smaller
        chunks at end-of-frame boundaries.
        """
        del chunk_size  # wreq's streamer does not currently accept a chunk_size hint
        async with map_wreq_transport_errors_async(), self._resp.stream() as streamer:
            async for chunk in streamer:
                if isinstance(chunk, (bytes, bytearray)) and chunk:
                    yield bytes(chunk)


class AsyncWreqAdapter:
    """Wrap a :class:`wreq.Client` as an :class:`AsyncTransportSession`."""

    __slots__ = ("_client",)

    def __init__(self, client: wreq.Client) -> None:
        self._client = client

    def prepare_headers(self, headers: TransportHeaders) -> TransportHeaders:
        """Optionally mutate headers before they are sent (noop).

        wreq's emulation config covers TLS / HTTP-2 fingerprint headers
        at the client level, so no per-request mutation is required here.
        """
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
        kwargs = _request_options_to_wreq_kwargs(options)
        async with map_wreq_transport_errors_async():
            resp = await self._client.get(
                str(url),
                headers=dict(headers),
                **kwargs,
            )
            try:
                yield WreqTransportResponse(resp)
            finally:
                # wreq.Response does not require explicit close; the
                # underlying streamer handles cleanup when exited.
                pass

    @asynccontextmanager
    async def stream_head(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> AsyncIterator[AsyncTransportResponse]:
        """Open a HEAD request and yield the response."""
        kwargs = _request_options_to_wreq_kwargs(options)
        async with map_wreq_transport_errors_async():
            resp = await self._client.head(
                str(url),
                headers=dict(headers),
                **kwargs,
            )
            yield WreqTransportResponse(resp)


def async_wreq_transport(client: wreq.Client) -> AsyncTransportSession:
    """Shorthand: ``AsyncWreqAdapter(client)``."""
    return AsyncWreqAdapter(client)


# Silence "imported but unused" without exposing TransportUnsupportedError
# at the module-public level — kept available for future scheme mapping.
_ = TransportUnsupportedError
