"""wreq adapter behavior the shared live suites don't reach: error mapping edges and threading."""

from __future__ import annotations

import asyncio
import http.server
import socket
import threading
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

pytest.importorskip("wreq")

import wreq.blocking

from pyhaul._types import Url
from pyhaul.engine import haul
from pyhaul.transport.errors import TransportConnectionError, TransportUnsupportedError
from pyhaul.transport.types import TransportRequestOptions
from pyhaul.transport.wreq_adapter import AsyncWreqAdapter, SyncWreqAdapter

BODY = bytes(range(256)) * 256


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_HEAD(self) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(4 * 1024 * 1024))
        self.end_headers()

    def do_GET(self) -> None:
        if self.path == "/stall":
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b"x" * 10)
            self.wfile.flush()
            time.sleep(2)
            return
        if self.path == "/truncated":
            self.send_response(200)
            self.send_header("Content-Length", "1000")
            self.end_headers()
            self.wfile.write(b"x" * 500)
            self.wfile.flush()
            self.connection.close()
            self.close_connection = True
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(BODY)))
        self.end_headers()
        self.wfile.write(BODY)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def base_url() -> Generator[str]:
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def _drain_sync(adapter: SyncWreqAdapter, url: str, options: TransportRequestOptions | None = None) -> int:
    with adapter.stream_get(Url(url), headers={}, options=options) as resp:
        return sum(memoryview(c).nbytes for c in resp.iter_raw_bytes(chunk_size=8192))


async def _drain_async(adapter: AsyncWreqAdapter, url: str, options: TransportRequestOptions | None = None) -> int:
    async with adapter.stream_get(Url(url), headers={}, options=options) as resp:
        return sum([memoryview(c).nbytes async for c in resp.aiter_raw_bytes(chunk_size=8192)])


def test_sync_read_timeout_mid_body_is_a_connection_error(base_url: str) -> None:
    adapter = SyncWreqAdapter(wreq.blocking.Client())
    with pytest.raises(TransportConnectionError):
        _drain_sync(adapter, f"{base_url}/stall", TransportRequestOptions(timeout=(5.0, 0.3)))


def test_async_read_timeout_mid_body_is_a_connection_error(base_url: str) -> None:
    adapter = AsyncWreqAdapter(wreq.Client())
    with pytest.raises(TransportConnectionError):
        asyncio.run(_drain_async(adapter, f"{base_url}/stall", TransportRequestOptions(timeout=(5.0, 0.3))))


def test_sync_truncated_body_is_a_connection_error(base_url: str) -> None:
    with pytest.raises(TransportConnectionError):
        _drain_sync(SyncWreqAdapter(wreq.blocking.Client()), f"{base_url}/truncated")


def test_connection_refused_is_a_connection_error() -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with pytest.raises(TransportConnectionError):
        _drain_sync(SyncWreqAdapter(wreq.blocking.Client()), f"http://127.0.0.1:{port}/")


def test_unsupported_scheme_is_unsupported() -> None:
    with pytest.raises(TransportUnsupportedError):
        _drain_sync(SyncWreqAdapter(wreq.blocking.Client()), "ftp://127.0.0.1/file")


def test_head_with_large_content_length_reads_no_body(base_url: str) -> None:
    adapter = SyncWreqAdapter(wreq.blocking.Client())
    with adapter.stream_head(Url(f"{base_url}/file"), headers={}) as resp:
        assert resp.status_code == 200
        assert resp.headers.get("content-length") == str(4 * 1024 * 1024)
        assert list(resp.iter_raw_bytes(chunk_size=8192)) == []


def test_one_sync_adapter_shared_across_threads(base_url: str, tmp_path: Path) -> None:
    adapter = SyncWreqAdapter(wreq.blocking.Client())

    def fetch(i: int) -> bytes:
        dest = tmp_path / f"out-{i}.bin"
        haul(f"{base_url}/file", adapter, dest=dest)
        return dest.read_bytes()

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(fetch, range(16)))
    assert all(r == BODY for r in results)
