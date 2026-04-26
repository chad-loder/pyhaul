"""Auto-coerce HTTP client objects into pyhaul transport sessions.

Built-in adapters handle requests, niquests, httpx, and urllib3.  Third-party
packages can register additional adapters via :func:`register_sync_adapter`
and :func:`register_async_adapter` — no monkeypatching required.

Each adapter factory is a callable ``(object) -> T | None``.  The dispatch
walks the factory list in registration order; the first non-``None`` result
wins.  Built-in factories use lazy imports so no backend is pulled until a
matching client object actually arrives.
"""

from __future__ import annotations

from collections.abc import Callable

from pyhaul.transport.protocols import AsyncTransportSession, TransportSession

type SyncAdapterFactory = Callable[[object], TransportSession | None]
"""Callable that wraps a raw sync HTTP client, or returns ``None``."""

type AsyncAdapterFactory = Callable[[object], AsyncTransportSession | None]
"""Callable that wraps a raw async HTTP client, or returns ``None``."""


# ---------------------------------------------------------------------------
# Built-in factories (lazy imports — no backend pulled until needed)
# ---------------------------------------------------------------------------


def _try_requests(obj: object) -> TransportSession | None:
    try:
        import requests
    except ImportError:
        return None
    if isinstance(obj, requests.Session):
        from pyhaul.transport.requests_adapter import RequestsAdapter

        return RequestsAdapter(obj)
    return None


def _try_niquests(obj: object) -> TransportSession | None:
    try:
        import niquests
    except ImportError:
        return None
    if isinstance(obj, niquests.Session):
        from pyhaul.transport.niquests_adapter import NiquestsAdapter

        return NiquestsAdapter(obj)
    return None


def _try_httpx(obj: object) -> TransportSession | None:
    try:
        import httpx
    except ImportError:
        return None
    if isinstance(obj, httpx.Client):
        from pyhaul.transport.httpx_adapter import HttpxAdapter

        return HttpxAdapter(obj)
    return None


def _try_urllib3(obj: object) -> TransportSession | None:
    try:
        import urllib3
    except ImportError:
        return None
    if isinstance(obj, urllib3.PoolManager):
        from pyhaul.transport.urllib3_adapter import Urllib3Adapter

        return Urllib3Adapter(obj)
    return None


def _try_async_niquests(obj: object) -> AsyncTransportSession | None:
    try:
        import niquests
    except ImportError:
        return None
    if isinstance(obj, niquests.AsyncSession):
        from pyhaul.transport.niquests_adapter import AsyncNiquestsAdapter

        return AsyncNiquestsAdapter(obj)
    return None


def _try_async_httpx(obj: object) -> AsyncTransportSession | None:
    try:
        import httpx
    except ImportError:
        return None
    if isinstance(obj, httpx.AsyncClient):
        from pyhaul.transport.httpx_adapter import AsyncHttpxAdapter

        return AsyncHttpxAdapter(obj)
    return None


def _try_async_aiohttp(obj: object) -> AsyncTransportSession | None:
    try:
        import aiohttp
    except ImportError:
        return None
    if isinstance(obj, aiohttp.ClientSession):
        from pyhaul.transport.aiohttp_adapter import AsyncAiohttpAdapter

        return AsyncAiohttpAdapter(obj)
    return None


# ---------------------------------------------------------------------------
# Registries
# ---------------------------------------------------------------------------

_sync_factories: list[SyncAdapterFactory] = [
    _try_requests,
    _try_niquests,
    _try_httpx,
    _try_urllib3,
]

_async_factories: list[AsyncAdapterFactory] = [
    _try_async_niquests,
    _try_async_httpx,
    _try_async_aiohttp,
]


def register_sync_adapter(factory: SyncAdapterFactory) -> None:
    """Append a sync adapter factory.

    *factory* is called with a raw client object and must return a
    :class:`~pyhaul.transport.protocols.TransportSession` or ``None``.
    Factories are tried in registration order; the first non-``None``
    result wins.
    """
    _sync_factories.append(factory)


def register_async_adapter(factory: AsyncAdapterFactory) -> None:
    """Append an async adapter factory.

    *factory* is called with a raw client object and must return an
    :class:`~pyhaul.transport.protocols.AsyncTransportSession` or ``None``.
    """
    _async_factories.append(factory)


# ---------------------------------------------------------------------------
# Public coercion API
# ---------------------------------------------------------------------------


def coerce_sync_session(obj: object) -> TransportSession:
    """Wrap a raw HTTP client as a ``TransportSession``, or pass through."""
    if isinstance(obj, TransportSession):
        return obj
    for factory in _sync_factories:
        result = factory(obj)
        if result is not None:
            return result
    raise TypeError(
        f"No sync adapter for {type(obj).__module__}.{type(obj).__qualname__}. "
        f"Install a pyhaul extra (pyhaul[niquests], pyhaul[requests], "
        f"pyhaul[httpx], pyhaul[urllib3]) or call register_sync_adapter()."
    )


def coerce_async_session(obj: object) -> AsyncTransportSession:
    """Wrap a raw async HTTP client as an ``AsyncTransportSession``, or pass through."""
    if isinstance(obj, AsyncTransportSession):
        return obj
    for factory in _async_factories:
        result = factory(obj)
        if result is not None:
            return result
    raise TypeError(
        f"No async adapter for {type(obj).__module__}.{type(obj).__qualname__}. "
        f"Install a pyhaul extra (pyhaul[niquests], pyhaul[httpx]) "
        f"or call register_async_adapter()."
    )
