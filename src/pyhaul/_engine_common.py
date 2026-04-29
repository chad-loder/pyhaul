"""Shared non-I/O logic for the sync and async download engines.

Both ``engine.haul`` and ``async_engine.haul_async`` delegate all
pure-logic work here: checkpoint reading, header building, response
interpretation, file allocation, chunk writing, flush, and
finalization.  The only thing each engine owns is the I/O boundary
(``with`` vs ``async with``, ``for`` vs ``async for``).
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from pyhaul._types import (
    CompleteHaul,
    ControlFileError,
    DestinationError,
    ETag,
    HashBuilder,
    HaulState,
    PartialHaulError,
    ServerMisconfiguredError,
    UnexpectedStatusError,
    Url,
    parse_etag,
    parse_url,
)
from pyhaul.alloc import allocate_file
from pyhaul.checkpoint import LATEST_VERSION, Checkpoint, registry
from pyhaul.content_range import parse_content_range
from pyhaul.fs import path_fits
from pyhaul.headers import DEFAULT_HEADERS, merge_headers
from pyhaul.persist import (
    ctrl_path_for,
    write_atomic,
)
from pyhaul.transport._headers import TransportHeaders

DEFAULT_CHUNK = 1 << 16  # 64 KiB
DEFAULT_FLUSH = 1 << 20  # 1 MiB
datasync = getattr(os, "fdatasync", os.fsync)

logger = logging.getLogger(__name__)

_HTTP_200 = 200
_HTTP_206 = 206
_HTTP_416 = 416

# Final responses when the underlying client chose not to follow (library default or explicit).
_REDIRECT_STATUSES: frozenset[int] = frozenset({301, 302, 303, 307, 308})


# ─── Dataclasses ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PrepareHaul:
    """Immutable context computed once before the HTTP request."""

    dest_path: Path
    parsed_url: Url
    part_path: Path
    ctrl_path: Path
    start: int
    cursor: int
    stored_etag: ETag
    hashes: list[bytes]
    tail_hash: bytes | None
    block_size: int
    request_byte: int
    merged_headers: TransportHeaders
    t0: float


@dataclass(slots=True)
class StreamPlan:
    """Mutable write-loop state initialized from the HTTP response."""

    start: int
    cursor: int
    extent: int | None
    etag: ETag
    reported_length: int | None
    content_type: str
    hb: HashBuilder
    bytes_since_flush: int = field(default=0, init=False)


# ─── Preparation ──────────────────────────────────────────────────


def prepare_haul(
    url: str,
    dest: str | Path,
    *,
    user_headers: Mapping[str, str] | None = None,
) -> PrepareHaul:
    """Read checkpoint, build request headers, return immutable context."""
    t0 = time.monotonic()
    dest_path = Path(dest)

    if not path_fits(dest_path):
        raise DestinationError(f"destination path is too long for sidecar files: {dest_path}")

    parsed_url = parse_url(url)
    part_path = dest_path.with_suffix(dest_path.suffix + ".part")
    ctrl_path = ctrl_path_for(part_path)

    cp: Checkpoint | None = None
    if ctrl_path.exists():
        try:
            cp = registry.load(ctrl_path.read_bytes())
        except ControlFileError:
            cp = None

    start = cp.start if cp else 0
    cursor = cp.valid_length if cp else 0
    stored_etag = cp.etag if cp else ETag("")
    hashes = cp.hashes if cp else []
    tail_hash = cp.tail_hash if cp else None
    block_size = cp.block_size if cp else 8 * 1024 * 1024

    request_byte = start + cursor
    req_hdrs: dict[str, str] = {"Range": f"bytes={request_byte}-"}
    if stored_etag:
        req_hdrs["If-Range"] = str(stored_etag)

    merged = merge_headers(dict(user_headers or {}), {**DEFAULT_HEADERS, **req_hdrs})
    th = TransportHeaders.from_mapping(merged)

    logger.debug(
        "Prepared download request",
        extra={
            "pyhaul_url": str(parsed_url),
            "pyhaul_dest": str(dest_path),
            "pyhaul_resume_valid_length": cursor,
            "pyhaul_request_byte": request_byte,
        },
    )

    return PrepareHaul(
        dest_path=dest_path,
        parsed_url=parsed_url,
        part_path=part_path,
        ctrl_path=ctrl_path,
        start=start,
        cursor=cursor,
        stored_etag=stored_etag,
        hashes=hashes,
        tail_hash=tail_hash,
        block_size=block_size,
        request_byte=request_byte,
        merged_headers=th,
        t0=t0,
    )


# ─── Response interpretation ──────────────────────────────────────


def handle_response(
    status: int,
    headers: TransportHeaders,
    prep: PrepareHaul,
    state: HaulState,
) -> CompleteHaul | StreamPlan:
    """Branch on status code; return a finished result or a streaming plan.

    Raises :class:`PartialHaulError` for the 416-reset path.
    """
    resp_etag = parse_etag(headers.get("ETag", ""))
    resp_ct = headers.get("Content-Type", "")

    logger.debug(
        "HTTP response",
        extra={
            "pyhaul_status": status,
            "pyhaul_etag": str(resp_etag) if resp_etag else "",
        },
    )

    if status == _HTTP_416:
        return _on_416(headers, prep, state, resp_etag=resp_etag, content_type=resp_ct)

    if status == _HTTP_206:
        return _plan_206(headers, prep, state, resp_etag=resp_etag, content_type=resp_ct)

    if status == _HTTP_200:
        return _plan_200(headers, prep, state, resp_etag=resp_etag, content_type=resp_ct)

    if status in _REDIRECT_STATUSES:
        loc = (headers.get("Location") or "").strip()
        if loc:
            reason = (
                f"The server responded with HTTP {status} and redirected to {loc!r}, "
                "but redirects were not followed, so the download never reached the final resource."
            )
        else:
            reason = (
                f"The server responded with HTTP {status} with a redirect to another URL, "
                "but redirects were not followed, so the download never reached the final resource."
            )
        raise UnexpectedStatusError(status, headers, reason=reason)

    raise UnexpectedStatusError(status, headers)


def _on_416(
    headers: TransportHeaders,
    prep: PrepareHaul,
    state: HaulState,
    *,
    resp_etag: ETag,
    content_type: str,
) -> CompleteHaul:
    """Handle 416 Range Not Satisfiable.

    If the server confirms our cursor matches the resource length, the
    file is already complete — finalize and return.  Otherwise reset
    the checkpoint and raise :class:`PartialHaulError`.
    """
    cr_raw = headers.get("Content-Range")
    if cr_raw:
        cr = parse_content_range(cr_raw)
        if cr.is_unsatisfied and cr.instance_length is not None and cr.instance_length == prep.cursor:
            if prep.part_path.exists() and prep.part_path.stat().st_size > prep.cursor:
                with prep.part_path.open("r+b") as f:
                    f.truncate(prep.cursor)

            # Re-read for tree hash
            final_sha = HashBuilder.hash_file(prep.part_path, block_size=prep.block_size)

            return finalize(
                prep.dest_path,
                prep.part_path,
                prep.ctrl_path,
                state,
                valid_length=prep.cursor,
                sha256=final_sha,
                etag=resp_etag or prep.stored_etag,
                content_type=content_type,
                t0=prep.t0,
            )

    _reset_checkpoint(prep.ctrl_path, prep.start, prep.block_size)
    state.valid_length = 0
    state.hashes = []
    raise PartialHaulError("416 Range Not Satisfiable — checkpoint reset")


def _plan_206(
    headers: TransportHeaders,
    prep: PrepareHaul,
    state: HaulState,
    *,
    resp_etag: ETag,
    content_type: str,
) -> StreamPlan:
    cr_raw = headers.get("Content-Range")
    if not cr_raw:
        raise ServerMisconfiguredError("206 without Content-Range")
    cr = parse_content_range(cr_raw)
    if cr.is_unsatisfied:
        raise ServerMisconfiguredError("206 with unsatisfied Content-Range")
    assert cr.start is not None  # noqa: S101 — type narrowing after is_unsatisfied guard
    if cr.start != prep.request_byte:
        raise ServerMisconfiguredError(f"Content-Range start {cr.start} != requested {prep.request_byte}")

    # BUG FIX: Verify ETag if server sent one.
    if resp_etag and prep.stored_etag and resp_etag != prep.stored_etag:
        logger.debug(
            "ETag mismatch on 206, aborting",
            extra={
                "pyhaul_etag_stored": str(prep.stored_etag),
                "pyhaul_etag_response": str(resp_etag),
            },
        )
        raise ServerMisconfiguredError(f"ETag mismatch on 206: server={resp_etag} stored={prep.stored_etag}")

    new_etag = resp_etag or prep.stored_etag
    new_rl = cr.instance_length
    new_extent = (cr.instance_length - prep.start) if cr.instance_length is not None else None

    # Resume hash state
    hb = HashBuilder(block_size=prep.block_size, initial_hashes=prep.hashes)

    # If we are resuming at a non-block boundary, we MUST re-read the partial tail
    # of the last block to warm up the hasher.
    num_full_blocks = len(prep.hashes)
    bytes_hashed = num_full_blocks * prep.block_size
    bytes_to_re_read = prep.cursor - bytes_hashed

    if bytes_to_re_read > 0:
        with prep.part_path.open("rb") as f:
            f.seek(bytes_hashed)
            tail = f.read(bytes_to_re_read)
            if len(tail) != bytes_to_re_read:
                raise ControlFileError(f"could not re-read {bytes_to_re_read} byte tail for hashing")

            # Verify integrity of re-read tail
            if prep.tail_hash:
                actual_tail_hash = hashlib.sha256(tail).digest()
                if actual_tail_hash != prep.tail_hash:
                    raise ControlFileError("integrity error: local tail corruption detected")

            hb.update(tail)

    state.block_size = prep.block_size
    state.hashes = hb.completed_hashes.copy()
    state.reported_length = new_rl

    logger.debug(
        "Using 206 Partial Content (range response)",
        extra={
            "pyhaul_range_start": cr.start,
            "pyhaul_reported_length": new_rl,
            "pyhaul_extent": new_extent,
        },
    )

    return StreamPlan(
        start=prep.start,
        cursor=prep.cursor,
        extent=new_extent,
        etag=new_etag,
        reported_length=new_rl,
        content_type=content_type,
        hb=hb,
    )


def _parse_content_length(raw: str | None) -> int | None:
    """Parse ``Content-Length`` for full-response (200) planning.

    Values are normally stripped by :class:`~pyhaul.transport.types.TransportHeaders`;
    we still strip here for defense in depth.

    Some misbehaving proxies emit comma-separated duplicate lengths (see RFC 7230).
    """
    if raw is None:
        return None
    s = raw.strip()
    tokens = [t.strip() for t in s.split(",") if t.strip()]
    if not tokens or any(not t.isdigit() for t in tokens):
        return None
    if len(set(tokens)) > 1:
        return None
    return int(tokens[0])


def _plan_200(
    headers: TransportHeaders,
    prep: PrepareHaul,
    state: HaulState,
    *,
    resp_etag: ETag,
    content_type: str,
) -> StreamPlan:
    resp_cl = _parse_content_length(headers.get("Content-Length"))

    if resp_cl == 0 and prep.cursor > 0:
        logger.info(
            "200 OK with Content-Length 0 while resuming had partial progress "
            "(cursor=%s, request_byte=%s); treating as empty full representation "
            "and discarding prior bytes — often CDN/misconfiguration vs Range",
            prep.cursor,
            prep.request_byte,
            extra={
                "pyhaul_resume_cursor": prep.cursor,
                "pyhaul_request_byte": prep.request_byte,
                "pyhaul_content_length": resp_cl,
            },
        )

    state.valid_length = 0
    state.hashes = []
    state.reported_length = resp_cl

    logger.debug(
        "Using 200 full representation (ignoring range)",
        extra={"pyhaul_content_length": resp_cl},
    )

    return StreamPlan(
        start=0,
        cursor=0,
        extent=resp_cl,
        etag=resp_etag,
        reported_length=resp_cl,
        content_type=content_type,
        hb=HashBuilder(block_size=state.block_size),
    )


# ─── File + write helpers ─────────────────────────────────────────


def open_part_file(plan: StreamPlan, part_path: Path) -> int:
    """Open (or create) the ``.part`` file, allocate, and seek to cursor.

    Returns an ``os``-level file descriptor.  The caller MUST close it.
    """
    part_path.parent.mkdir(parents=True, exist_ok=True)
    open_flags = os.O_RDWR | os.O_CREAT
    # Windows: default is text mode; O_BINARY turns off CRLF translation for raw bytes
    # (os.write would otherwise corrupt network chunk bytes and file hashes / lengths).
    open_flags |= getattr(os, "O_BINARY", 0)
    fd = os.open(str(part_path), open_flags, 0o644)
    try:
        if plan.extent is not None and plan.extent > 0:
            allocate_file(fd, total_length=plan.extent)
        os.lseek(fd, plan.cursor, os.SEEK_SET)
    except BaseException:
        os.close(fd)
        raise
    return fd


def write_chunk(
    fd: int,
    chunk: bytes,
    plan: StreamPlan,
    prep: PrepareHaul,
    state: HaulState,
    flush_every: int,
) -> None:
    """Write *chunk* to *fd*, advance counters, and flush ctrl if threshold hit."""
    os.write(fd, chunk)
    n = len(chunk)
    plan.cursor += n
    plan.bytes_since_flush += n
    state.bytes_read += n
    state.valid_length = plan.cursor

    plan.hb.update(chunk)
    state.hashes = plan.hb.completed_hashes.copy()

    if plan.bytes_since_flush >= flush_every:
        datasync(fd)
        save_checkpoint(prep.ctrl_path, plan, prep)
        plan.bytes_since_flush = 0


def after_stream(plan: StreamPlan, prep: PrepareHaul, state: HaulState) -> CompleteHaul:
    """Post-loop: compare ``cursor`` to ``extent`` when known, trim junk tail, finalize.

    When ``plan.extent`` is ``None`` (e.g. **206** with ``Content-Range`` whose
    total length is ``*``), there is no byte budget for ``cursor`` to satisfy,
    so a truncated chunked body cannot be detected as incomplete here.
    """
    if plan.extent is not None and plan.cursor < plan.extent:
        save_checkpoint(prep.ctrl_path, plan, prep)
        raise PartialHaulError("stream ended before extent reached")

    actual = prep.part_path.stat().st_size
    if actual > plan.cursor:
        with prep.part_path.open("r+b") as f:
            f.truncate(plan.cursor)

    return finalize(
        prep.dest_path,
        prep.part_path,
        prep.ctrl_path,
        state,
        valid_length=plan.cursor,
        sha256=plan.hb.finalize(),
        etag=plan.etag,
        content_type=plan.content_type,
        t0=prep.t0,
    )


# ─── Finalization ─────────────────────────────────────────────────


def finalize(
    dest_path: Path,
    part_path: Path,
    ctrl_path: Path,
    state: HaulState,
    *,
    valid_length: int,
    sha256: str,
    etag: ETag,
    content_type: str,
    t0: float,
) -> CompleteHaul:
    """Rename ``.part`` -> dest, delete ``.ctrl``, hash the result."""
    part_path.rename(dest_path)
    ctrl_path.unlink(missing_ok=True)
    state.is_complete = True
    state.valid_length = valid_length
    return CompleteHaul(
        elapsed=time.monotonic() - t0,
        sha256=sha256,
        etag=etag,
        content_type=content_type,
    )


def _reset_checkpoint(ctrl_path: Path, start: int, block_size: int) -> None:
    ctrl_path.parent.mkdir(parents=True, exist_ok=True)
    cp = Checkpoint(
        version=LATEST_VERSION,
        start=start,
        extent=None,
        valid_length=0,
        etag=ETag(""),
        block_size=block_size,
        hashes=[],
        tail_hash=None,
        reported_length=None,
    )
    write_atomic(ctrl_path, registry.dump(cp))


def save_checkpoint(path: Path, plan: StreamPlan, _prep: PrepareHaul) -> None:
    """Atomically persist the current download checkpoint to *path*."""
    cp = Checkpoint(
        version=LATEST_VERSION,
        start=plan.start,
        extent=plan.extent,
        valid_length=plan.cursor,
        etag=plan.etag,
        block_size=plan.hb.block_size,
        hashes=plan.hb.completed_hashes.copy(),
        tail_hash=plan.hb.current_digest,
        reported_length=plan.reported_length,
    )
    write_atomic(path, registry.dump(cp))


def flush_dirty(fd: int, plan: StreamPlan, prep: PrepareHaul) -> None:
    """If any bytes were written since the last periodic flush, persist now.

    Called from the engine's ``finally`` / ``except`` blocks so the
    on-disk checkpoint is accurate before an exception propagates.
    """
    if plan.bytes_since_flush > 0:
        datasync(fd)
        save_checkpoint(prep.ctrl_path, plan, prep)
        plan.bytes_since_flush = 0
