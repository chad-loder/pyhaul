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
from collections.abc import Callable
from pathlib import Path

from pyhaul._engine_common import (
    DEFAULT_CHUNK,
    DEFAULT_FLUSH,
    PrepareHaul,
    StreamPlan,
    after_stream,
    datasync,
    flush_dirty,
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


async def haul_async(
    url: str,
    client: AsyncTransportSession | object,
    *,
    dest: str | Path,
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
    prep = prepare_haul(url, dest)
    loop = asyncio.get_running_loop()

    try:
        async with transport.stream_get(prep.parsed_url, headers=prep.merged_headers) as resp:
            action = handle_response(resp.status_code, resp.headers, prep, state)

            if not isinstance(action, StreamPlan):
                return action

            plan = action
            logger.debug(
                "Beginning response body stream",
                extra={"pyhaul_part_path": str(prep.part_path)},
            )
            fd = open_part_file(plan, prep.part_path)
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

                await loop.run_in_executor(None, datasync, fd)
            except TransportError as exc:
                flush_dirty(fd, plan, prep)
                os.close(fd)
                raise PartialHaulError("connection lost during stream") from exc
            finally:
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
