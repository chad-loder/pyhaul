"""Async download engine: single-range, cursor-based, resumable.

Async mirror of :mod:`pyhaul.engine`.  All non-I/O logic is shared via
:mod:`pyhaul._engine_common`; only the session context manager and the
chunk iterator differ (``async with`` / ``async for``).

Blocking disk preparation (:func:`~pyhaul._engine_common.prepare_haul`),
opening/preallocating the ``.part`` file (:func:`~pyhaul._engine_common.open_part_file`),
HTTP ``416`` completion handling (including tree-hash of large ``.part`` files via
:func:`~pyhaul._engine_common.handle_response`), post-download finalization
(:func:`~pyhaul._engine_common.after_stream` — stat / truncate / rename / unlink),
and ``fdatasync`` / checkpoint writes (including the best-effort dirty flush and
close on ``TransportError``, ``asyncio.CancelledError``, or other abnormal exits in ``_flush_dirty_and_close``)
use :func:`asyncio.loop.run_in_executor`
(the default :class:`~concurrent.futures.ThreadPoolExecutor`) so the event loop
stays responsive under concurrent downloads, slow filesystems, or streaming flushes.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
import logging
import os
from collections.abc import Mapping
from http import HTTPStatus
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
from pyhaul._types import (
    AsyncProgressCallback,
    CompleteHaul,
    HaulState,
    PartialHaulError,
)
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


async def _maybe_await_progress(cb: AsyncProgressCallback, state: HaulState) -> None:
    out = cb(state)
    if inspect.isawaitable(out):
        await out


async def haul_async(  # noqa: C901, PLR0912, PLR0915 — stream loop + transport/error paths; keep linear
    url: str,
    client: AsyncTransportSession | object,
    *,
    dest: str | Path,
    headers: Mapping[str, str] | None = None,
    state: HaulState | None = None,
    chunk_size: int = DEFAULT_CHUNK,
    flush_every: int = DEFAULT_FLUSH,
    on_progress: AsyncProgressCallback | None = None,
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

    *on_progress*, if set, is called after each chunk.  It may be an
    ordinary synchronous callable or return an awaitable (for example
    an ``async def``); when the return value is awaitable, it is
    awaited before the next chunk is read.  :func:`pyhaul.engine.haul`
    accepts only synchronous callbacks.

    Returns :class:`CompleteHaul` on success.  Raises
    :class:`PartialHaulError` when the stream ends before all bytes
    arrive (the ``.part`` and ``.part.ctrl`` files remain on disk for
    the next call to resume from).  Transport errors from the
    underlying HTTP library propagate unwrapped.
    """
    if state is None:
        state = HaulState()
    transport = coerce_async_session(client)

    loop = asyncio.get_running_loop()
    prep = await loop.run_in_executor(
        None,
        functools.partial(prepare_haul, url, dest, user_headers=headers),
    )

    prepare_fn = getattr(transport, "prepare_headers", None)
    final_headers = prepare_fn(prep.merged_headers) if prepare_fn else prep.merged_headers

    try:
        async with transport.stream_get(prep.parsed_url, headers=final_headers) as resp:
            # 416 → _on_416 may SHA-hash multi-GB .part files before finalize — offload.
            if resp.status_code == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE:
                action = await loop.run_in_executor(
                    None,
                    functools.partial(
                        handle_response,
                        resp.status_code,
                        resp.headers,
                        prep,
                        state,
                    ),
                )
            else:
                action = handle_response(resp.status_code, resp.headers, prep, state)

            if not isinstance(action, StreamPlan):
                return action

            plan = action
            logger.debug(
                "Beginning response body stream",
                extra={"pyhaul_part_path": str(prep.part_path)},
            )
            fd = await loop.run_in_executor(
                None,
                functools.partial(open_part_file, plan, prep.part_path),
            )
            # Once True, the executor finalizer owns *fd* and is solely
            # responsible for closing it.  ``finally`` below may call
            # ``_flush_dirty_and_close`` if the stream exits without a finisher—
            # preventing a cancellation race where the loop thread closes *fd*
            # while ``datasync(fd)`` is still running (EBADF).
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
                        await _maybe_await_progress(on_progress, state)
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
            except asyncio.CancelledError:
                # Persist bytes written since the last periodic flush so cancel-resume
                # matches abrupt-exit checkpoint semantics.
                fd_finalized_by_executor = True
                await loop.run_in_executor(
                    None,
                    _flush_dirty_and_close,
                    fd,
                    plan,
                    prep,
                )
                raise
            else:
                # Normal stream end: final datasync + close on the executor thread.
                fd_finalized_by_executor = True
                await loop.run_in_executor(None, _datasync_and_close, fd)
            finally:
                if not fd_finalized_by_executor:
                    await loop.run_in_executor(
                        None,
                        _flush_dirty_and_close,
                        fd,
                        plan,
                        prep,
                    )
                    fd_finalized_by_executor = True

            return await loop.run_in_executor(
                None,
                functools.partial(after_stream, plan, prep, state),
            )
    except TransportConnectionError as ce:
        raise PartialHaulError("connection error") from ce
    except TransportError as te:
        original = te.__cause__
        if original is not None:
            raise original from None
        raise
