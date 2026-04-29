"""Async remote metadata probe."""

from __future__ import annotations

from collections.abc import Mapping

from pyhaul._probe_common import run_probe_async
from pyhaul._session_dispatch import coerce_async_session
from pyhaul._types import ProbeResult
from pyhaul.transport.types import TransportRequestOptions


async def probe_async(
    url: str,
    client: object,
    *,
    headers: Mapping[str, str] | None = None,
    options: TransportRequestOptions | None = None,
) -> ProbeResult:
    """Async version of :func:`~pyhaul.probe.probe`.

    *client* is coerced with the same adapter registry as :func:`~pyhaul.async_engine.haul_async`.
    """
    transport = coerce_async_session(client)
    return await run_probe_async(transport, url, headers=headers, options=options)


__all__ = ["probe_async"]
