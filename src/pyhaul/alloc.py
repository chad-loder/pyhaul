"""File preallocation: fail-fast disk space reservation.

Linux / libc: ``posix_fallocate`` when it succeeds.  On macOS, ``posix_fallocate``
often returns ``ENOTSUP`` on APFS; we then try ``fcntl(F_PREALLOCATE)`` with a
full ``fstore_t``, then ``ftruncate`` to the final logical size.  Otherwise
``ftruncate`` extends the file sparsely.

:func:`allocate_file` returns an :class:`AllocationResult` (outcome, sizes, and
mechanism) for telemetry, resume hints, and ``.part.ctrl`` metadata.
"""

from __future__ import annotations

import errno
import os
import struct
import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal


class AllocationOutcome(StrEnum):
    """What :func:`allocate_file` accomplished for the file's logical size."""

    RESERVED = "reserved"
    """This call eagerly reserved backing space (``posix_fallocate`` or ``F_PREALLOCATE``)."""
    SPARSE = "sparse"
    """This call grew the file with ``ftruncate`` only (sparse)."""
    ALREADY_SIZED = "already_sized"
    """The file was already ``total_length``; this call did nothing."""
    RESIZED_SPARSE = "resized_sparse"
    """This call shrank and/or resized without eager reservation (sparse path)."""


class Mechanism(StrEnum):
    """How :func:`allocate_file` applied the final logical size."""

    POSIX_FALLOCATE = "posix_fallocate"
    F_PREALLOCATE = "f_preallocate"
    FTRUNCATE = "ftruncate"
    NOOP = "noop"


@dataclass(frozen=True, slots=True)
class AllocationResult:
    outcome: AllocationOutcome
    previous_size: int
    final_size: int
    mechanism: Mechanism


AllocationPreset = Literal["sparse", "reserved_posix", "reserved_fpreallocate"]
"""Shorthand for construction helpers; expands to :class:`AllocationResult`."""


def allocation_sparse(total_length: int, *, previous_size: int = 0) -> AllocationResult:
    """File reached *total_length* by growing with ``ftruncate`` only (sparse)."""
    if previous_size < 0:
        msg = f"previous_size must be non-negative, got {previous_size}"
        raise ValueError(msg)
    return AllocationResult(
        outcome=AllocationOutcome.SPARSE,
        previous_size=previous_size,
        final_size=total_length,
        mechanism=Mechanism.FTRUNCATE,
    )


def allocation_reserved_posix(total_length: int, *, previous_size: int = 0) -> AllocationResult:
    """``posix_fallocate`` eagerly reserved backing for the final size."""
    if previous_size < 0:
        msg = f"previous_size must be non-negative, got {previous_size}"
        raise ValueError(msg)
    return AllocationResult(
        outcome=AllocationOutcome.RESERVED,
        previous_size=previous_size,
        final_size=total_length,
        mechanism=Mechanism.POSIX_FALLOCATE,
    )


def allocation_reserved_fpreallocate(total_length: int, *, previous_size: int = 0) -> AllocationResult:
    """Darwin ``F_PREALLOCATE`` eagerly reserved backing for the final size."""
    if previous_size < 0:
        msg = f"previous_size must be non-negative, got {previous_size}"
        raise ValueError(msg)
    return AllocationResult(
        outcome=AllocationOutcome.RESERVED,
        previous_size=previous_size,
        final_size=total_length,
        mechanism=Mechanism.F_PREALLOCATE,
    )


# Darwin ``sys/fcntl.h`` — used only with ``fcntl(F_PREALLOCATE, ...)``.
_F_PREALLOCATE = 42
_F_ALLOCATECONTIG = 0x00000002
_F_ALLOCATEALL = 0x00000004
_F_PEOFPOSMODE = 3
_FSTORE_FMT = "@Iiqqq"
_FSTORE_PACKED_BYTES = 32
assert struct.calcsize(_FSTORE_FMT) == _FSTORE_PACKED_BYTES, "unexpected fstore_t pack size"

_POSIX_FALLOCATE_FALLBACK_ERRNOS = frozenset(
    {
        errno.ENOTSUP,
        errno.EOPNOTSUPP,
        errno.EINVAL,
    }
)


def _darwin_preallocate_from_peof(fd: int, *, delta: int) -> bool:
    """Reserve *delta* bytes from the current physical EOF (Darwin only)."""
    if sys.platform != "darwin" or delta <= 0:
        return False

    import fcntl

    for flags in (_F_ALLOCATECONTIG | _F_ALLOCATEALL, _F_ALLOCATEALL):
        buf = struct.pack(_FSTORE_FMT, flags, _F_PEOFPOSMODE, 0, delta, 0)
        try:
            fcntl.fcntl(fd, _F_PREALLOCATE, buf)
        except OSError as e:
            if e.errno == errno.ENOSPC:
                raise
            continue
        return True
    return False


def allocate_file(fd: int, *, total_length: int) -> AllocationResult:
    """Reserve or size *total_length* bytes for the download file.

    ``total_length`` must be positive (callers with no Content-Length should
    not invoke this).

    When the file is already ``total_length``, returns :attr:`AllocationOutcome.ALREADY_SIZED`
    without calling ``posix_fallocate``, preserving whatever reservation state
    a prior run left.

    We do not retry ``EINTR`` from ``posix_fallocate``: POSIX requires libc to
    handle interruption internally for this entry point.
    """
    if total_length <= 0:
        msg = f"total_length must be positive, got {total_length}"
        raise ValueError(msg)

    previous_size = os.fstat(fd).st_size
    final_size = total_length

    if previous_size == total_length:
        return AllocationResult(
            outcome=AllocationOutcome.ALREADY_SIZED,
            previous_size=previous_size,
            final_size=final_size,
            mechanism=Mechanism.NOOP,
        )

    shrunk = False
    current_size = previous_size
    if current_size > total_length:
        os.ftruncate(fd, total_length)
        current_size = total_length
        shrunk = True

    if hasattr(os, "posix_fallocate"):
        try:
            os.posix_fallocate(fd, 0, total_length)
            return AllocationResult(
                outcome=AllocationOutcome.RESERVED,
                previous_size=previous_size,
                final_size=final_size,
                mechanism=Mechanism.POSIX_FALLOCATE,
            )
        except OSError as e:
            if e.errno == errno.ENOSPC:
                raise
            if e.errno not in _POSIX_FALLOCATE_FALLBACK_ERRNOS:
                raise

    if sys.platform == "darwin":
        delta = total_length - current_size
        if delta > 0 and _darwin_preallocate_from_peof(fd, delta=delta):
            os.ftruncate(fd, total_length)
            return AllocationResult(
                outcome=AllocationOutcome.RESERVED,
                previous_size=previous_size,
                final_size=final_size,
                mechanism=Mechanism.F_PREALLOCATE,
            )

    os.ftruncate(fd, total_length)
    outcome = AllocationOutcome.RESIZED_SPARSE if shrunk else AllocationOutcome.SPARSE
    return AllocationResult(
        outcome=outcome,
        previous_size=previous_size,
        final_size=final_size,
        mechanism=Mechanism.FTRUNCATE,
    )
