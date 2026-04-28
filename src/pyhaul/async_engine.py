"""Async download engine: single-range, cursor-based, resumable.

Async mirror of :mod:`pyhaul.engine`.  All non-I/O logic is shared via
:mod:`pyhaul._engine_common`; only the session context manager and the
chunk iterator differ (``async with`` / ``async for``).
"""

from __future__ import annotations

import contextlib
import logging
import os
from collections.abc import Callable
from pathlib import Path

from pyhaul._engine_common import (
    DEFAULT_CHUNK,
    DEFAULT_FLUSH,
    StreamPlan,
    after_stream,
    datasync,
    flush_dirty,
    handle_response,
    open_part_file,
    prepare_haul,
    write_chunk,
)
from pyhaul._session_dispatch import coerce_async_session
from pyhaul._types import CompleteHaul, HaulState
from pyhaul.transport.errors import TransportError
from pyhaul.transport.protocols import AsyncTransportSession

logger = logging.getLogger(__name__)


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

    *client* can be an :class:`~pyhaul.transport.protocols.AsyncTransportSession`
    or a raw async HTTP client (``niquests.AsyncSession``, ``httpx.AsyncClient``).
    Raw clients are auto-wrapped.

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
                    write_chunk(fd, chunk, plan, prep, state, flush_every)
                    if on_progress is not None:
                        on_progress(state)
                datasync(fd)
            except TransportError:
                flush_dirty(fd, plan, prep)
                os.close(fd)
                raise
            finally:
                with contextlib.suppress(OSError):
                    os.close(fd)

            return after_stream(plan, prep, state)
    except TransportError as te:
        original = te.__cause__
        if original is not None:
            raise original from None
        raise
