"""Sync download engine: single-range, cursor-based, resumable.

One range, one session, one part file.  The caller borrows a
``TransportSession`` to the engine; the engine never closes it.
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
from pyhaul._session_dispatch import coerce_sync_session
from pyhaul._types import CompleteHaul, HaulState
from pyhaul.transport.errors import TransportError
from pyhaul.transport.protocols import TransportSession

logger = logging.getLogger(__name__)


def haul(
    url: str,
    client: TransportSession | object,
    *,
    dest: str | Path,
    state: HaulState | None = None,
    chunk_size: int = DEFAULT_CHUNK,
    flush_every: int = DEFAULT_FLUSH,
    on_progress: Callable[[HaulState], None] | None = None,
) -> CompleteHaul:
    """Download a single byte range to *dest*, resumably.

    *client* is your HTTP session — ``requests.Session``,
    ``httpx.Client``, ``niquests.Session``, or ``urllib3.PoolManager``.

    *url* is validated on entry; invalid schemes or missing hosts raise
    :class:`ValueError`.

    *state*, when provided, is a :class:`HaulState` updated in-place
    throughout the download — always accurate regardless of how the
    function exits.

    *on_progress*, if set, is called after each chunk is written with the
    current *state* (synchronous; keep it fast — e.g. progress UI, metrics).

    Returns :class:`CompleteHaul` on success.  Raises
    :class:`PartialHaulError` when the stream ends before all bytes
    arrive (the ``.part`` and ``.part.ctrl`` files remain on disk for
    the next call to resume from).  Transport errors from the
    underlying HTTP library propagate unwrapped.
    """
    if state is None:
        state = HaulState()
    transport = coerce_sync_session(client)
    prep = prepare_haul(url, dest)

    try:
        with transport.stream_get(prep.parsed_url, headers=prep.merged_headers) as resp:
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
                for chunk in resp.iter_raw_bytes(chunk_size=chunk_size):
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
