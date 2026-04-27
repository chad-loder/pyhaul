import hashlib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

import pytest

from pyhaul._types import (
    ControlFileError,
    ETag,
    Url,
)
from pyhaul.checkpoint import LATEST_VERSION, Checkpoint, registry
from pyhaul.engine import haul
from pyhaul.persist import ctrl_path_for, write_atomic
from pyhaul.transport.protocols import TransportResponse
from pyhaul.transport.types import TransportHeaders, TransportRequestOptions

_TEST_URL = Url("http://example.com/file.bin")


class MockResponse:
    def __init__(self, status_code: int, headers: dict[str, str], body: bytes):
        self.status_code = status_code
        self.headers = TransportHeaders.from_mapping(headers)
        self._body = body

    def raise_for_status(self) -> None:
        pass

    def iter_raw_bytes(self, *, chunk_size: int) -> Iterator[bytes]:
        yield self._body


class MockSession:
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


def _get_hash(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def test_resume_tail_corruption_detected(tmp_path: Path) -> None:
    """
    CONFIRMATION: If the tail of the .part file is corrupted, the engine
    now detects it via the tail_hash and raises ControlFileError.
    """
    dest = tmp_path / "out.bin"
    part_path = dest.with_suffix(dest.suffix + ".part")
    ctrl_path = ctrl_path_for(part_path)

    # 1. Simulate a download with 1 full block (10 bytes) and a partial tail (5 bytes)
    block1 = b"0123456789"
    tail_good = b"ABCDE"
    tail_bad = b"AXCXE"  # Corrupted on disk
    to_arrive = b"FGHIJ"

    part_path.write_bytes(block1 + tail_bad)

    cp = Checkpoint(
        version=LATEST_VERSION,
        start=0,
        extent=20,
        valid_length=15,
        etag=ETag('"v1"'),
        block_size=10,
        hashes=[_get_hash(block1)],
        tail_hash=_get_hash(tail_good),  # We thought it was GOOD when we saved ctrl
        resource_length=20,
    )
    write_atomic(ctrl_path, registry.dump(cp))

    # 2. Resume
    session = MockSession()
    session.add_response(_make_206_response(to_arrive, start=15, total=20, etag='"v1"'))

    with pytest.raises(ControlFileError, match="integrity error: local tail corruption detected"):
        haul(_TEST_URL, session, dest=str(dest))


def test_resume_tail_truncation_detected(tmp_path: Path) -> None:
    """
    CONFIRMATION: If the tail of the .part file is truncated (shorter than valid_length),
    the engine correctly detects this and raises ControlFileError.
    """
    dest = tmp_path / "out.bin"
    part_path = dest.with_suffix(dest.suffix + ".part")
    ctrl_path = ctrl_path_for(part_path)

    # valid_length is 15, but file only has 12 bytes
    part_path.write_bytes(b"A" * 12)

    cp = Checkpoint(
        version=LATEST_VERSION,
        start=0,
        extent=20,
        valid_length=15,
        etag=ETag('"v1"'),
        block_size=10,
        hashes=[b"H" * 32],
    )
    write_atomic(ctrl_path, registry.dump(cp))

    session = MockSession()
    session.add_response(_make_206_response(b"B" * 5, start=15, total=20, etag='"v1"'))

    with pytest.raises(ControlFileError, match="could not re-read"):
        haul(_TEST_URL, session, dest=str(dest))
