"""Tests for file preallocation."""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path

import pytest

from pyhaul.alloc import (
    AllocationOutcome,
    AllocationResult,
    Mechanism,
    allocate_file,
    allocation_reserved_posix,
    allocation_sparse,
)


def test_allocate_file_creates_correct_size(tmp_path: Path) -> None:
    p = tmp_path / "test.part"
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR)
    try:
        result = allocate_file(fd, total_length=4096)
        assert result.final_size == 4096
        assert result.outcome in (AllocationOutcome.RESERVED, AllocationOutcome.SPARSE)
        assert os.fstat(fd).st_size == 4096
    finally:
        os.close(fd)


def test_allocation_factories_reject_negative_previous_size() -> None:
    with pytest.raises(ValueError, match="previous_size"):
        allocation_sparse(100, previous_size=-1)
    with pytest.raises(ValueError, match="previous_size"):
        allocation_reserved_posix(100, previous_size=-1)


def test_allocate_non_positive_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bad.part"
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR)
    try:
        with pytest.raises(ValueError, match="total_length must be positive"):
            allocate_file(fd, total_length=0)
        with pytest.raises(ValueError, match="total_length must be positive"):
            allocate_file(fd, total_length=-1)
    finally:
        os.close(fd)


def test_allocate_file_without_posix_fallocate_uses_host_strategy(
    tmp_path: Path,
) -> None:
    """When ``posix_fallocate`` is absent, sizing still succeeds (Darwin
    ``F_PREALLOCATE`` or sparse ``ftruncate``)."""
    p = tmp_path / "test.part"
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR)
    try:
        result = allocate_file(fd, total_length=1024)
        if not hasattr(os, "posix_fallocate"):
            assert result.outcome in (AllocationOutcome.RESERVED, AllocationOutcome.SPARSE)
    finally:
        os.close(fd)


def test_allocate_file_posix_fallocate_enospc_propagates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_space(_fd: int, _offset: int, _length: int) -> None:
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(os, "posix_fallocate", no_space, raising=False)

    p = tmp_path / "enospc.part"
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR)
    try:
        with pytest.raises(OSError) as exc_info:
            allocate_file(fd, total_length=4096)
        assert exc_info.value.errno == errno.ENOSPC
    finally:
        os.close(fd)


@pytest.fixture
def linux_like_fallocate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Force Linux-style ``posix_fallocate`` semantics on any platform."""

    def linux_posix_fallocate(fd: int, offset: int, length: int) -> None:
        target = offset + length
        current = os.fstat(fd).st_size
        if target > current:
            os.ftruncate(fd, target)

    monkeypatch.setattr(os, "posix_fallocate", linux_posix_fallocate, raising=False)


def test_allocate_file_shrinks_oversized_file_under_linux_fallocate(
    tmp_path: Path,
    linux_like_fallocate: None,
) -> None:
    del linux_like_fallocate
    p = tmp_path / "stale.part"
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR)
    try:
        os.ftruncate(fd, 8192)
    finally:
        os.close(fd)
    assert p.stat().st_size == 8192

    fd = os.open(str(p), os.O_RDWR)
    try:
        result = allocate_file(fd, total_length=1024)
        assert os.fstat(fd).st_size == 1024
    finally:
        os.close(fd)

    assert result == AllocationResult(
        outcome=AllocationOutcome.RESERVED,
        previous_size=8192,
        final_size=1024,
        mechanism=Mechanism.POSIX_FALLOCATE,
    )
    assert p.stat().st_size == 1024


def test_allocate_file_grows_file_under_linux_fallocate(
    tmp_path: Path,
    linux_like_fallocate: None,
) -> None:
    del linux_like_fallocate
    p = tmp_path / "small.part"
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR)
    try:
        os.ftruncate(fd, 512)
    finally:
        os.close(fd)

    fd = os.open(str(p), os.O_RDWR)
    try:
        result = allocate_file(fd, total_length=4096)
        assert os.fstat(fd).st_size == 4096
    finally:
        os.close(fd)

    assert result.outcome == AllocationOutcome.RESERVED
    assert result.mechanism == Mechanism.POSIX_FALLOCATE


def test_allocate_file_already_sized_skips_posix_fallocate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    p = tmp_path / "sized.part"
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR)
    try:
        os.ftruncate(fd, 2048)
    finally:
        os.close(fd)

    called: list[int] = []
    _real_posix_fallocate = getattr(os, "posix_fallocate", None)

    def counting_fallocate(f: int, off: int, ln: int) -> None:
        called.append(1)
        if _real_posix_fallocate is not None:
            _real_posix_fallocate(f, off, ln)

    monkeypatch.setattr(os, "posix_fallocate", counting_fallocate, raising=False)

    fd = os.open(str(p), os.O_RDWR)
    try:
        result = allocate_file(fd, total_length=2048)
    finally:
        os.close(fd)

    assert called == []
    assert result == AllocationResult(
        outcome=AllocationOutcome.ALREADY_SIZED,
        previous_size=2048,
        final_size=2048,
        mechanism=Mechanism.NOOP,
    )


def test_allocate_file_falls_back_to_ftruncate_when_posix_fallocate_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raising_fallocate(_fd: int, _offset: int, _length: int) -> None:
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(os, "posix_fallocate", raising_fallocate, raising=False)

    p = tmp_path / "fallback.part"
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR)
    try:
        result = allocate_file(fd, total_length=2048)
        if sys.platform == "darwin" and result.outcome == AllocationOutcome.RESERVED:
            assert result.mechanism == Mechanism.F_PREALLOCATE
        else:
            assert result.outcome == AllocationOutcome.SPARSE
            assert result.mechanism == Mechanism.FTRUNCATE
        assert os.fstat(fd).st_size == 2048
    finally:
        os.close(fd)


def test_pyhaul_alloc_resolves_posix_fallocate_via_hasattr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr("pyhaul.alloc.os.posix_fallocate", raising=False)

    p = tmp_path / "nofalloc.part"
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR)
    try:
        os.ftruncate(fd, 8192)
    finally:
        os.close(fd)

    fd = os.open(str(p), os.O_RDWR)
    try:
        result = allocate_file(fd, total_length=1024)
        assert result.outcome in (AllocationOutcome.RESERVED, AllocationOutcome.RESIZED_SPARSE)
        assert os.fstat(fd).st_size == 1024
    finally:
        os.close(fd)


@pytest.mark.skipif(sys.platform != "darwin", reason="F_PREALLOCATE is Darwin-specific")
def test_allocate_file_darwin_sets_final_size_from_empty(tmp_path: Path) -> None:
    p = tmp_path / "darwin_empty.part"
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR)
    try:
        result = allocate_file(fd, total_length=8192)
        assert os.fstat(fd).st_size == 8192
        assert result.outcome in (AllocationOutcome.RESERVED, AllocationOutcome.SPARSE)
    finally:
        os.close(fd)


@pytest.mark.skipif(sys.platform != "darwin", reason="F_PREALLOCATE is Darwin-specific")
def test_allocate_file_darwin_grows_smaller_file_to_target(tmp_path: Path) -> None:
    p = tmp_path / "darwin_grow.part"
    fd = os.open(str(p), os.O_CREAT | os.O_RDWR)
    try:
        os.ftruncate(fd, 512)
    finally:
        os.close(fd)

    fd = os.open(str(p), os.O_RDWR)
    try:
        result = allocate_file(fd, total_length=16 * 1024)
        assert os.fstat(fd).st_size == 16 * 1024
        assert result.outcome in (AllocationOutcome.RESERVED, AllocationOutcome.SPARSE)
    finally:
        os.close(fd)
