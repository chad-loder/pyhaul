"""Tests for pyhaul.engine (sync haul)."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

from pyhaul._types import CompleteHaul, ETag, HaulState, PartialHaulError, Url
from pyhaul.persist import CTRL_VERSION, Checkpoint, ctrl_path_for, read_checkpoint, write_atomic
from pyhaul.transport.protocols import TransportResponse
from pyhaul.transport.types import TransportHeaders, TransportRequestOptions

_TEST_URL = Url("http://example.com/file.bin")

# ---------------------------------------------------------------------------
# Mock transport
# ---------------------------------------------------------------------------


class MockResponse:
    """Canned HTTP response for testing."""

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

    def iter_raw_bytes(self, *, chunk_size: int) -> Iterator[bytes]:
        cs = chunk_size or self._chunk_hint
        for i in range(0, len(self._body), cs):
            yield self._body[i : i + cs]


class MockSession:
    """Mock TransportSession that returns pre-configured responses."""

    def __init__(self) -> None:
        self.responses: list[MockResponse] = []
        self.requests: list[dict[str, object]] = []
        self._call_index = 0

    def add_response(self, resp: MockResponse) -> None:
        self.responses.append(resp)

    @contextmanager
    def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> Iterator[TransportResponse]:
        self.requests.append({"url": url, "headers": dict(headers), "options": options})
        idx = self._call_index
        self._call_index += 1
        if idx >= len(self.responses):
            msg = f"MockSession: no response configured for request {idx}"
            raise RuntimeError(msg)
        yield self.responses[idx]


def _make_206_response(body: bytes, start: int, total: int | None, etag: str = '"test"') -> MockResponse:
    end = start + len(body) - 1
    total_str = str(total) if total is not None else "*"
    hdrs: dict[str, str] = {
        "Content-Range": f"bytes {start}-{end}/{total_str}",
        "Content-Length": str(len(body)),
        "ETag": etag,
    }
    return MockResponse(206, hdrs, body)


def _make_200_response(body: bytes, etag: str = '"test"') -> MockResponse:
    hdrs: dict[str, str] = {
        "Content-Length": str(len(body)),
        "ETag": etag,
    }
    return MockResponse(200, hdrs, body)


def _make_416_response(total: int, etag: str = '"test"') -> MockResponse:
    hdrs: dict[str, str] = {
        "Content-Range": f"bytes */{total}",
        "ETag": etag,
    }
    return MockResponse(416, hdrs, b"")


# ---------------------------------------------------------------------------
# Fresh downloads
# ---------------------------------------------------------------------------


class TestFreshDownload206KnownTotal:
    def test_complete_download(self, tmp_path: Path) -> None:
        from pyhaul.engine import haul

        body = b"Hello, world! This is test data."
        session = MockSession()
        session.add_response(_make_206_response(body, start=0, total=len(body)))

        dest = tmp_path / "out.bin"
        state = HaulState()
        result = haul(_TEST_URL, session, dest=str(dest), state=state)

        assert isinstance(result, CompleteHaul)
        assert state.bytes_read == len(body)
        assert state.is_complete is True
        assert state.valid_length == len(body)
        assert dest.read_bytes() == body
        assert not ctrl_path_for(dest.with_suffix(dest.suffix + ".part")).exists()

    def test_sends_range_header(self, tmp_path: Path) -> None:
        from pyhaul.engine import haul

        body = b"data"
        session = MockSession()
        session.add_response(_make_206_response(body, start=0, total=len(body)))

        dest = tmp_path / "out.bin"
        haul(_TEST_URL, session, dest=str(dest))

        assert len(session.requests) == 1
        req_headers = session.requests[0]["headers"]
        assert isinstance(req_headers, dict)
        assert "Range" in req_headers
        assert req_headers["Range"] == "bytes=0-"


class TestFreshDownload206UnknownTotal:
    def test_streams_to_eof(self, tmp_path: Path) -> None:
        from pyhaul.engine import haul

        body = b"chunked data without known total"
        session = MockSession()
        session.add_response(_make_206_response(body, start=0, total=None))

        dest = tmp_path / "out.bin"
        result = haul(_TEST_URL, session, dest=str(dest))

        assert isinstance(result, CompleteHaul)
        assert dest.read_bytes() == body


class TestFreshDownload200Fallback:
    def test_server_ignores_range(self, tmp_path: Path) -> None:
        from pyhaul.engine import haul

        body = b"full body from 200"
        session = MockSession()
        session.add_response(_make_200_response(body))

        dest = tmp_path / "out.bin"
        result = haul(_TEST_URL, session, dest=str(dest))

        assert isinstance(result, CompleteHaul)
        assert dest.read_bytes() == body


# ---------------------------------------------------------------------------
# Resume
# ---------------------------------------------------------------------------


class TestResume206:
    def test_resumes_from_cursor(self, tmp_path: Path) -> None:
        from pyhaul.engine import haul

        full_body = b"AAAAABBBBB"
        first_half = full_body[:5]
        second_half = full_body[5:]

        dest = tmp_path / "out.bin"
        part_path = dest.with_suffix(dest.suffix + ".part")
        ctrl_path = ctrl_path_for(part_path)

        part_path.write_bytes(first_half)
        write_atomic(
            ctrl_path,
            Checkpoint(
                version=CTRL_VERSION,
                start=0,
                extent=10,
                valid_length=5,
                etag=ETag('"test"'),
                resource_length=10,
            ),
        )

        session = MockSession()
        session.add_response(_make_206_response(second_half, start=5, total=10))

        result = haul(_TEST_URL, session, dest=str(dest))

        assert isinstance(result, CompleteHaul)
        assert dest.read_bytes() == full_body

        req_headers = session.requests[0]["headers"]
        assert isinstance(req_headers, dict)
        assert req_headers["Range"] == "bytes=5-"
        assert req_headers["If-Range"] == '"test"'


class TestResumeEtagChanged:
    def test_restarts_from_zero_on_200(self, tmp_path: Path) -> None:
        from pyhaul.engine import haul

        old_body = b"XXXXX"
        new_body = b"YYYYYYYYY"

        dest = tmp_path / "out.bin"
        part_path = dest.with_suffix(dest.suffix + ".part")
        ctrl_path = ctrl_path_for(part_path)

        part_path.write_bytes(old_body)
        write_atomic(
            ctrl_path,
            Checkpoint(
                version=CTRL_VERSION,
                start=0,
                extent=5,
                valid_length=5,
                etag=ETag('"old-etag"'),
                resource_length=5,
            ),
        )

        session = MockSession()
        session.add_response(_make_200_response(new_body, etag='"new-etag"'))

        result = haul(_TEST_URL, session, dest=str(dest))

        assert isinstance(result, CompleteHaul)
        assert dest.read_bytes() == new_body


class TestResume416AlreadyComplete:
    def test_416_means_done(self, tmp_path: Path) -> None:
        from pyhaul.engine import haul

        body = b"complete"
        dest = tmp_path / "out.bin"
        part_path = dest.with_suffix(dest.suffix + ".part")
        ctrl_path = ctrl_path_for(part_path)

        part_path.write_bytes(body)
        write_atomic(
            ctrl_path,
            Checkpoint(
                version=CTRL_VERSION,
                start=0,
                extent=len(body),
                valid_length=len(body),
                etag=ETag('"test"'),
                resource_length=len(body),
            ),
        )

        session = MockSession()
        session.add_response(_make_416_response(total=len(body)))

        result = haul(_TEST_URL, session, dest=str(dest))

        assert isinstance(result, CompleteHaul)
        assert dest.read_bytes() == body


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------


class TestResumeNoEtag:
    def test_resumes_without_if_range(self, tmp_path: Path) -> None:
        """Resume works when no ETag was stored — Range only, no If-Range."""
        from pyhaul.engine import haul

        full_body = b"0123456789"
        first = full_body[:3]
        rest = full_body[3:]

        dest = tmp_path / "out.bin"
        part_path = dest.with_suffix(dest.suffix + ".part")
        ctrl_path = ctrl_path_for(part_path)

        part_path.write_bytes(first)
        write_atomic(
            ctrl_path,
            Checkpoint(
                version=CTRL_VERSION,
                start=0,
                extent=10,
                valid_length=3,
                etag=ETag(""),
                resource_length=10,
            ),
        )

        session = MockSession()
        session.add_response(_make_206_response(rest, start=3, total=10))

        result = haul(_TEST_URL, session, dest=str(dest))

        assert isinstance(result, CompleteHaul)
        assert dest.read_bytes() == full_body

        req_headers = session.requests[0]["headers"]
        assert isinstance(req_headers, dict)
        assert req_headers["Range"] == "bytes=3-"
        assert "If-Range" not in req_headers


class TestResume416ResourceShrank:
    def test_416_resource_shrank(self, tmp_path: Path) -> None:
        """416 with instance_length < valid_length resets the checkpoint."""
        from pyhaul.engine import haul

        dest = tmp_path / "out.bin"
        part_path = dest.with_suffix(dest.suffix + ".part")
        ctrl_path = ctrl_path_for(part_path)

        part_path.write_bytes(b"AAAAAAAAAA")
        write_atomic(
            ctrl_path,
            Checkpoint(
                version=CTRL_VERSION,
                start=0,
                extent=10,
                valid_length=10,
                etag=ETag('"test"'),
                resource_length=10,
            ),
        )

        session = MockSession()
        session.add_response(_make_416_response(total=5))

        state = HaulState()
        with pytest.raises(PartialHaulError):
            haul(_TEST_URL, session, dest=str(dest), state=state)

        assert state.valid_length == 0

        cp = read_checkpoint(ctrl_path)
        assert cp.valid_length == 0


class TestRestart200SizeChange:
    def test_200_with_larger_resource(self, tmp_path: Path) -> None:
        """200 on resume where new Content-Length is larger: restart, extend file."""
        from pyhaul.engine import haul

        new_body = b"AABBCCDDEE"

        dest = tmp_path / "out.bin"
        part_path = dest.with_suffix(dest.suffix + ".part")
        ctrl_path = ctrl_path_for(part_path)

        part_path.write_bytes(b"XXXXX")
        write_atomic(
            ctrl_path,
            Checkpoint(
                version=CTRL_VERSION,
                start=0,
                extent=5,
                valid_length=5,
                etag=ETag('"old"'),
                resource_length=5,
            ),
        )

        session = MockSession()
        session.add_response(_make_200_response(new_body, etag='"new"'))

        result = haul(_TEST_URL, session, dest=str(dest))

        assert isinstance(result, CompleteHaul)
        assert dest.read_bytes() == new_body

    def test_200_with_smaller_resource(self, tmp_path: Path) -> None:
        """200 on resume where new Content-Length is smaller: restart, truncate."""
        from pyhaul.engine import haul

        new_body = b"AB"

        dest = tmp_path / "out.bin"
        part_path = dest.with_suffix(dest.suffix + ".part")
        ctrl_path = ctrl_path_for(part_path)

        part_path.write_bytes(b"XXXXXXXXXXXX")
        write_atomic(
            ctrl_path,
            Checkpoint(
                version=CTRL_VERSION,
                start=0,
                extent=12,
                valid_length=12,
                etag=ETag('"old"'),
                resource_length=12,
            ),
        )

        session = MockSession()
        session.add_response(_make_200_response(new_body, etag='"new"'))

        result = haul(_TEST_URL, session, dest=str(dest))

        assert isinstance(result, CompleteHaul)
        assert dest.read_bytes() == new_body

    def test_200_chunked_no_truncate_during_stream(self, tmp_path: Path) -> None:
        """200 with no Content-Length: don't truncate during stream, trim at end."""
        from pyhaul.engine import haul

        new_body = b"SHORT"

        dest = tmp_path / "out.bin"
        part_path = dest.with_suffix(dest.suffix + ".part")
        ctrl_path = ctrl_path_for(part_path)

        part_path.write_bytes(b"MUCH_LONGER_OLD_DATA")
        write_atomic(
            ctrl_path,
            Checkpoint(
                version=CTRL_VERSION,
                start=0,
                extent=20,
                valid_length=20,
                etag=ETag('"old"'),
                resource_length=20,
            ),
        )

        session = MockSession()
        hdrs: dict[str, str] = {"ETag": '"new"'}
        session.add_response(MockResponse(200, hdrs, new_body))

        result = haul(_TEST_URL, session, dest=str(dest))

        assert isinstance(result, CompleteHaul)
        assert dest.read_bytes() == new_body


class TestCrashRecovery:
    def test_part_longer_than_ctrl(self, tmp_path: Path) -> None:
        """Part file has junk past valid_length from a prior interrupted write."""
        from pyhaul.engine import haul

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
            Checkpoint(
                version=CTRL_VERSION,
                start=0,
                extent=len(full_body),
                valid_length=len(valid_data),
                etag=ETag('"test"'),
                resource_length=len(full_body),
            ),
        )

        session = MockSession()
        session.add_response(_make_206_response(remaining, start=4, total=8))

        result = haul(_TEST_URL, session, dest=str(dest))

        assert isinstance(result, CompleteHaul)
        assert dest.read_bytes() == full_body


# ---------------------------------------------------------------------------
# Durability ordering
# ---------------------------------------------------------------------------


class TestDurabilityOrdering:
    def test_fdatasync_before_ctrl_write(self, tmp_path: Path) -> None:
        """The engine must fdatasync the .part fd before writing .ctrl."""
        from pyhaul.engine import haul

        body = b"A" * (2 * 1024 * 1024)
        session = MockSession()
        session.add_response(_make_206_response(body, start=0, total=len(body)))

        dest = tmp_path / "out.bin"

        call_log: list[str] = []
        real_datasync = os.fdatasync if hasattr(os, "fdatasync") else os.fsync

        def tracking_datasync(fd: int) -> None:
            call_log.append("datasync")
            real_datasync(fd)

        real_write_atomic = write_atomic

        def tracking_write_atomic(*args: object, **kwargs: object) -> None:
            call_log.append("ctrl_write")
            real_write_atomic(*args, **kwargs)  # type: ignore[arg-type]

        with (
            patch("pyhaul._engine_common.datasync", tracking_datasync),
            patch("pyhaul._engine_common.write_atomic", tracking_write_atomic),
        ):
            result = haul(
                _TEST_URL,
                session,
                dest=str(dest),
                flush_every=1 << 20,
            )

        assert isinstance(result, CompleteHaul)

        datasync_positions = [i for i, x in enumerate(call_log) if x == "datasync"]
        ctrl_positions = [i for i, x in enumerate(call_log) if x == "ctrl_write"]

        assert len(datasync_positions) >= 1, "expected at least one fdatasync call"

        for ctrl_pos in ctrl_positions:
            preceding = [d for d in datasync_positions if d < ctrl_pos]
            assert len(preceding) >= 1, f"ctrl write at position {ctrl_pos} had no preceding fdatasync"
