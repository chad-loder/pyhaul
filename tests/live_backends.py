"""Live HTTP transport matrix helpers for the test suite.

Every sync client (niquests, requests, httpx, urllib3) and async client
(niquests, aiohttp, httpx, wreq) gets the same integration coverage via
:class:`tests.conftest.HttpTest`.

``PYHAUL_LIVE_MATRIX=reduced`` keeps one third of the backends, chosen by
``(rotation index + OS index + Python minor) % 3``. Across three Python
versions on one OS, or three OSes on one Python version, every backend runs
at least once. CI sets it on every row except the full Linux and macOS rows.
"""

from __future__ import annotations

import contextlib
import os
import sys
from collections.abc import Iterable
from typing import Any, cast

from pyhaul.transport.protocols import TransportSession

ALL_SYNC_BACKENDS: tuple[str, ...] = ("niquests", "requests", "httpx", "urllib3")
ALL_ASYNC_BACKENDS: tuple[str, ...] = ("niquests", "aiohttp", "httpx", "wreq")

# Interleaves sync and async so each third holds both kinds. Reordering
# changes which CI row runs which backend.
_ROTATION: tuple[str, ...] = (
    "sync:niquests",
    "async:aiohttp",
    "sync:requests",
    "async:httpx",
    "sync:httpx",
    "async:niquests",
    "sync:urllib3",
    "async:wreq",
)
_OS_INDEX = {"linux": 0, "darwin": 1, "win32": 2}


def _in_matrix(key: str) -> bool:
    if os.environ.get("PYHAUL_LIVE_MATRIX", "full") != "reduced":
        return True
    row = _OS_INDEX.get(sys.platform, 0) + sys.version_info.minor
    return (_ROTATION.index(key) + row) % 3 == 0


LIVE_BACKENDS: tuple[str, ...] = tuple(b for b in ALL_SYNC_BACKENDS if _in_matrix(f"sync:{b}"))
LIVE_ASYNC_BACKENDS: tuple[str, ...] = tuple(b for b in ALL_ASYNC_BACKENDS if _in_matrix(f"async:{b}"))


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
    # PoolManager.clear() historically only dropped pool refs without always closing
    # sockets (e.g. RecentlyUsedContainer with no dispose_func). Newer releases use
    # TrafficPolice, which is not dict-like. Close every HTTPConnectionPool explicitly,
    # then clear.
    import urllib3

    if isinstance(native, urllib3.PoolManager):
        pools = native.pools
        keys = getattr(pools, "keys", None)
        if callable(keys):
            key_ids = cast(Iterable[Any], keys())  # noqa: TC006
            for key in list(key_ids):
                with contextlib.suppress(OSError):
                    pools[key].close()  # type: ignore[index]
        else:
            reg = getattr(pools, "_registry", None)
            if isinstance(reg, dict):
                for pool in list(reg.values()):
                    with contextlib.suppress(OSError):
                        pool.close()
        native.clear()
        return

    close = getattr(native, "close", None)
    if callable(close):
        close()
    else:
        # urllib3 PoolManager might only have clear()
        clear = getattr(native, "clear", None)
        if callable(clear):
            clear()
