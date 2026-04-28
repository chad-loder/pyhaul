"""Tests for pyhaul.async_engine (haul_async)."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from pyhaul._types import CompleteHaul, ETag, HaulState, Url
from pyhaul.checkpoint import LATEST_VERSION, Checkpoint, registry
from pyhaul.persist import ctrl_path_for, write_atomic
from pyhaul.transport.protocols import AsyncTransportResponse
from pyhaul.transport.types import TransportHeaders, TransportRequestOptions

_TEST_URL = Url("http://example.com/file.bin")

# ---------------------------------------------------------------------------
# Async mock transport
# ---------------------------------------------------------------------------


class AsyncMockResponse:
    """Canned async HTTP response for testing."""

    def __init__(
        self,
        status_code: int,
        headers: dict[str, str],
        body: bytes = b"",
        *,
        chunk_size: int = 0,
    ) -> None:
        self._status_code = status_code
        self._headers = TransportHeaders.from_mapping(headers)
        self._body = body
        self._chunk_hint = chunk_size or len(body) or 8192

    @property
    def status_code(self) -> int:
        return self._status_code

    @property
    def headers(self) -> TransportHeaders:
        return self._headers

    def raise_for_status(self) -> None:
        pass

    async def aiter_raw_bytes(self, *, chunk_size: int) -> AsyncIterator[bytes]:
        cs = chunk_size or self._chunk_hint
        for i in range(0, len(self._body), cs):
            yield self._body[i : i + cs]


class AsyncMockSession:
    """Async mock AsyncTransportSession."""

    def __init__(self) -> None:
        self.responses: list[AsyncMockResponse] = []
        self.requests: list[dict[str, object]] = []
        self._call_index = 0

    def add_response(self, resp: AsyncMockResponse) -> None:
        self.responses.append(resp)

    @asynccontextmanager
    async def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> AsyncIterator[AsyncTransportResponse]:
        self.requests.append({"url": url, "headers": dict(headers), "options": options})
        idx = self._call_index
        self._call_index += 1
        if idx >= len(self.responses):
            msg = f"AsyncMockSession: no response configured for request {idx}"
            raise RuntimeError(msg)
        yield self.responses[idx]


def _make_206(body: bytes, start: int, total: int | None, etag: str = '"test"') -> AsyncMockResponse:
    end = start + len(body) - 1
    total_str = str(total) if total is not None else "*"
    return AsyncMockResponse(
        206,
        {
            "Content-Range": f"bytes {start}-{end}/{total_str}",
            "Content-Length": str(len(body)),
            "ETag": etag,
        },
        body,
    )


def _make_200(body: bytes, etag: str = '"test"') -> AsyncMockResponse:
    return AsyncMockResponse(
        200,
        {"Content-Length": str(len(body)), "ETag": etag},
        body,
    )


def _make_416(total: int, etag: str = '"test"') -> AsyncMockResponse:
    return AsyncMockResponse(
        416,
        {"Content-Range": f"bytes */{total}", "ETag": etag},
        b"",
    )


# ---------------------------------------------------------------------------
# Fresh downloads
# ---------------------------------------------------------------------------


class TestAsyncFresh206KnownTotal:
    @pytest.mark.anyio
    async def test_complete_download(self, tmp_path: Path) -> None:
        from pyhaul.async_engine import haul_async

        body = b"Hello, async world!"
        session = AsyncMockSession()
        session.add_response(_make_206(body, start=0, total=len(body)))

        dest = tmp_path / "out.bin"
        state = HaulState()
        result = await haul_async(_TEST_URL, session, dest=str(dest), state=state)

        assert isinstance(result, CompleteHaul)
        assert state.bytes_read == len(body)
        assert state.is_complete is True
        assert dest.read_bytes() == body
        assert not ctrl_path_for(dest.with_suffix(dest.suffix + ".part")).exists()

    @pytest.mark.anyio
    async def test_on_progress_per_chunk(self, tmp_path: Path) -> None:
        from pyhaul.async_engine import haul_async

        body = b"Hello, async world!"
        session = AsyncMockSession()
        session.add_response(_make_206(body, start=0, total=len(body)))

        dest = tmp_path / "out.bin"
        state = HaulState()
        seen: list[int] = []

        def on_progress(s: HaulState) -> None:
            seen.append(s.valid_length)

        await haul_async(
            _TEST_URL,
            session,
            dest=str(dest),
            state=state,
            chunk_size=4,
            on_progress=on_progress,
        )
        assert len(seen) >= 2
        assert seen[-1] == len(body)
        assert state.reported_length == len(body)

    @pytest.mark.anyio
    async def test_sends_range_header(self, tmp_path: Path) -> None:
        from pyhaul.async_engine import haul_async

        body = b"data"
        session = AsyncMockSession()
        session.add_response(_make_206(body, start=0, total=len(body)))

        dest = tmp_path / "out.bin"
        await haul_async(_TEST_URL, session, dest=str(dest))

        req_headers = session.requests[0]["headers"]
        assert isinstance(req_headers, dict)
        assert req_headers["Range"] == "bytes=0-"


class TestAsyncFresh206UnknownTotal:
    @pytest.mark.anyio
    async def test_streams_to_eof(self, tmp_path: Path) -> None:
        from pyhaul.async_engine import haul_async

        body = b"chunked data without known total"
        session = AsyncMockSession()
        session.add_response(_make_206(body, start=0, total=None))

        dest = tmp_path / "out.bin"
        result = await haul_async(_TEST_URL, session, dest=str(dest))

        assert isinstance(result, CompleteHaul)
        assert dest.read_bytes() == body


class TestAsyncFresh200Fallback:
    @pytest.mark.anyio
    async def test_server_ignores_range(self, tmp_path: Path) -> None:
        from pyhaul.async_engine import haul_async

        body = b"full body from 200"
        session = AsyncMockSession()
        session.add_response(_make_200(body))

        dest = tmp_path / "out.bin"
        result = await haul_async(_TEST_URL, session, dest=str(dest))

        assert isinstance(result, CompleteHaul)
        assert dest.read_bytes() == body


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


class TestAsyncResume206:
    @pytest.mark.anyio
    async def test_resumes_from_cursor(self, tmp_path: Path) -> None:
        from pyhaul.async_engine import haul_async

        full_body = b"AAAAABBBBB"
        first_half = full_body[:5]
        second_half = full_body[5:]

        dest = tmp_path / "out.bin"
        part_path = dest.with_suffix(dest.suffix + ".part")
        ctrl_path = ctrl_path_for(part_path)

        part_path.write_bytes(first_half)
        write_atomic(
            ctrl_path,
            registry.dump(
                Checkpoint(
                    version=LATEST_VERSION,
                    start=0,
                    extent=10,
                    valid_length=5,
                    etag=ETag('"test"'),
                    block_size=8 * 1024 * 1024,
                    hashes=[],
                    reported_length=10,
                )
            ),
        )

        session = AsyncMockSession()
        session.add_response(_make_206(second_half, start=5, total=10))

        result = await haul_async(_TEST_URL, session, dest=str(dest))

        assert isinstance(result, CompleteHaul)
        assert dest.read_bytes() == full_body

        req_headers = session.requests[0]["headers"]
        assert isinstance(req_headers, dict)
        assert req_headers["Range"] == "bytes=5-"
        assert req_headers["If-Range"] == '"test"'


class TestAsyncResumeEtagChanged:
    @pytest.mark.anyio
    async def test_restarts_from_zero_on_200(self, tmp_path: Path) -> None:
        from pyhaul.async_engine import haul_async

        old_body = b"XXXXX"
        new_body = b"YYYYYYYYY"

        dest = tmp_path / "out.bin"
        part_path = dest.with_suffix(dest.suffix + ".part")
        ctrl_path = ctrl_path_for(part_path)

        part_path.write_bytes(old_body)
        write_atomic(
            ctrl_path,
            registry.dump(
                Checkpoint(
                    version=LATEST_VERSION,
                    start=0,
                    extent=5,
                    valid_length=5,
                    etag=ETag('"old-etag"'),
                    block_size=8 * 1024 * 1024,
                    hashes=[],
                    reported_length=5,
                )
            ),
        )

        session = AsyncMockSession()
        session.add_response(_make_200(new_body, etag='"new-etag"'))

        result = await haul_async(_TEST_URL, session, dest=str(dest))

        assert isinstance(result, CompleteHaul)
        assert dest.read_bytes() == new_body


class TestAsyncResume416AlreadyComplete:
    @pytest.mark.anyio
    async def test_416_means_done(self, tmp_path: Path) -> None:
        from pyhaul.async_engine import haul_async

        body = b"complete"
        dest = tmp_path / "out.bin"
        part_path = dest.with_suffix(dest.suffix + ".part")
        ctrl_path = ctrl_path_for(part_path)

        part_path.write_bytes(body)
        write_atomic(
            ctrl_path,
            registry.dump(
                Checkpoint(
                    version=LATEST_VERSION,
                    start=0,
                    extent=len(body),
                    valid_length=len(body),
                    etag=ETag('"test"'),
                    block_size=8 * 1024 * 1024,
                    hashes=[],
                    reported_length=len(body),
                )
            ),
        )

        session = AsyncMockSession()
        session.add_response(_make_416(total=len(body)))

        result = await haul_async(_TEST_URL, session, dest=str(dest))

        assert isinstance(result, CompleteHaul)
        assert dest.read_bytes() == body


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------


class TestAsyncCrashRecovery:
    @pytest.mark.anyio
    async def test_part_longer_than_ctrl(self, tmp_path: Path) -> None:
        """Part file has junk past valid_length from a prior interrupted write."""
        from pyhaul.async_engine import haul_async

        valid_data = b"AAAA"
        junk = b"XX"
        remaining = b"BBBB"
        full_body = valid_data + remaining

        dest = tmp_path / "out.bin"
        part_path = dest.with_suffix(dest.suffix + ".part")
        ctrl_path = ctrl_path_for(part_path)

        part_path.write_bytes(valid_data + junk)
        write_atomic(
            ctrl_path,
            registry.dump(
                Checkpoint(
                    version=LATEST_VERSION,
                    start=0,
                    extent=len(full_body),
                    valid_length=len(valid_data),
                    etag=ETag('"test"'),
                    block_size=8 * 1024 * 1024,
                    hashes=[],
                    reported_length=len(full_body),
                )
            ),
        )

        session = AsyncMockSession()
        session.add_response(_make_206(remaining, start=4, total=8))

        result = await haul_async(_TEST_URL, session, dest=str(dest))

        assert isinstance(result, CompleteHaul)
        assert dest.read_bytes() == full_body
