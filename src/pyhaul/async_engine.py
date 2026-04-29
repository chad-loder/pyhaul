"""Async download engine: single-range, cursor-based, resumable.

Async mirror of :mod:`pyhaul.engine`.  All non-I/O logic is shared via
:mod:`pyhaul._engine_common`; only the session context manager and the
chunk iterator differ (``async with`` / ``async for``).

Blocking disk I/O (``fdatasync``, checkpoint writes) is offloaded to the
default executor via :func:`asyncio.loop.run_in_executor` so the event
loop stays responsive for other tasks during periodic flushes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import Callable, Mapping
from pathlib import Path

from pyhaul._engine_common import (
    DEFAULT_CHUNK,
    DEFAULT_FLUSH,
    PrepareHaul,
    StreamPlan,
    after_stream,
    datasync,
    handle_response,
    open_part_file,
    prepare_haul,
    save_checkpoint,
)
from pyhaul._session_dispatch import coerce_async_session
from pyhaul._types import CompleteHaul, HaulState, PartialHaulError
from pyhaul.transport.errors import TransportConnectionError, TransportError
from pyhaul.transport.protocols import AsyncTransportSession

logger = logging.getLogger(__name__)


def _sync_flush(fd: int, plan: StreamPlan, prep: PrepareHaul) -> None:
    """Flush data to disk and persist checkpoint (runs in executor thread)."""
    datasync(fd)
    save_checkpoint(prep.ctrl_path, plan, prep)


def _datasync_and_close(fd: int) -> None:
    """Final fdatasync then close fd, atomically inside the executor.

    Bundling the datasync and close into a single executor function
    eliminates the cancellation race where the event-loop thread closes
    *fd* while an in-flight ``datasync(fd)`` is still running in a worker
    thread (which surfaces as ``OSError: [Errno 9] Bad file descriptor``).
    Both syscalls now run serially on the same thread.
    """
    try:
        datasync(fd)
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


def _flush_dirty_and_close(fd: int, plan: StreamPlan, prep: PrepareHaul) -> None:
    """Best-effort dirty flush + checkpoint, then close fd.

    Used on the ``TransportError`` path.  Same atomicity story as
    :func:`_datasync_and_close`: every operation that touches *fd* runs
    on the executor thread, so no event-loop-side close can race with
    this work.
    """
    try:
        if plan.bytes_since_flush > 0:
            with contextlib.suppress(OSError):
                datasync(fd)
            with contextlib.suppress(OSError):
                save_checkpoint(prep.ctrl_path, plan, prep)
            plan.bytes_since_flush = 0
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


async def haul_async(  # noqa: C901 — stream loop + transport/error paths; keep linear
    url: str,
    client: AsyncTransportSession | object,
    *,
    dest: str | Path,
    headers: Mapping[str, str] | None = None,
    state: HaulState | None = None,
    chunk_size: int = DEFAULT_CHUNK,
    flush_every: int = DEFAULT_FLUSH,
    on_progress: Callable[[HaulState], None] | None = None,
) -> CompleteHaul:
    """Async equivalent of :func:`pyhaul.engine.haul`.

    *client* is your async HTTP session — ``httpx.AsyncClient``,
    ``niquests.AsyncSession``, or ``aiohttp.ClientSession``.

    *url* is validated on entry; invalid schemes or missing hosts raise
    :class:`ValueError`.

    *headers*, when provided, are merged with pyhaul's structural requirements.

    *state*, when provided, is a :class:`HaulState` updated in-place
    throughout the download — always accurate regardless of how the
    function exits.

    *on_progress*, if set, is called after each chunk (synchronous
    callback; same contract as :func:`pyhaul.engine.haul`).

    Returns :class:`CompleteHaul` on success.  Raises
    :class:`PartialHaulError` when the stream ends before all bytes
    arrive (the ``.part`` and ``.part.ctrl`` files remain on disk for
    the next call to resume from).  Transport errors from the
    underlying HTTP library propagate unwrapped.
    """
    if state is None:
        state = HaulState()
    transport = coerce_async_session(client)

    prep = prepare_haul(url, dest, user_headers=headers)
    prepare_fn = getattr(transport, "prepare_headers", None)
    final_headers = prepare_fn(prep.merged_headers) if prepare_fn else prep.merged_headers

    loop = asyncio.get_running_loop()

    try:
        async with transport.stream_get(prep.parsed_url, headers=final_headers) as resp:
            action = handle_response(resp.status_code, resp.headers, prep, state)

            if not isinstance(action, StreamPlan):
                return action

            plan = action
            logger.debug(
                "Beginning response body stream",
                extra={"pyhaul_part_path": str(prep.part_path)},
            )
            fd = open_part_file(plan, prep.part_path)
            # Once True, the executor finalizer owns *fd* and is solely
            # responsible for closing it.  The event-loop-side fallback
            # close in the outer ``finally`` then becomes a no-op.  This
            # prevents a cancellation race where the loop thread closes
            # *fd* while a queued executor task is still calling
            # ``datasync(fd)`` on it (EBADF).
            fd_finalized_by_executor = False
            try:
                async for chunk in resp.aiter_raw_bytes(chunk_size=chunk_size):
                    os.write(fd, chunk)
                    n = len(chunk)
                    plan.cursor += n
                    plan.bytes_since_flush += n
                    state.bytes_read += n
                    state.valid_length = plan.cursor
                    plan.hb.update(chunk)
                    state.hashes = plan.hb.completed_hashes.copy()

                    if plan.bytes_since_flush >= flush_every:
                        await loop.run_in_executor(None, _sync_flush, fd, plan, prep)
                        plan.bytes_since_flush = 0

                    if on_progress is not None:
                        on_progress(state)

                # Hand *fd* over to the executor for the final datasync +
                # close.  The flag is set BEFORE the await so a
                # cancellation here does not race the executor: the
                # worker thread runs to completion and closes *fd*
                # serially after datasync, with no concurrent close
                # from the loop thread.
                fd_finalized_by_executor = True
                await loop.run_in_executor(None, _datasync_and_close, fd)
            except TransportError as exc:
                fd_finalized_by_executor = True
                await loop.run_in_executor(
                    None,
                    _flush_dirty_and_close,
                    fd,
                    plan,
                    prep,
                )
                raise PartialHaulError("connection lost during stream") from exc
            finally:
                if not fd_finalized_by_executor:
                    # Reachable only if a synchronous error occurred
                    # between opening *fd* and any executor handoff.
                    # Safe to close on the loop thread because no
                    # executor task is in flight against this fd.
                    with contextlib.suppress(OSError):
                        os.close(fd)

            return after_stream(plan, prep, state)
    except TransportConnectionError as ce:
        raise PartialHaulError("connection error") from ce
    except TransportError as te:
        original = te.__cause__
        if original is not None:
            raise original from None
        raise
