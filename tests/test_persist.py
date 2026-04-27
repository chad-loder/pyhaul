"""Tests for pyhaul.persist — checkpoint serialization and atomic writes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyhaul._types import ControlFileError, ETag
from pyhaul.checkpoint import LATEST_VERSION, Checkpoint, registry
from pyhaul.persist import (
    ctrl_path_for,
    write_atomic,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_checkpoint(**overrides: object) -> Checkpoint:
    defaults: dict[str, object] = {
        "version": LATEST_VERSION,
        "start": 0,
        "extent": 104857600,
        "valid_length": 67108864,
        "etag": ETag('"abc123"'),
        "block_size": 8 * 1024 * 1024,
        "hashes": [],
        "resource_length": 104857600,
    }
    defaults.update(overrides)
    return Checkpoint(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ctrl_path_for
# ---------------------------------------------------------------------------


class TestCtrlPathFor:
    def test_appends_ctrl_suffix(self, tmp_path: Path) -> None:
        part = tmp_path / "file.bin.part"
        assert ctrl_path_for(part) == tmp_path / "file.bin.part.ctrl"

    def test_double_suffix(self, tmp_path: Path) -> None:
        part = tmp_path / "archive.tar.gz.part"
        assert ctrl_path_for(part) == tmp_path / "archive.tar.gz.part.ctrl"


# ---------------------------------------------------------------------------
# Round-trip: serialize → deserialize
# ---------------------------------------------------------------------------


class TestRoundTrip:
    def test_known_total(self) -> None:
        cp = _make_checkpoint()
        raw = registry.dump(cp)
        restored = registry.load(raw)
        assert restored == cp

    def test_null_extent(self) -> None:
        cp = _make_checkpoint(extent=None)
        raw = registry.dump(cp)
        restored = registry.load(raw)
        assert restored.extent is None
        assert restored == cp

    def test_null_resource_length(self) -> None:
        cp = _make_checkpoint(resource_length=None)
        raw = registry.dump(cp)
        restored = registry.load(raw)
        assert restored.resource_length is None

    def test_empty_etag(self) -> None:
        cp = _make_checkpoint(etag=ETag(""))
        raw = registry.dump(cp)
        restored = registry.load(raw)
        assert restored.etag == ""

    def test_zero_valid_length(self) -> None:
        cp = _make_checkpoint(valid_length=0)
        raw = registry.dump(cp)
        restored = registry.load(raw)
        assert restored.valid_length == 0

    def test_nonzero_start(self) -> None:
        cp = _make_checkpoint(start=1048576, extent=1048576)
        raw = registry.dump(cp)
        restored = registry.load(raw)
        assert restored.start == 1048576

    def test_with_hashes(self) -> None:
        hashes = [b"\x00" * 32, b"\xff" * 32]
        cp = _make_checkpoint(hashes=hashes)
        raw = registry.dump(cp)
        restored = registry.load(raw)
        assert restored.hashes == hashes
        assert restored == cp


# ---------------------------------------------------------------------------
# Corrupt / invalid input
# ---------------------------------------------------------------------------


class TestDeserializeErrors:
    def test_empty_bytes(self) -> None:
        with pytest.raises(ControlFileError, match="empty"):
            registry.load(b"")

    def test_not_binary_nor_json(self) -> None:
        with pytest.raises(ControlFileError, match="unrecognized"):
            registry.load(b"INVALID")

    def test_wrong_magic(self) -> None:
        with pytest.raises(ControlFileError, match="unrecognized"):
            registry.load(b"NOTH" + b"\x04" + b"\x00" * 32)

    def test_wrong_version(self) -> None:
        with pytest.raises(ControlFileError, match="unsupported"):
            registry.load(b"HAUL" + b"\xff" + b"\x00" * 32)


class TestV3Migration:
    def test_migrates_v3_json(self) -> None:
        v3_data = {
            "version": 3,
            "start": 100,
            "extent": 1000,
            "valid_length": 500,
            "etag": '"migrated"',
            "resource_length": 1000,
        }
        raw = json.dumps(v3_data).encode("utf-8")
        cp = registry.load(raw)

        assert cp.version == 3
        assert cp.start == 100
        assert cp.valid_length == 500
        assert cp.etag == '"migrated"'
        assert cp.block_size == 8 * 1024 * 1024
        assert cp.hashes == []


# ---------------------------------------------------------------------------
# Atomic write / read_checkpoint
# ---------------------------------------------------------------------------


class TestAtomicWriteAndRead:
    def test_write_and_read_round_trip(self, tmp_path: Path) -> None:
        cp = _make_checkpoint()
        ctrl = tmp_path / "file.bin.part.ctrl"
        write_atomic(ctrl, registry.dump(cp))
        restored = registry.load(ctrl.read_bytes())
        assert restored == cp

    def test_write_is_atomic_no_partial_file(self, tmp_path: Path) -> None:
        """The .tmp file should not linger after a successful write."""
        ctrl = tmp_path / "file.bin.part.ctrl"
        write_atomic(ctrl, registry.dump(_make_checkpoint()))
        assert not ctrl.with_suffix(".ctrl.tmp").exists()

    def test_overwrite_preserves_atomicity(self, tmp_path: Path) -> None:
        ctrl = tmp_path / "file.bin.part.ctrl"
        write_atomic(ctrl, registry.dump(_make_checkpoint(valid_length=100)))
        write_atomic(ctrl, registry.dump(_make_checkpoint(valid_length=200)))
        restored = registry.load(ctrl.read_bytes())
        assert restored.valid_length == 200


# ---------------------------------------------------------------------------
# Checkpoint is immutable
# ---------------------------------------------------------------------------


class TestCheckpointImmutable:
    def test_frozen(self) -> None:
        cp = _make_checkpoint()
        with pytest.raises(Exception, match=r"frozen|cannot assign"):
            cp.valid_length = 999  # type: ignore[misc]
