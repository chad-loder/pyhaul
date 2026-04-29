"""Sync remote metadata probe (HEAD + optional ranged GET).

One :func:`probe` call performs at least one HTTP round-trip (``HEAD``), and may add a
byte-ranged ``GET`` when ``HEAD`` did not expose everything needed for common shard planners.
"""

from __future__ import annotations

from collections.abc import Mapping

from pyhaul._probe_common import run_probe_sync
from pyhaul._session_dispatch import coerce_sync_session
from pyhaul._types import ProbeResult
from pyhaul.transport.types import TransportRequestOptions


def probe(
    url: str,
    client: object,
    *,
    headers: Mapping[str, str] | None = None,
    options: TransportRequestOptions | None = None,
) -> ProbeResult:
    """Discover static metadata for *url* using the caller's HTTP session.

    The sequence mirrors pypdl-style probing: send ``HEAD``, then — when metadata is still
    incomplete — ``GET`` with ``Range: bytes=0-0`` and drain the tiny body.

    *client* is coerced with the same adapter registry as :func:`~pyhaul.engine.haul`.
    Transport errors from the underlying HTTP library propagate unwrapped.
    """
    transport = coerce_sync_session(client)
    return run_probe_sync(transport, url, headers=headers, options=options)


__all__ = ["probe"]
