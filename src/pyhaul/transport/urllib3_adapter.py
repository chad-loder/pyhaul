"""urllib3-backed sync transport adapter."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import TypedDict

import urllib3
import urllib3.exceptions as _urllib3_exceptions

from pyhaul._types import Url
from pyhaul.transport._http_common import transport_header_pairs
from pyhaul.transport.errors import (
    TransportConnectionError,
    TransportError,
    TransportHTTPError,
    TransportTLSError,
)
from pyhaul.transport.protocols import TransportResponse, TransportSession
from pyhaul.transport.types import TransportHeaders, TransportRequestOptions

_HTTP_400 = 400


def headers_from_urllib3_response(resp: urllib3.HTTPResponse) -> TransportHeaders:
    """Build :class:`TransportHeaders` from a urllib3 response (multi-value safe)."""
    h = resp.headers
    # getlist() preserves multi-value headers; order is grouped-by-name
    # (HTTPHeaderDict doesn't track interleaved wire order).
    pairs = transport_header_pairs((name, val) for name in h for val in h.getlist(name))
    return TransportHeaders.from_pairs(pairs)


@contextmanager
def map_urllib3_transport_errors() -> Iterator[None]:
    """Map :mod:`urllib3` failures to :mod:`pyhaul.transport.errors`."""
    try:
        yield
    except TransportError:
        raise
    except _urllib3_exceptions.SSLError as e:
        raise TransportTLSError(str(e)) from e
    except _urllib3_exceptions.ProxyError as e:
        raise TransportConnectionError(str(e)) from e
    except _urllib3_exceptions.ConnectTimeoutError as e:
        raise TransportConnectionError(str(e)) from e
    except _urllib3_exceptions.ReadTimeoutError as e:
        raise TransportConnectionError(str(e)) from e
    except _urllib3_exceptions.IncompleteRead as e:
        raise TransportConnectionError(str(e)) from e
    except _urllib3_exceptions.ProtocolError as e:
        raise TransportConnectionError(str(e)) from e
    except _urllib3_exceptions.DecodeError as e:
        raise TransportError(str(e)) from e
    except _urllib3_exceptions.HTTPError as e:
        raise TransportConnectionError(str(e)) from e


class _Urllib3UrlOpenKwargs(TypedDict, total=False):
    timeout: float | urllib3.Timeout
    preload_content: bool
    redirect: bool


def _build_urlopen_kwargs(
    options: TransportRequestOptions | None,
) -> _Urllib3UrlOpenKwargs:
    kw: _Urllib3UrlOpenKwargs = {"preload_content": False, "redirect": True}
    if options is None:
        return kw
    if options.timeout is not None:
        t = options.timeout
        kw["timeout"] = urllib3.Timeout(connect=t[0], read=t[1]) if isinstance(t, tuple) else t
    if options.allow_redirects is not None:
        kw["redirect"] = options.allow_redirects
    return kw


class Urllib3TransportResponse(TransportResponse):
    """Transport view over a :class:`urllib3.HTTPResponse`."""

    __slots__ = ("_headers", "_resp")

    def __init__(self, resp: urllib3.HTTPResponse) -> None:
        self._resp = resp
        self._headers: TransportHeaders | None = None

    @property
    def status_code(self) -> int:
        """HTTP status code of the response."""
        return self._resp.status

    @property
    def headers(self) -> TransportHeaders:
        """Response headers, lazily parsed on first access."""
        if self._headers is None:
            self._headers = headers_from_urllib3_response(self._resp)
        return self._headers

    def raise_for_status(self) -> None:
        """Raise :exc:`~pyhaul.transport.errors.TransportHTTPError` for 4xx/5xx responses."""
        if self._resp.status >= _HTTP_400:
            raise TransportHTTPError(
                f"HTTP {self._resp.status}",
                status_code=self._resp.status,
            )

    def iter_raw_bytes(self, *, chunk_size: int) -> Iterator[bytes]:
        """Yield raw response body chunks without decoding."""
        with map_urllib3_transport_errors():
            yield from self._resp.stream(chunk_size, decode_content=False)

    def close(self) -> None:
        """Close the underlying urllib3 response."""
        close_fn = getattr(self._resp, "close", None)
        if callable(close_fn):
            close_fn()


class Urllib3Adapter:
    """Wrap a :class:`urllib3.PoolManager` as a :class:`TransportSession`."""

    __slots__ = ("_pool",)

    def __init__(self, pool: urllib3.PoolManager) -> None:
        self._pool = pool

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
        kw = _build_urlopen_kwargs(options)
        with map_urllib3_transport_errors():
            resp = self._pool.request(
                "GET",
                str(url),
                headers=dict(headers),
                **kw,
            )
        transport_resp = Urllib3TransportResponse(resp)
        try:
            yield transport_resp
        finally:
            transport_resp.close()
            resp.release_conn()

    @contextmanager
    def stream_head(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> Iterator[TransportResponse]:
        """Open a HEAD request and yield the response."""
        kw = _build_urlopen_kwargs(options)
        with map_urllib3_transport_errors():
            resp = self._pool.request(
                "HEAD",
                str(url),
                headers=dict(headers),
                **kw,
            )
        transport_resp = Urllib3TransportResponse(resp)
        try:
            yield transport_resp
        finally:
            transport_resp.close()
            resp.release_conn()


def urllib3_transport(pool: urllib3.PoolManager) -> TransportSession:
    """Shorthand: ``Urllib3Adapter(pool)``."""
    return Urllib3Adapter(pool)
