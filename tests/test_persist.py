"""Tests for pyhaul.persist — checkpoint serialization and atomic writes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pyhaul._types import ControlFileError, ETag
from pyhaul.persist import (
    CTRL_VERSION,
    Checkpoint,
    ctrl_path_for,
    deserialize,
    read_checkpoint,
    serialize,
    write_atomic,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_checkpoint(**overrides: object) -> Checkpoint:
    defaults: dict[str, object] = {
        "version": CTRL_VERSION,
        "start": 0,
        "extent": 104857600,
        "valid_length": 67108864,
        "etag": ETag('"abc123"'),
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
        raw = serialize(cp)
        restored = deserialize(raw)
        assert restored == cp

    def test_null_extent(self) -> None:
        cp = _make_checkpoint(extent=None)
        raw = serialize(cp)
        restored = deserialize(raw)
        assert restored.extent is None
        assert restored == cp

    def test_null_resource_length(self) -> None:
        cp = _make_checkpoint(resource_length=None)
        raw = serialize(cp)
        restored = deserialize(raw)
        assert restored.resource_length is None

    def test_empty_etag(self) -> None:
        cp = _make_checkpoint(etag=ETag(""))
        raw = serialize(cp)
        restored = deserialize(raw)
        assert restored.etag == ""

    def test_zero_valid_length(self) -> None:
        cp = _make_checkpoint(valid_length=0)
        raw = serialize(cp)
        restored = deserialize(raw)
        assert restored.valid_length == 0

    def test_nonzero_start(self) -> None:
        cp = _make_checkpoint(start=1048576, extent=1048576)
        raw = serialize(cp)
        restored = deserialize(raw)
        assert restored.start == 1048576

    def test_serialized_is_valid_json(self) -> None:
        cp = _make_checkpoint()
        raw = serialize(cp)
        parsed = json.loads(raw)
        assert parsed["version"] == CTRL_VERSION
        assert "url" not in parsed


# ---------------------------------------------------------------------------
# Corrupt / invalid input
# ---------------------------------------------------------------------------


class TestDeserializeErrors:
    def test_empty_bytes(self) -> None:
        with pytest.raises(ControlFileError):
            deserialize(b"")

    def test_not_json(self) -> None:
        with pytest.raises(ControlFileError):
            deserialize(b"this is not json")

    def test_json_array_not_object(self) -> None:
        with pytest.raises(ControlFileError):
            deserialize(json.dumps([1, 2, 3]).encode())

    def test_missing_version(self) -> None:
        blob = json.dumps({"url": "http://x.com/f"}).encode()
        with pytest.raises(ControlFileError):
            deserialize(blob)

    def test_wrong_version(self) -> None:
        blob = json.dumps({"version": 999, "url": "http://x.com/f"}).encode()
        with pytest.raises(ControlFileError):
            deserialize(blob)

    def test_missing_required_field(self) -> None:
        cp = _make_checkpoint()
        raw = serialize(cp)
        obj = json.loads(raw)
        del obj["valid_length"]
        with pytest.raises(ControlFileError):
            deserialize(json.dumps(obj).encode())

    def test_wrong_type_for_valid_length(self) -> None:
        cp = _make_checkpoint()
        raw = serialize(cp)
        obj = json.loads(raw)
        obj["valid_length"] = "not a number"
        with pytest.raises(ControlFileError):
            deserialize(json.dumps(obj).encode())

    def test_boolean_not_accepted_as_int(self) -> None:
        cp = _make_checkpoint()
        raw = serialize(cp)
        obj = json.loads(raw)
        obj["valid_length"] = True
        with pytest.raises(ControlFileError):
            deserialize(json.dumps(obj).encode())

    def test_negative_valid_length(self) -> None:
        cp = _make_checkpoint()
        raw = serialize(cp)
        obj = json.loads(raw)
        obj["valid_length"] = -1
        with pytest.raises(ControlFileError):
            deserialize(json.dumps(obj).encode())

    def test_negative_start(self) -> None:
        cp = _make_checkpoint()
        raw = serialize(cp)
        obj = json.loads(raw)
        obj["start"] = -1
        with pytest.raises(ControlFileError):
            deserialize(json.dumps(obj).encode())


# ---------------------------------------------------------------------------
# Atomic write / read_checkpoint
# ---------------------------------------------------------------------------


class TestAtomicWriteAndRead:
    def test_write_and_read_round_trip(self, tmp_path: Path) -> None:
        cp = _make_checkpoint()
        ctrl = tmp_path / "file.bin.part.ctrl"
        write_atomic(ctrl, cp)
        restored = read_checkpoint(ctrl)
        assert restored == cp

    def test_read_nonexistent_raises_file_not_found(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_checkpoint(tmp_path / "nope.ctrl")

    def test_write_is_atomic_no_partial_file(self, tmp_path: Path) -> None:
        """The .tmp file should not linger after a successful write."""
        ctrl = tmp_path / "file.bin.part.ctrl"
        write_atomic(ctrl, _make_checkpoint())
        assert not ctrl.with_suffix(".ctrl.tmp").exists()

    def test_overwrite_preserves_atomicity(self, tmp_path: Path) -> None:
        ctrl = tmp_path / "file.bin.part.ctrl"
        write_atomic(ctrl, _make_checkpoint(valid_length=100))
        write_atomic(ctrl, _make_checkpoint(valid_length=200))
        restored = read_checkpoint(ctrl)
        assert restored.valid_length == 200


# ---------------------------------------------------------------------------
# Checkpoint is immutable
# ---------------------------------------------------------------------------


class TestCheckpointImmutable:
    def test_frozen(self) -> None:
        cp = _make_checkpoint()
        with pytest.raises(Exception, match=r"frozen|cannot assign"):
            cp.valid_length = 999  # type: ignore[misc]
