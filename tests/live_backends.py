"""Live HTTP transport matrix helpers for the test suite.

Each supported optional client (niquests, requests, httpx, urllib3) gets the
same integration coverage via :class:`tests.conftest.HttpTest`.
"""

from __future__ import annotations

from pyhaul.transport.protocols import TransportSession

LIVE_BACKENDS: tuple[str, ...] = ("niquests", "requests", "httpx", "urllib3")


def make_native(backend: str) -> object:
    """Construct a fresh native client for *backend* (caller owns lifecycle)."""
    if backend == "niquests":
        import niquests as nq

        return nq.Session()
    if backend == "requests":
        import requests as rq

        return rq.Session()
    if backend == "httpx":
        import httpx as hx

        return hx.Client()
    if backend == "urllib3":
        import urllib3 as u3

        return u3.PoolManager()
    msg = f"unknown transport backend {backend!r}"
    raise ValueError(msg)


def make_transport(backend: str, native: object) -> TransportSession:
    """Wrap *native* in the matching :class:`TransportSession` adapter."""
    if backend == "niquests":
        from pyhaul.transport.niquests_adapter import NiquestsAdapter

        return NiquestsAdapter(native)  # type: ignore[arg-type]
    if backend == "requests":
        from pyhaul.transport.requests_adapter import RequestsAdapter

        return RequestsAdapter(native)  # type: ignore[arg-type]
    if backend == "httpx":
        from pyhaul.transport.httpx_adapter import HttpxAdapter

        return HttpxAdapter(native)  # type: ignore[arg-type]
    if backend == "urllib3":
        from pyhaul.transport.urllib3_adapter import Urllib3Adapter

        return Urllib3Adapter(native)  # type: ignore[arg-type]
    msg = f"unknown transport backend {backend!r}"
    raise ValueError(msg)


def close_native(native: object) -> None:
    """Shut down a native HTTP client by calling its ``close`` or ``clear`` method."""
    close = getattr(native, "close", None)
    if callable(close):
        close()
    else:
        # urllib3 PoolManager might only have clear()
        clear = getattr(native, "clear", None)
        if callable(clear):
            clear()
