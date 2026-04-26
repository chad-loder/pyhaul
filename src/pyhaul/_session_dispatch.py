"""Auto-coerce popular HTTP client objects into pyhaul transport sessions.

Dispatch is based on MRO class identity (module + qualname), not duck-typing,
so no optional dependency is imported until a matching object actually arrives.
"""

from __future__ import annotations

from typing import Any

from pyhaul.transport.protocols import AsyncTransportSession, TransportSession

_SYNC_DISPATCH: list[tuple[str, str, str, str]] = [
    ("requests.sessions", "Session", "pyhaul.transport.requests_adapter", "RequestsAdapter"),
    ("niquests.sessions", "Session", "pyhaul.transport.niquests_adapter", "NiquestsAdapter"),
    ("httpx._client", "Client", "pyhaul.transport.httpx_adapter", "HttpxAdapter"),
    ("urllib3.poolmanager", "PoolManager", "pyhaul.transport.urllib3_adapter", "Urllib3Adapter"),
    ("urllib3.poolmanager", "ProxyManager", "pyhaul.transport.urllib3_adapter", "Urllib3Adapter"),
]

_ASYNC_DISPATCH: list[tuple[str, str, str, str]] = [
    ("niquests.sessions", "AsyncSession", "pyhaul.transport.niquests_adapter", "AsyncNiquestsAdapter"),
    ("httpx._client", "AsyncClient", "pyhaul.transport.httpx_adapter", "AsyncHttpxAdapter"),
]


def _resolve(obj: Any, table: list[tuple[str, str, str, str]]) -> Any | None:
    for cls in type(obj).__mro__:
        key = (cls.__module__, cls.__qualname__)
        for mod_name, cls_name, adapter_mod, adapter_cls in table:
            if key == (mod_name, cls_name):
                import importlib

                m = importlib.import_module(adapter_mod)  # nosemgrep: non-literal-import
                return getattr(m, adapter_cls)(obj)
    return None


def coerce_sync_session(obj: object) -> TransportSession:
    """Wrap a raw HTTP client as a ``TransportSession``, or pass through."""
    if isinstance(obj, TransportSession):
        return obj
    result = _resolve(obj, _SYNC_DISPATCH)
    if isinstance(result, TransportSession):
        return result
    raise TypeError(
        f"expected a TransportSession or a supported HTTP client "
        f"(requests.Session, niquests.Session, httpx.Client, urllib3.PoolManager), "
        f"got {type(obj).__module__}.{type(obj).__qualname__}"
    )


def coerce_async_session(obj: object) -> AsyncTransportSession:
    """Wrap a raw async HTTP client as an ``AsyncTransportSession``, or pass through."""
    if isinstance(obj, AsyncTransportSession):
        return obj
    result = _resolve(obj, _ASYNC_DISPATCH)
    if isinstance(result, AsyncTransportSession):
        return result
    raise TypeError(
        f"expected an AsyncTransportSession or a supported async HTTP client "
        f"(niquests.AsyncSession, httpx.AsyncClient), "
        f"got {type(obj).__module__}.{type(obj).__qualname__}"
    )
