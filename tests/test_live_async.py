"""Live async integration tests: concurrent haul_async over real HTTP.

Exercises the ``TaskGroup`` + ``Semaphore`` concurrency pattern shown in
``docs/guides/async.md``, against a real threaded HTTP server, once per
installed async client (httpx, aiohttp, niquests).
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import http.server as _http_server
import os
import socket
import threading
import time
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from pyhaul._types import CompleteHaul, HashBuilder, HaulState, PartialHaulError
from pyhaul.async_engine import haul_async

ASYNC_BACKENDS: tuple[str, ...] = ("httpx", "aiohttp", "niquests")

# ---------------------------------------------------------------------------
# Async client factories
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _make_async_client(backend: str) -> AsyncIterator[object]:
    """Create and yield a native async HTTP client for *backend*."""
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


# ---------------------------------------------------------------------------
# Minimal HTTP server (multi-file, each URL → different content)
# ---------------------------------------------------------------------------


def _deterministic(size: int, *, seed: int = 0) -> bytes:
    return bytes(((i * 37 + seed + 11) & 0xFF) for i in range(size))


def _tree_hash(data: bytes) -> str:
    """Compute the tree-hash that ``CompleteHaul.sha256`` uses."""
    import tempfile

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(data)
        p = Path(tmp.name)
    try:
        return HashBuilder.hash_file(p)
    finally:
        p.unlink()


class _ServerFaults:
    """Thread-safe per-file request counter for fault injection."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._hits: dict[int, int] = {}
        self.truncate_first_n: int = 0

    def hit(self, file_idx: int) -> int:
        """Record a request for *file_idx*, return the 0-based attempt number."""
        with self._lock:
            attempt = self._hits.get(file_idx, 0)
            self._hits[file_idx] = attempt + 1
            return attempt

    def should_truncate(self, file_idx: int) -> bool:
        """True if this request should be truncated mid-body."""
        attempt = self.hit(file_idx)
        return attempt < self.truncate_first_n

    def reset_attempts(self) -> None:
        """Clear per-file counters (for stress loops that reuse one server)."""
        with self._lock:
            self._hits.clear()


class _MultiFileHandler(_http_server.BaseHTTPRequestHandler):
    """Serves /0, /1, /2, … each with deterministic content seeded by index."""

    file_size: int = 0
    file_count: int = 0
    faults: _ServerFaults

    def do_GET(self) -> None:
        try:
            idx = int(self.path.strip("/"))
        except (ValueError, IndexError):
            self.send_error(404)
            return

        content = _deterministic(self.file_size, seed=idx)
        range_hdr = self.headers.get("Range", "")
        truncate = self.faults.should_truncate(idx)

        if range_hdr:
            self._send_206(content, range_hdr, truncate=truncate)
        else:
            self._send_200(content, truncate=truncate)

    def _send_200(self, content: bytes, *, truncate: bool = False) -> None:
        self.send_response(200)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("ETag", f'"seed-{len(content)}"')
        if truncate:
            # Announce close before framing the body as keep-alive; avoids slam-close EBADF races.
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if truncate:
            self.wfile.write(content[: len(content) // 2])
            self.wfile.flush()
        else:
            self.wfile.write(content)

    def _send_206(self, content: bytes, range_hdr: str, *, truncate: bool = False) -> None:
        try:
            start_s, end_s = range_hdr.replace("bytes=", "").split("-")
            start = int(start_s)
            end = int(end_s) if end_s else len(content) - 1
        except (ValueError, IndexError):
            self._send_200(content)
            return

        end = min(end, len(content) - 1)
        chunk = content[start : end + 1]

        self.send_response(206)
        self.send_header("Content-Range", f"bytes {start}-{end}/{len(content)}")
        self.send_header("Content-Length", str(len(chunk)))
        self.send_header("ETag", f'"seed-{len(content)}"')
        if truncate:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if truncate:
            self.wfile.write(chunk[: len(chunk) // 2])
            self.wfile.flush()
        else:
            self.wfile.write(chunk)

    def log_message(self, format: str, *args: object) -> None:
        pass


class _ThreadingHTTPServer(_http_server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def shutdown_request(self, request: socket.socket) -> None:  # type: ignore[override]
        """Suppress OSError from double-close after forced truncation."""
        with contextlib.suppress(OSError):
            super().shutdown_request(request)


@pytest.fixture(params=ASYNC_BACKENDS)
def async_backend(request: pytest.FixtureRequest) -> str:
    """Yield each async backend name, skipping if not installed."""
    pytest.importorskip(request.param)
    return str(request.param)


@pytest.fixture
def multi_file_server(tmp_path: Path) -> Generator[tuple[str, Path, int, int, _ServerFaults]]:
    """Spin up an HTTP server serving N deterministic files.

    Yields ``(base_url, dest_dir, file_count, file_size, faults)``.
    """
    file_count = 8
    file_size = 16 * 1024
    faults = _ServerFaults()

    handler_cls = type(
        "_Handler",
        (_MultiFileHandler,),
        {"file_size": file_size, "file_count": file_count, "faults": faults},
    )
    srv = _ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()

    dest_dir = tmp_path / "downloads"
    dest_dir.mkdir()

    try:
        yield f"http://127.0.0.1:{port}", dest_dir, file_count, file_size, faults
    finally:
        srv.shutdown()
        srv.server_close()
        thread.join(timeout=1.0)
        time.sleep(0.01)
        gc.collect()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConcurrentAsyncDownloads:
    """Multiple haul_async calls in a TaskGroup, mirroring the docs pattern."""

    @pytest.mark.anyio
    async def test_taskgroup_all_complete(
        self,
        async_backend: str,
        multi_file_server: tuple[str, Path, int, int, _ServerFaults],
    ) -> None:
        base_url, dest_dir, file_count, file_size, _faults = multi_file_server

        results: dict[int, CompleteHaul] = {}

        async with _make_async_client(async_backend) as client, asyncio.TaskGroup() as tg:
            for i in range(file_count):

                async def _download(idx: int = i) -> None:
                    url = f"{base_url}/{idx}"
                    dest = dest_dir / f"file_{idx}.bin"
                    results[idx] = await haul_async(url, client, dest=str(dest))

                tg.create_task(_download())

        assert len(results) == file_count
        for i in range(file_count):
            expected = _deterministic(file_size, seed=i)
            dest = dest_dir / f"file_{i}.bin"
            assert dest.exists(), f"file_{i}.bin missing"
            assert dest.read_bytes() == expected
            assert results[i].sha256 == _tree_hash(expected)

    @pytest.mark.anyio
    async def test_semaphore_limits_concurrency(
        self,
        async_backend: str,
        multi_file_server: tuple[str, Path, int, int, _ServerFaults],
    ) -> None:
        """Semaphore-bounded concurrency still completes all files."""
        base_url, dest_dir, file_count, file_size, _faults = multi_file_server
        sem = asyncio.Semaphore(3)
        peak_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def _bounded_download(client: object, idx: int) -> CompleteHaul:
            nonlocal peak_concurrent, current_concurrent
            async with sem:
                async with lock:
                    current_concurrent += 1
                    peak_concurrent = max(peak_concurrent, current_concurrent)
                try:
                    url = f"{base_url}/{idx}"
                    dest = dest_dir / f"file_{idx}.bin"
                    return await haul_async(url, client, dest=str(dest))
                finally:
                    async with lock:
                        current_concurrent -= 1

        async with _make_async_client(async_backend) as client, asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_bounded_download(client, i)) for i in range(file_count)]

        for i, task in enumerate(tasks):
            expected = _deterministic(file_size, seed=i)
            dest = dest_dir / f"file_{i}.bin"
            assert dest.read_bytes() == expected
            assert task.result().sha256 == _tree_hash(expected)

        assert peak_concurrent <= 3, f"semaphore violated: peak was {peak_concurrent}"

    @pytest.mark.anyio
    async def test_progress_tracking_per_file(
        self,
        async_backend: str,
        multi_file_server: tuple[str, Path, int, int, _ServerFaults],
    ) -> None:
        """Each concurrent download gets independent state + progress callbacks."""
        base_url, dest_dir, file_count, _file_size, _faults = multi_file_server
        progress_counts: dict[int, int] = {}

        async def _download_with_progress(client: object, idx: int) -> CompleteHaul:
            state = HaulState()
            count = 0

            def on_progress(_st: HaulState) -> None:
                nonlocal count
                count += 1

            url = f"{base_url}/{idx}"
            dest = dest_dir / f"file_{idx}.bin"
            result = await haul_async(
                url,
                client,
                dest=str(dest),
                state=state,
                on_progress=on_progress,
            )
            progress_counts[idx] = count
            return result

        async with _make_async_client(async_backend) as client, asyncio.TaskGroup() as tg:
            for i in range(file_count):
                tg.create_task(_download_with_progress(client, i))

        assert len(progress_counts) == file_count
        for idx, count in progress_counts.items():
            assert count > 0, f"file {idx}: on_progress never called"

    @pytest.mark.anyio
    async def test_truncation_and_resume_concurrent(
        self,
        async_backend: str,
        multi_file_server: tuple[str, Path, int, int, _ServerFaults],
    ) -> None:
        """Server truncates the first request for every file; retry resumes and completes."""
        base_url, dest_dir, _file_count, file_size, faults = multi_file_server
        faults.truncate_first_n = 1
        target_count = 4

        async with _make_async_client(async_backend) as client:
            results: dict[int, CompleteHaul] = {}
            async with asyncio.TaskGroup() as tg:
                for i in range(target_count):

                    async def _download(idx: int = i) -> None:
                        url = f"{base_url}/{idx}"
                        dest = dest_dir / f"file_{idx}.bin"
                        for attempt in range(5):
                            try:
                                results[idx] = await haul_async(url, client, dest=str(dest))
                                break
                            except PartialHaulError:
                                assert attempt < 4, f"file {idx}: still partial after 5 attempts"

                    tg.create_task(_download())

        assert len(results) == target_count
        for i in range(target_count):
            expected = _deterministic(file_size, seed=i)
            assert (dest_dir / f"file_{i}.bin").read_bytes() == expected
            assert results[i].sha256 == _tree_hash(expected)

    @pytest.mark.anyio
    async def test_repeated_truncation_and_resume(
        self,
        async_backend: str,
        multi_file_server: tuple[str, Path, int, int, _ServerFaults],
    ) -> None:
        """Server truncates the first 2 requests per file; multiple resumes converge."""
        base_url, dest_dir, _file_count, file_size, faults = multi_file_server
        faults.truncate_first_n = 2
        target_count = 3

        async with _make_async_client(async_backend) as client:
            results: dict[int, CompleteHaul] = {}
            partial_count: dict[int, int] = {}
            async with asyncio.TaskGroup() as tg:
                for i in range(target_count):

                    async def _download(idx: int = i) -> None:
                        url = f"{base_url}/{idx}"
                        dest = dest_dir / f"file_{idx}.bin"
                        partials = 0
                        for attempt in range(10):
                            try:
                                results[idx] = await haul_async(url, client, dest=str(dest))
                                break
                            except PartialHaulError:
                                partials += 1
                                assert attempt < 9, f"file {idx}: still partial after 10 attempts"
                        partial_count[idx] = partials

                    tg.create_task(_download())

        assert len(results) == target_count
        for i in range(target_count):
            expected = _deterministic(file_size, seed=i)
            assert (dest_dir / f"file_{i}.bin").read_bytes() == expected
            assert partial_count[i] >= 1, f"file {i}: expected at least 1 PartialHaulError"


class TestTruncationConcurrentStress:
    """Tight-loop regression harness for async-engine teardown races.

    Mirrors :meth:`TestConcurrentAsyncDownloads.test_truncation_and_resume_concurrent`
    (``TaskGroup``, shared client, first GET truncated per file, resume loop). Running
    many iterations increases the chance of surfacing an executor cancellation race on
    the final ``fdatasync`` / ``close`` path (e.g. ``EBADF`` in a worker thread while
    the event loop closes the fd).

    **Opt-in:** set ``PYHAUL_TRUNCATION_STRESS_ROUNDS`` to a positive integer (e.g.
    ``200``). If unset or ``0``, the test is skipped so default CI stays fast.

    Example::

        PYHAUL_TRUNCATION_STRESS_ROUNDS=200 \\
          uv run pytest tests/test_live_async.py::TestTruncationConcurrentStress \\
          -q --tb=short
    """

    @pytest.mark.anyio
    async def test_truncation_and_resume_concurrent_stress_loop(
        self,
        multi_file_server: tuple[str, Path, int, int, _ServerFaults],
    ) -> None:
        pytest.importorskip("httpx")
        rounds_s = os.environ.get("PYHAUL_TRUNCATION_STRESS_ROUNDS", "").strip()
        if not rounds_s or not rounds_s.isdigit():
            pytest.skip(
                "Set PYHAUL_TRUNCATION_STRESS_ROUNDS to a positive integer "
                "(e.g. 200) to run truncation TaskGroup stress loop",
            )
        rounds = int(rounds_s)
        if rounds < 1:
            pytest.skip(
                "PYHAUL_TRUNCATION_STRESS_ROUNDS must be >= 1 (omit or set 0 to skip this stress test)",
            )

        base_url, dest_dir, _file_count, file_size, faults = multi_file_server
        target_count = 4

        async with _make_async_client("httpx") as client:
            for round_ix in range(rounds):
                faults.truncate_first_n = 1
                faults.reset_attempts()

                results: dict[int, CompleteHaul] = {}
                async with asyncio.TaskGroup() as tg:
                    for i in range(target_count):

                        async def _download(
                            idx: int = i,
                            *,
                            r: int = round_ix,
                            bucket: dict[int, CompleteHaul] = results,
                        ) -> None:
                            url = f"{base_url}/{idx}"
                            dest = dest_dir / f"stress_r{r}_f{idx}.bin"
                            for attempt in range(5):
                                try:
                                    bucket[idx] = await haul_async(url, client, dest=str(dest))
                                    break
                                except PartialHaulError:
                                    assert attempt < 4, f"round {r} file {idx}: still partial after 5 attempts"

                        tg.create_task(_download())

                assert len(results) == target_count
                for i in range(target_count):
                    expected = _deterministic(file_size, seed=i)
                    path = dest_dir / f"stress_r{round_ix}_f{i}.bin"
                    assert path.read_bytes() == expected
                    assert results[i].sha256 == _tree_hash(expected)
