"""HTTP edge-case and pathology tests for the single-range download engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from pyhaul._types import CompleteHaul, HashBuilder, PartialHaulError
from pyhaul.checkpoint import LATEST_VERSION, Checkpoint, registry
from pyhaul.persist import write_atomic
from tests.conftest import Request, ServerState, deterministic

if TYPE_CHECKING:
    from tests.conftest import HttpTest


def _get_expected_hash(payload: bytes, block_size: int = 8 * 1024 * 1024) -> str:
    import tempfile
    from pathlib import Path

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    try:
        return HashBuilder.hash_file(tmp_path, block_size=block_size)
    finally:
        tmp_path.unlink()


# ---------------------------------------------------------------------------
# Pathology 1: Mid-body connection reset on 206
# ---------------------------------------------------------------------------


def test_short_read_on_206_raises_partial(http: HttpTest) -> None:
    """Server sends headers claiming full range, then closes the socket
    halfway through.  Must raise PartialHaulError (not a raw transport exception)."""
    data = deterministic(4 * 1024, seed=3)
    http.serve(data).truncate_206_body_after(len(data) // 2)

    with pytest.raises(PartialHaulError):
        http.haul()

    assert not http.dest.exists(), "dest must not exist after truncated 206"


# ---------------------------------------------------------------------------
# Pathology 2: 200 fallback that dies mid-stream
# ---------------------------------------------------------------------------


def test_200_truncated_mid_stream_raises_partial(http: HttpTest) -> None:
    """Server returns 200 (Range ignored) then closes mid-body.
    Must raise PartialHaulError (not a raw transport exception)."""
    data = deterministic(8 * 1024, seed=5)
    http.serve(data).force_200().truncate_200_body_after(len(data) // 2)

    with pytest.raises(PartialHaulError):
        http.haul()

    assert not http.dest.exists()


# ---------------------------------------------------------------------------
# Pathology 2b: Truncate-then-resume round trip (206)
# ---------------------------------------------------------------------------


def test_206_truncation_then_resume_completes(http: HttpTest) -> None:
    """Server truncates the first request mid-body.  Second call resumes
    from the checkpoint and completes.  This is the core retry contract:
    callers only need ``except PartialHaulError``."""
    data = deterministic(16 * 1024, seed=42)
    http.serve(data).truncate_206_body_after(len(data) // 2)

    with pytest.raises(PartialHaulError):
        http.haul()

    assert not http.dest.exists()
    assert http.part_path.exists(), ".part file should survive for resume"

    # Remove truncation; second call should resume and complete.
    http._state.truncate_206_body_at = None
    http.serve(data)
    result = http.haul()
    assert isinstance(result, CompleteHaul)
    assert result.sha256 == _get_expected_hash(data)
    assert http.output == data


def test_206_repeated_truncation_then_resume(http: HttpTest) -> None:
    """Server truncates the first *two* requests.  Third call succeeds.
    Validates that the checkpoint advances across multiple partial hauls."""
    data = deterministic(16 * 1024, seed=99)
    http.serve(data).truncate_206_body_after(len(data) // 4)

    for _ in range(2):
        with pytest.raises(PartialHaulError):
            http.haul()
        assert not http.dest.exists()
        assert http.part_path.exists()

    http._state.truncate_206_body_at = None
    http.serve(data)
    result = http.haul()
    assert isinstance(result, CompleteHaul)
    assert result.sha256 == _get_expected_hash(data)
    assert http.output == data


def test_200_truncation_restarts_from_zero(http: HttpTest) -> None:
    """When the server ignores Range (200 fallback) and truncates, no
    checkpoint can be reused — the retry must restart from zero and still
    complete."""
    data = deterministic(8 * 1024, seed=77)
    http.serve(data).force_200().truncate_200_body_after(len(data) // 2)

    with pytest.raises(PartialHaulError):
        http.haul()
    assert not http.dest.exists()

    http._state.truncate_200_body_at = None
    result = http.haul()
    assert isinstance(result, CompleteHaul)
    assert result.sha256 == _get_expected_hash(data)
    assert http.output == data


# ---------------------------------------------------------------------------
# Pathology 3: Resume after server file shrank → 416
# ---------------------------------------------------------------------------


def _write_synthetic_ctrl(http: HttpTest, *, valid_length: int, extent: int, etag: str = "test-etag") -> None:
    """Stage a .ctrl as if a prior haul wrote ``valid_length`` bytes."""
    from pyhaul.etag import parse_etag

    cp = Checkpoint(
        version=LATEST_VERSION,
        start=0,
        extent=extent,
        valid_length=valid_length,
        etag=parse_etag(etag),
        block_size=8 * 1024 * 1024,
        hashes=[],
        reported_length=extent,
    )
    http.ctrl_path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(http.ctrl_path, registry.dump(cp))


def test_416_after_shrink_resets_and_recovers(http: HttpTest) -> None:
    """File shrinks between haul() calls.  First resume gets 416 (range
    past EOF), engine resets checkpoint (PartialHaulError).  Second call
    starts fresh and succeeds."""
    original = deterministic(8 * 1024, seed=6)
    shrunk = deterministic(2 * 1024, seed=7)

    # Stage a partial download of the "original" content.
    http.serve(original)
    http.part_path.parent.mkdir(parents=True, exist_ok=True)
    http.part_path.write_bytes(original)
    _write_synthetic_ctrl(http, valid_length=len(original), extent=len(original))

    # Server now has the smaller file.
    http.serve(shrunk)

    with pytest.raises(PartialHaulError):
        http.haul()

    # Second call starts fresh (checkpoint was reset).
    r2 = http.haul()
    assert isinstance(r2, CompleteHaul)
    assert r2.sha256 == _get_expected_hash(shrunk)
    assert http.output == shrunk


# ---------------------------------------------------------------------------
# Pathology 4: Resume after ETag change → 200 restart
# ---------------------------------------------------------------------------


def test_resume_after_etag_change_restarts_from_zero(http: HttpTest) -> None:
    """First haul partially completes.  Server changes content + ETag.
    Resume sends If-Range with the old ETag; server ignores Range and
    returns 200 with the new content.  Engine restarts from byte 0."""
    v1 = deterministic(4 * 1024, seed=10)
    v2 = deterministic(4 * 1024, seed=11)
    half = len(v1) // 2

    http.serve(v1).set_etag('"v1"')
    http.part_path.parent.mkdir(parents=True, exist_ok=True)
    http.part_path.write_bytes(v1[:half])
    _write_synthetic_ctrl(http, valid_length=half, extent=len(v1), etag='"v1"')

    # Server swaps to v2.  The before_each hook returns 200 (no Range
    # honour) whenever If-Range doesn't match, which the stdlib handler
    # does automatically for us when force_200 is set.
    def swap_to_v2(_req: Request, state: ServerState) -> None:
        state.content = v2
        state.etag = '"v2"'
        state.force_200_enabled = True

    http.before_each(swap_to_v2)

    result = http.haul()
    assert isinstance(result, CompleteHaul)
    assert result.sha256 == _get_expected_hash(v2)
    assert http.output == v2


# ---------------------------------------------------------------------------
# Pathology 5: Resume without ETag → no If-Range sent
# ---------------------------------------------------------------------------


def test_resume_without_etag_still_works(http: HttpTest) -> None:
    """When the original download had no ETag, resume sends Range but
    omits If-Range.  The server honours the Range and returns 206."""
    data = deterministic(8 * 1024, seed=20)
    half = len(data) // 2

    http.serve(data).set_etag('""')

    # We can't use an empty ETag("") directly because our engine won't
    # emit If-Range when etag is empty. But the server sets ETag to '""'
    # which after parse_etag is also treated as no-etag? Let me use ''.
    # Actually, the test server sends whatever state.etag is.  We want
    # the ctrl to record an empty ETag so If-Range is skipped.
    http.serve(data).set_etag('"test-etag"')

    http.part_path.parent.mkdir(parents=True, exist_ok=True)
    http.part_path.write_bytes(data[:half])
    _write_synthetic_ctrl(http, valid_length=half, extent=len(data), etag="")

    result = http.haul()
    assert isinstance(result, CompleteHaul)
    assert result.sha256 == _get_expected_hash(data)
    assert http.output == data


# ---------------------------------------------------------------------------
# Pathology 6: Clean fresh download via 206 (baseline / sanity)
# ---------------------------------------------------------------------------


def test_fresh_download_206(http: HttpTest) -> None:
    """Happy path: server supports Range, returns 206 for the full
    range, download completes in one call."""
    data = deterministic(16 * 1024, seed=30)
    http.serve(data)

    result = http.haul()
    assert isinstance(result, CompleteHaul)
    assert result.sha256 == _get_expected_hash(data)
    assert http.output == data
    assert not http.part_path.exists(), ".part should be renamed to dest"
    assert not http.ctrl_path.exists(), ".ctrl should be cleaned up"


# ---------------------------------------------------------------------------
# Pathology 7: 206 with lie about total length
# ---------------------------------------------------------------------------


def test_206_total_length_lie_detected_on_resume(http: HttpTest) -> None:
    """Server lies about /TOTAL in Content-Range on a resume attempt.

    The engine trusts Content-Range for ``extent``.  A lying /TOTAL
    makes the engine expect more bytes than the server actually sends,
    so the stream ends short → PartialHaulError.  A second haul without
    the lie recovers cleanly.
    """
    data = deterministic(4 * 1024, seed=40)
    half = len(data) // 2
    fake_total = len(data) * 2

    http.serve(data).set_etag('"v1"')

    http.part_path.parent.mkdir(parents=True, exist_ok=True)
    http.part_path.write_bytes(data[:half])
    _write_synthetic_ctrl(http, valid_length=half, extent=len(data), etag='"v1"')

    http.lie_about_total_length_on_206(fake_total)

    r1: PartialHaulError | None = None
    with pytest.raises(PartialHaulError) as exc_info:
        http.haul()
    r1 = exc_info.value
    assert r1 is not None, "lying /TOTAL should cause PartialHaulError (extent > actual bytes)"

    # Remove the lie; second haul should complete cleanly.
    http._state.lie_206_total_length = None
    r2 = http.haul()
    assert isinstance(r2, CompleteHaul)
    assert r2.sha256 == _get_expected_hash(data)
