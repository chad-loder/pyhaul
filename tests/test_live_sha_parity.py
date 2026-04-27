"""SHA parity across the live HTTP transport matrix."""

from __future__ import annotations

from typing import TYPE_CHECKING

from pyhaul._types import CompleteHaul, HashBuilder
from tests.conftest import deterministic

if TYPE_CHECKING:
    from tests.conftest import HttpTest

_PARITY_PAYLOAD = deterministic(64 * 1024, seed=42)


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


_EXPECTED_SHA = _get_expected_hash(_PARITY_PAYLOAD)


def test_live_transport_matrix_complete_download_sha(http: HttpTest) -> None:
    """Each installed backend yields the same SHA for the same bytes."""
    http.serve(_PARITY_PAYLOAD)
    result = http.haul()
    assert isinstance(result, CompleteHaul)
    assert result.sha256 == _EXPECTED_SHA


def test_live_fresh_download_with_200_fallback_sha(http: HttpTest) -> None:
    """Server ignores Range, returns 200 full body. SHA must still match."""
    http.serve(_PARITY_PAYLOAD).force_200()
    result = http.haul()
    assert isinstance(result, CompleteHaul)
    assert result.sha256 == _EXPECTED_SHA
