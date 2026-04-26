r"""Filesystem utilities: path-length validation and safe path construction.

pyhaul creates sidecar files alongside the destination (``dest.part``,
``dest.part.ctrl``, ``dest.done``).  These extend the filename, so a
destination whose name is already close to the filesystem limit will
fail mid-download.  :func:`path_fits` catches this upfront.

Two independent constraints are checked:

1. **Filename component** — ``NAME_MAX`` for the target directory,
   queried via ``os.pathconf`` (POSIX) or ``GetVolumeInformationW``
   (Windows).  Measured in bytes on POSIX (ext4, APFS, XFS all count
   UTF-8 bytes) and in UTF-16 code units on NTFS (255 code units).

2. **Full path string** — checked against the string that will actually
   be handed to ``open()``.  On Windows, :func:`safe_open_path` applies
   the ``\\\\?\\`` extended-length prefix *only when needed* (path
   exceeds 250 chars), so the effective limit is 32,767.  Short and
   relative paths pass through with just slash normalisation.  On POSIX
   the limit is queried via ``PC_PATH_MAX`` (falling back to 4,096) and
   the caller-provided string is measured as-is (no symlink resolution).
"""

from __future__ import annotations

import os
import sys
import unicodedata
from pathlib import Path

_LONGEST_SIDECAR_SUFFIX = ".part.ctrl"

_FALLBACK_NAME_MAX = 255
_FALLBACK_PATH_MAX = 4096
_WIN32_LONG_PATH_MAX = 32767
_WIN32_LONG_PREFIX = "\\\\?\\"
_WIN32_LONG_PATH_THRESHOLD = 250


def _pathconf_or_fallback(
    path: str,
    key: str,
    *,
    fallback: int,
) -> int:
    try:
        return int(os.pathconf(path, key))
    except (OSError, ValueError):
        return fallback


# -- filename component limit ------------------------------------------------


def _get_name_max(parent_dir: str) -> int:
    """Max filename component length for *parent_dir*."""
    if sys.platform == "win32":
        return _win32_name_max(parent_dir)
    return _posix_name_max(parent_dir)


def _posix_name_max(parent_dir: str) -> int:
    target = _existing_ancestor(parent_dir)
    return _pathconf_or_fallback(target, "PC_NAME_MAX", fallback=_FALLBACK_NAME_MAX)


def _win32_name_max(parent_dir: str) -> int:  # pragma: no cover
    if sys.platform != "win32":
        return _FALLBACK_NAME_MAX
    import ctypes

    root = Path(parent_dir).anchor or "C:\\"
    max_component = ctypes.c_ulong()
    ok = ctypes.windll.kernel32.GetVolumeInformationW(
        ctypes.c_wchar_p(root),
        None,
        0,
        None,
        ctypes.byref(max_component),
        None,
        None,
        0,
    )
    if ok:
        return max_component.value
    return _FALLBACK_NAME_MAX


def _existing_ancestor(path: str) -> str:
    """Walk up to the nearest existing ancestor for ``pathconf`` queries."""
    p = Path(path).resolve()
    while not p.exists():
        parent = p.parent
        if parent == p:
            break
        p = parent
    return str(p)


def _name_byte_len(name: str) -> int:
    """Filename length in the units the OS uses for its limit.

    POSIX filesystems measure NAME_MAX in bytes (UTF-8).  On macOS
    (APFS), filenames are stored in NFD (decomposed) form, so we
    normalize before measuring to match what the filesystem records.

    Windows NTFS measures in UTF-16 code units — astral-plane characters
    (emoji, CJK Extension B, etc.) consume 2 code units via surrogate
    pairs, so ``len(str)`` would undercount.
    """
    if sys.platform == "win32":
        return len(name.encode("utf-16-le")) // 2
    if sys.platform == "darwin":
        name = unicodedata.normalize("NFD", name)
    return len(name.encode("utf-8", errors="surrogateescape"))


# -- path length limit -------------------------------------------------------


def _get_path_max(parent_dir: str) -> int:
    r"""Path-length limit that applies to strings we hand to ``open()``.

    On Windows we use :func:`safe_open_path` which prepends the
    ``\\\\?\\`` extended-length prefix when needed, so the effective
    limit is 32,767.  On POSIX the limit is queried from the filesystem
    via ``PC_PATH_MAX``, falling back to 4,096.
    """
    if sys.platform == "win32":
        return _WIN32_LONG_PATH_MAX
    return _posix_path_max(parent_dir)


def _posix_path_max(parent_dir: str) -> int:
    target = _existing_ancestor(parent_dir)
    return _pathconf_or_fallback(target, "PC_PATH_MAX", fallback=_FALLBACK_PATH_MAX)


# -- safe path for open() ---------------------------------------------------


def safe_open_path(path: str | os.PathLike[str]) -> str:
    r"""Return *path* in a form safe to pass to ``open()``.

    On Windows, applies the ``\\\\?\\`` extended-length prefix **only
    when the path exceeds** :data:`_WIN32_LONG_PATH_THRESHOLD`
    characters.  Short and relative paths are returned with just
    forward-slash → backslash normalisation, preserving the caller's
    original form.  When the prefix *is* applied, the path is resolved
    to an absolute canonical form (no ``..`` components) as required by
    the Win32 ``\\\\?\\`` API.

    On POSIX, returns ``os.fsdecode(path)`` unchanged.
    """
    s = os.fsdecode(path)
    if sys.platform != "win32":
        return s
    return _win32_safe_open_path(s)  # pragma: no cover


def _win32_safe_open_path(path_str: str) -> str:  # pragma: no cover
    if path_str.startswith(_WIN32_LONG_PREFIX):
        return path_str
    if len(path_str) > _WIN32_LONG_PATH_THRESHOLD:
        abspath = str(Path(path_str).resolve()).replace("/", "\\")
        return _WIN32_LONG_PREFIX + abspath
    return path_str.replace("/", "\\")


# -- public API: preflight ---------------------------------------------------


def path_fits(
    dest: str | os.PathLike[str],
    suffix: str = _LONGEST_SIDECAR_SUFFIX,
) -> bool:
    r"""Check whether *dest* plus *suffix* fits within filesystem limits.

    Checks two independent constraints:

    1. The filename component (``dest.name + suffix``) must fit within
       the target directory's ``NAME_MAX`` (255 bytes on POSIX, 255
       UTF-16 code units on NTFS).
    2. The full path string, *as it will be presented to the OS*, must
       fit within the platform's path-length limit.

    On Windows the path check models what :func:`safe_open_path` will
    do: short paths pass through with just slash normalisation; long
    paths get the ``\\\\?\\`` prefix and the limit becomes 32,767.
    On POSIX the caller-provided string is measured as-is.
    """
    dest_str = os.fsdecode(dest)
    p = Path(dest_str)

    extended_name = p.name + suffix

    parent_str = str(p.parent)
    if parent_str == ".":
        parent_str = str(Path.cwd())

    name_limit = _get_name_max(parent_str)
    if _name_byte_len(extended_name) > name_limit:
        return False

    effective_path = safe_open_path(dest_str + suffix)
    path_limit = _get_path_max(parent_str)

    if sys.platform == "win32":
        return len(effective_path) <= path_limit

    path_bytes = effective_path.encode("utf-8", errors="surrogateescape")
    return len(path_bytes) <= path_limit


# -- public API: sidecar paths -----------------------------------------------


def done_path_for(dest: Path) -> Path:
    """Return the ``.done`` receipt path for a destination file."""
    return dest.with_suffix(dest.suffix + ".done")
