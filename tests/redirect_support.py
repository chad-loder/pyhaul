"""Shared helpers for live redirect integration tests.

``haul()`` / ``haul_async()`` do not yet thread :class:`~pyhaul.transport.types.TransportRequestOptions`
from callers, so adapters usually rely on each HTTP client's defaults. Tests pin
``allow_redirects`` by wrapping a concrete adapter and merging
:class:`~pyhaul.transport.types.TransportRequestOptions` on every ``stream_get``, giving every
backend an explicit redirect-on vs redirect-off path without changing production APIs.
"""

from __future__ import annotations

import gc
import http.server as http_server
import threading
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from typing import cast
from urllib.parse import urlparse

from pyhaul._types import Url
from pyhaul.transport.protocols import (
    AsyncTransportResponse,
    AsyncTransportSession,
    TransportResponse,
    TransportSession,
)
from pyhaul.transport.types import TransportHeaders, TransportRequestOptions
from tests.live_backends import make_native, make_transport

CONTENT = b"redirect-matrix-download-body"
ETAG = '"matrix-redirect"'

LIVE_ASYNC_BACKENDS: tuple[str, ...] = ("httpx", "aiohttp", "niquests")


def _parse_range(header: str, content_len: int) -> tuple[int, int] | None:
    if not header:
        return None
    try:
        start_s, end_s = header.replace("bytes=", "").split("-")
        start = int(start_s)
        end = int(end_s) if end_s else content_len - 1
    except (ValueError, IndexError):
        return None
    return start, end


class _RedirectFollowHandler(http_server.BaseHTTPRequestHandler):
    """``GET /redirect`` → 302 to ``/final``; ``GET /final`` serves :data:`CONTENT` with Range."""

    def do_GET(self) -> None:
        path = urlparse(self.path).path.rstrip("/") or "/"
        host, port = cast("tuple[str, int]", self.server.server_address)
        base = f"http://{host}:{port}"

        if path == "/redirect":
            self.send_response(302)
            self.send_header("Location", f"{base}/final")
            self.end_headers()
            return

        if path != "/final":
            self.send_error(404)
            return

        range_hdr = self.headers.get("Range", "")
        parsed = _parse_range(range_hdr, len(CONTENT))
        if parsed is None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(CONTENT)))
            self.send_header("ETag", ETAG)
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            self.wfile.write(CONTENT)
            return

        start, end = parsed
        if start >= len(CONTENT):
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{len(CONTENT)}")
            self.send_header("ETag", ETAG)
            self.end_headers()
            return

        end = min(end, len(CONTENT) - 1)
        chunk = CONTENT[start : end + 1]
        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(CONTENT)}")
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("ETag", ETAG)
        self.end_headers()
        self.wfile.write(chunk)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        pass


class _ThreadingHTTPServer(http_server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


@contextmanager
def redirect_server_context() -> Iterator[tuple[Url, bytes]]:
    """Serve ``GET /redirect`` → 302 ``/final`` until context exits."""
    srv = _ThreadingHTTPServer(("127.0.0.1", 0), _RedirectFollowHandler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        port = srv.server_address[1]
        yield Url(f"http://127.0.0.1:{port}/redirect"), CONTENT
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=2.0)
        time.sleep(0.01)
        gc.collect()


def merge_allow_redirects(options: TransportRequestOptions | None, *, pin: bool) -> TransportRequestOptions:
    """Force ``allow_redirects`` while preserving any other fields from *options* (tests only)."""
    if options is None:
        return TransportRequestOptions(allow_redirects=pin)
    return TransportRequestOptions(
        timeout=options.timeout,
        verify=options.verify,
        allow_redirects=pin,
    )


class PinnedRedirectSyncTransport:
    """Wraps a sync adapter and merges ``TransportRequestOptions(allow_redirects=...)``."""

    __slots__ = ("_allow_redirects", "_inner")

    def __init__(self, inner: TransportSession, *, allow_redirects: bool) -> None:
        self._inner = inner
        self._allow_redirects = allow_redirects

    def prepare_headers(self, headers: TransportHeaders) -> TransportHeaders:
        """Forward to the wrapped adapter."""
        return self._inner.prepare_headers(headers)

    @contextmanager
    def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> Iterator[TransportResponse]:
        """Pin redirect policy, then open a streaming GET."""
        merged = merge_allow_redirects(options, pin=self._allow_redirects)
        with self._inner.stream_get(url, headers=headers, options=merged) as resp:
            yield resp


class PinnedRedirectAsyncTransport:
    """Wraps an async adapter and merges ``TransportRequestOptions(allow_redirects=...)``."""

    __slots__ = ("_allow_redirects", "_inner")

    def __init__(self, inner: AsyncTransportSession, *, allow_redirects: bool) -> None:
        self._inner = inner
        self._allow_redirects = allow_redirects

    def prepare_headers(self, headers: TransportHeaders) -> TransportHeaders:
        """Forward to the wrapped adapter."""
        return self._inner.prepare_headers(headers)

    def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> AbstractAsyncContextManager[AsyncTransportResponse]:
        """Pin redirect policy, then open a streaming GET."""
        merged = merge_allow_redirects(options, pin=self._allow_redirects)

        @asynccontextmanager
        async def _cm() -> AsyncIterator[AsyncTransportResponse]:
            async with self._inner.stream_get(url, headers=headers, options=merged) as resp:
                yield resp

        return _cm()


def build_sync_pinned_transport(backend: str, *, allow_redirects: bool) -> tuple[object, PinnedRedirectSyncTransport]:
    """Vanilla native client + adapter wrapped so redirect policy is explicit."""
    native = make_native(backend)
    inner = make_transport(backend, native)
    return native, PinnedRedirectSyncTransport(inner, allow_redirects=allow_redirects)


def make_async_inner_transport(backend: str, native: object) -> AsyncTransportSession:
    """Mirror :func:`tests.live_backends.make_transport` for async sessions."""
    if backend == "httpx":
        from pyhaul.transport.httpx_adapter import AsyncHttpxAdapter

        return AsyncHttpxAdapter(native)  # type: ignore[arg-type]
    if backend == "aiohttp":
        from pyhaul.transport.aiohttp_adapter import AsyncAiohttpAdapter

        return AsyncAiohttpAdapter(native)  # type: ignore[arg-type]
    if backend == "niquests":
        from pyhaul.transport.niquests_adapter import AsyncNiquestsAdapter

        return AsyncNiquestsAdapter(native)  # type: ignore[arg-type]
    msg = f"unknown async backend {backend!r}"
    raise ValueError(msg)


@asynccontextmanager
async def async_native_session(backend: str) -> AsyncIterator[object]:
    """Yield an async HTTP client for *backend* (caller must ``importorskip`` first)."""
    if backend == "httpx":
        import httpx

        async with httpx.AsyncClient() as client:
            yield client
    elif backend == "aiohttp":
        import aiohttp

        async with aiohttp.ClientSession() as session:
            yield session
    elif backend == "niquests":
        import niquests

        async with niquests.AsyncSession() as session:
            yield session
    else:
        msg = f"unknown async backend {backend!r}"
        raise ValueError(msg)
