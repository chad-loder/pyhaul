"""Pytest fixtures and ``HttpTest`` harness for live HTTP integration tests.

The ``http`` fixture runs each test once per installed HTTP client (niquests,
requests, httpx).  ``HttpTest`` wraps a threaded ``http.server`` on localhost,
a temp destination path, fault injection helpers, and a shortcut for
``engine.haul``.
"""

from __future__ import annotations

import hashlib
import http.server as _http_server
import threading
import time
from collections.abc import Callable, Generator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import pytest

from pyhaul._types import CompleteHaul, Url
from pyhaul.checkpoint import Checkpoint, registry
from pyhaul.engine import haul as engine_haul
from pyhaul.persist import ctrl_path_for
from pyhaul.transport.protocols import TransportSession
from tests.live_backends import LIVE_BACKENDS, close_native, make_native, make_transport


class Content(bytes):
    """``bytes`` subclass that knows its own SHA-256."""

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self).hexdigest()


def repeated(byte: bytes, count: int) -> Content:
    if len(byte) != 1:
        msg = f"repeated() expects a single-byte bytes, got {byte!r}"
        raise ValueError(msg)
    return Content(byte * count)


def deterministic(size: int, *, seed: int = 0) -> Content:
    """Reproducible pseudo-random ``Content`` of length ``size``."""
    return Content(bytes(((i * 37 + seed + 11) & 0xFF) for i in range(size)))


@dataclass(frozen=True, slots=True)
class Request:
    """An incoming request as seen by the test server."""

    index: int
    method: Literal["HEAD", "GET"]
    range: tuple[int, int] | None


BeforeEach = Callable[[Request, "ServerState"], None]


@dataclass
class ServerState:
    """Per-test state for the mock HTTP handler."""

    content: bytes = b""
    etag: str = '"test-etag"'

    force_200_enabled: bool = False
    inject_content_encoding: bool = False
    omit_206_content_range: bool = False
    lie_206_content_range: bool = False
    ignore_range_end: bool = False
    truncate_206_body_at: int | None = None
    truncate_200_body_at: int | None = None
    lie_206_total_length: int | None = None
    get_delay_seconds: float = 0.0

    before_each: BeforeEach | None = None
    request_counter: int = 0

    _lock: threading.Lock = field(default_factory=threading.Lock)


class HttpTest:
    """Fluent test harness for live HTTP integration tests.

    All ``serve``/fault-injection methods return ``self`` for chaining::

        http.serve(b"...").force_200().delay_gets(0.02)
    """

    def __init__(self, url: Url, dest: Path, state: ServerState, *, backend: str) -> None:
        self.url = url
        self.dest = dest
        self._state = state
        self.backend = backend
        self._native: object | None = None

    def _ensure_native(self) -> object:
        if self._native is None:
            self._native = make_native(self.backend)
        return self._native

    @property
    def transport(self) -> TransportSession:
        return make_transport(self.backend, self._ensure_native())

    def close_session(self) -> None:
        if self._native is not None:
            close_native(self._native)
            self._native = None

    # ---- Fluent server configuration ----------------------------------------

    def serve(self, content: bytes) -> HttpTest:
        self._state.content = content
        return self

    def set_etag(self, value: str) -> HttpTest:
        self._state.etag = value
        return self

    def force_200(self) -> HttpTest:
        self._state.force_200_enabled = True
        return self

    def inject_content_encoding_on_206(self) -> HttpTest:
        self._state.inject_content_encoding = True
        return self

    def omit_content_range_on_206(self) -> HttpTest:
        self._state.omit_206_content_range = True
        return self

    def lie_content_range_on_206(self) -> HttpTest:
        self._state.lie_206_content_range = True
        return self

    def ignore_range_end_on_206(self) -> HttpTest:
        self._state.ignore_range_end = True
        return self

    def truncate_206_body_after(self, n: int) -> HttpTest:
        self._state.truncate_206_body_at = n
        return self

    def truncate_200_body_after(self, n: int) -> HttpTest:
        self._state.truncate_200_body_at = n
        return self

    def lie_about_total_length_on_206(self, total: int) -> HttpTest:
        self._state.lie_206_total_length = total
        return self

    def delay_gets(self, seconds: float) -> HttpTest:
        self._state.get_delay_seconds = seconds
        return self

    def before_each(self, fn: BeforeEach) -> HttpTest:
        self._state.before_each = fn
        return self

    # ---- Download convenience -----------------------------------------------

    def haul(self, **kwargs: Any) -> CompleteHaul:
        """``engine.haul`` pre-filled with the fixture's url, dest, and backend."""
        return engine_haul(self.url, self.transport, dest=str(self.dest), **kwargs)

    # ---- Output inspection --------------------------------------------------

    @property
    def output(self) -> bytes:
        return self.dest.read_bytes()

    @property
    def part_path(self) -> Path:
        return self.dest.with_suffix(self.dest.suffix + ".part")

    @property
    def ctrl_path(self) -> Path:
        return ctrl_path_for(self.part_path)

    def read_ctrl(self) -> Checkpoint:
        return registry.load(self.ctrl_path.read_bytes())


# ─── HTTP server ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class _Snapshot:
    content: bytes
    etag: str
    force_200: bool
    inject_content_encoding: bool
    omit_206_content_range: bool
    lie_206_content_range: bool
    ignore_range_end: bool
    truncate_206_body_at: int | None
    truncate_200_body_at: int | None
    lie_206_total_length: int | None
    get_delay_seconds: float


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


def _make_handler(state: ServerState) -> type[_http_server.BaseHTTPRequestHandler]:
    def _begin(method: Literal["HEAD", "GET"], range_hdr: str) -> _Snapshot:
        with state._lock:
            parsed_range = _parse_range(range_hdr, len(state.content))
            request = Request(index=state.request_counter, method=method, range=parsed_range)
            state.request_counter += 1
            if state.before_each is not None:
                state.before_each(request, state)
            return _Snapshot(
                content=state.content,
                etag=state.etag,
                force_200=state.force_200_enabled,
                inject_content_encoding=state.inject_content_encoding,
                omit_206_content_range=state.omit_206_content_range,
                lie_206_content_range=state.lie_206_content_range,
                ignore_range_end=state.ignore_range_end,
                truncate_206_body_at=state.truncate_206_body_at,
                truncate_200_body_at=state.truncate_200_body_at,
                lie_206_total_length=state.lie_206_total_length,
                get_delay_seconds=state.get_delay_seconds,
            )

    class _TestHandler(_http_server.BaseHTTPRequestHandler):
        def do_HEAD(self) -> None:
            snap = _begin("HEAD", "")
            self.send_response(200)
            self.send_header("Content-Length", str(len(snap.content)))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", snap.etag)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()

        def do_GET(self) -> None:
            range_hdr = self.headers.get("Range", "")
            snap = _begin("GET", range_hdr)
            self._serve(snap, range_hdr)

        def _serve(self, snap: _Snapshot, range_hdr: str) -> None:
            if snap.get_delay_seconds:
                time.sleep(snap.get_delay_seconds)

            parsed = _parse_range(range_hdr, len(snap.content))
            if snap.force_200 or parsed is None:
                self._send_full(snap)
                return

            start, end = parsed
            if start >= len(snap.content):
                self._send_416(snap)
                return

            end = min(end, len(snap.content) - 1)
            actual_end = len(snap.content) - 1 if snap.ignore_range_end else end
            chunk = snap.content[start : actual_end + 1]
            total = snap.lie_206_total_length if snap.lie_206_total_length is not None else len(snap.content)

            self.send_response(206)
            if snap.lie_206_content_range:
                self.send_header("Content-Range", f"bytes {start + 1}-{actual_end + 1}/{total}")
            elif not snap.omit_206_content_range:
                self.send_header("Content-Range", f"bytes {start}-{actual_end}/{total}")
            self.send_header("Content-Length", str(len(chunk)))
            self.send_header("ETag", snap.etag)
            if snap.inject_content_encoding:
                self.send_header("Content-Encoding", "gzip")
            self.end_headers()
            if snap.truncate_206_body_at is not None:
                self.wfile.write(chunk[: snap.truncate_206_body_at])
                self.wfile.flush()
                self.connection.close()
                self.close_connection = True
            else:
                self.wfile.write(chunk)

        def _send_full(self, snap: _Snapshot) -> None:
            self.send_response(200)
            self.send_header("Content-Length", str(len(snap.content)))
            self.send_header("ETag", snap.etag)
            self.end_headers()
            if snap.truncate_200_body_at is not None and self.command == "GET":
                self.wfile.write(snap.content[: snap.truncate_200_body_at])
                self.wfile.flush()
                self.connection.close()
                self.close_connection = True
            else:
                self.wfile.write(snap.content)

        def _send_416(self, snap: _Snapshot) -> None:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{len(snap.content)}")
            self.send_header("ETag", snap.etag)
            self.end_headers()

        def log_message(self, format: str, *args: object) -> None:
            pass

    return _TestHandler


class _ThreadingHTTPServer(_http_server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _yield_live_http_harness(tmp_path: Path, backend: str) -> Generator[HttpTest]:
    state = ServerState()
    handler_cls = _make_handler(state)
    srv = _ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    ht: HttpTest | None = None
    try:
        ht = HttpTest(
            url=Url(f"http://127.0.0.1:{port}/file.bin"), dest=tmp_path / "out.bin", state=state, backend=backend
        )
        yield ht
    finally:
        if ht is not None:
            ht.close_session()
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=1.0)
        time.sleep(0.01)  # allow sockets to close


@pytest.fixture(params=LIVE_BACKENDS)
def http(tmp_path: Path, request: pytest.FixtureRequest) -> Generator[HttpTest]:
    """Per-test HTTP harness, once per live transport backend (N-matrix)."""
    pytest.importorskip(request.param)
    yield from _yield_live_http_harness(tmp_path, request.param)
