"""Checkpoint persistence for crash-safe resume (.part.ctrl files).

Pure JSON, UTF-8 encoded. Written atomically via tmp + fsync + rename.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from pyhaul._types import ControlFileError, ETag, parse_etag

CTRL_VERSION = 3
_JSON_ENCODING = "utf-8"


@dataclass(frozen=True, slots=True, kw_only=True)
class Checkpoint:
    """On-disk state for a single download range."""

    version: int
    start: int
    extent: int | None
    valid_length: int
    etag: ETag
    resource_length: int | None


def ctrl_path_for(part_path: Path) -> Path:
    """Return the ``.part.ctrl`` path for a ``.part`` file."""
    return part_path.with_suffix(part_path.suffix + ".ctrl")


def serialize(cp: Checkpoint) -> bytes:
    """Encode a checkpoint as JSON bytes."""
    obj = {
        "version": cp.version,
        "start": cp.start,
        "extent": cp.extent,
        "valid_length": cp.valid_length,
        "etag": str(cp.etag),
        "resource_length": cp.resource_length,
    }
    return json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode(_JSON_ENCODING)


def deserialize(data: bytes) -> Checkpoint:
    """Decode JSON bytes into a Checkpoint.

    Raises :class:`ControlFileError` on corrupt or incompatible input.
    """
    if not data:
        raise ControlFileError("checkpoint file is empty")

    try:
        text = data.decode(_JSON_ENCODING)
        loaded = json.loads(text)
    except UnicodeDecodeError as exc:
        raise ControlFileError(f"not valid {_JSON_ENCODING}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ControlFileError(f"not valid JSON: {exc}") from exc

    if not isinstance(loaded, dict):
        raise ControlFileError("JSON root is not an object")

    obj = cast("dict[str, object]", loaded)

    version = _require_int(obj, "version")
    if version != CTRL_VERSION:
        raise ControlFileError(f"unsupported checkpoint version: {version}")

    etag = parse_etag(_require_str(obj, "etag"))
    start = _require_int(obj, "start", non_negative=True)
    valid_length = _require_int(obj, "valid_length", non_negative=True)
    extent = _require_optional_int(obj, "extent", non_negative=True)
    resource_length = _require_optional_int(obj, "resource_length", non_negative=True)

    return Checkpoint(
        version=version,
        start=start,
        extent=extent,
        valid_length=valid_length,
        etag=etag,
        resource_length=resource_length,
    )


def write_atomic(path: Path, cp: Checkpoint) -> None:
    """Serialize and write a checkpoint atomically (tmp + fsync + rename).

    Callers that also need ``.part`` data bytes to be durable MUST
    ``fdatasync`` the ``.part`` fd *before* calling this function.
    """
    raw = serialize(cp)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def read_checkpoint(path: Path) -> Checkpoint:
    """Read and parse a checkpoint from disk.

    Raises :class:`FileNotFoundError` if absent, :class:`ControlFileError`
    if corrupt.
    """
    return deserialize(path.read_bytes())


# ---------------------------------------------------------------------------
# Field extraction helpers
# ---------------------------------------------------------------------------


def _require_str(obj: dict[str, object], key: str) -> str:
    val = obj.get(key)
    if not isinstance(val, str):
        raise ControlFileError(f"{key}: expected str, got {type(val).__name__}")
    return val


def _require_int(obj: dict[str, object], key: str, *, non_negative: bool = False) -> int:
    val = obj.get(key)
    if not isinstance(val, int) or isinstance(val, bool):
        raise ControlFileError(f"{key}: expected int, got {type(val).__name__}")
    if non_negative and val < 0:
        raise ControlFileError(f"{key}: must be non-negative, got {val}")
    return val


def _require_optional_int(obj: dict[str, object], key: str, *, non_negative: bool = False) -> int | None:
    val = obj.get(key)
    if val is None:
        return None
    if not isinstance(val, int) or isinstance(val, bool):
        raise ControlFileError(f"{key}: expected int or null, got {type(val).__name__}")
    if non_negative and val < 0:
        raise ControlFileError(f"{key}: must be non-negative, got {val}")
    return val
