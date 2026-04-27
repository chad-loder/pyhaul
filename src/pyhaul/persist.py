"""Atomic file operations for pyhaul persistence."""

from __future__ import annotations

import os
from pathlib import Path


def ctrl_path_for(part_path: Path) -> Path:
    """Return the ``.part.ctrl`` path for a ``.part`` file."""
    return part_path.with_suffix(part_path.suffix + ".ctrl")


def write_atomic(path: Path, raw: bytes) -> None:
    """Write bytes atomically (tmp + fsync + rename)."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("wb") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)
