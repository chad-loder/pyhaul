"""Faithful proxies around transport sessions with a staged, fluent factory.

Chains read left-to-right as imperative steps, e.g.::

    transport_session_proxy().around(adapter).preparing_headers_with(strip_noise).build()

The staging model omits impossible sequences: you cannot chain a second ``.around()`` on
the recipe type, and ``.build()`` is unavailable until ``.around(inner)`` has run
(different public types per stage encode this without extra ``TypeVar`` ceremony).

``preparing_headers_with`` chains **after** the inner session's
:meth:`~pyhaul.transport.protocols.TransportSession.prepare_headers` (outer wins for
telemetry and conformance policy).

**With downloads** (``coerce_sync_session`` / ``haul``): ``.around()`` takes an existing
:class:`~pyhaul.transport.protocols.TransportSession` — adapt a raw HTTP client with the
adapter registry or explicit adapter constructors first, wrap with this builder, then pass
the result to ``haul``. Built proxies satisfy the protocol, so coercion **passes them
through** without re-running reflective dispatch; forwarded calls delegate to ``inner``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import AbstractContextManager, asynccontextmanager
from dataclasses import dataclass
from typing import Self, final

from pyhaul._types import Url
from pyhaul.transport.protocols import (
    AsyncTransportResponse,
    AsyncTransportSession,
    TransportResponse,
    TransportSession,
)
from pyhaul.transport.types import TransportHeaders, TransportRequestOptions

# ---------------------------------------------------------------------------
# Outer header hook applied after inner.prepare_headers(...)
# ---------------------------------------------------------------------------

type OuterPrepareHeaders = Callable[[TransportHeaders], TransportHeaders]

# ─── Pending: must call .around() before anything else ─────────────────────


class TransportSessionProxyPlanning:
    """First stage — ``around(inner)`` is required before shaping headers or ``build()``."""

    __slots__ = ()

    def around(self, inner: TransportSession) -> TransportSessionProxyRecipe:
        """Bind the delegate session that receives forwarded calls (reads: *proxy around this session*)."""
        return TransportSessionProxyRecipe(inner=inner)


class AsyncTransportSessionProxyPlanning:
    """Async first stage — ``around(inner)`` is required before shaping headers or ``build()``."""

    __slots__ = ()

    def around(self, inner: AsyncTransportSession) -> AsyncTransportSessionProxyRecipe:
        """Bind the delegate async session (reads: *proxy around this async session*)."""
        return AsyncTransportSessionProxyRecipe(inner=inner)


# ─── Recipe: optional header layering, then build ───────────────────────────


@dataclass(frozen=True, slots=True)
class TransportSessionProxyRecipe:
    """Session is bound — optionally layer header policy, then :meth:`build`."""

    inner: TransportSession
    outer_prepare_headers: OuterPrepareHeaders | None = None

    def preparing_headers_with(self, outer: OuterPrepareHeaders) -> Self:
        """Layer an outer transformation after ``inner.prepare_headers`` (*last call wins if chained*)."""
        return type(self)(
            inner=self.inner,
            outer_prepare_headers=outer,
        )

    def build(self) -> TransportSession:
        """Produce the forwarding :class:`~pyhaul.transport.protocols.TransportSession` instance."""
        return _ProxiedTransportSession(
            inner=self.inner,
            outer_prepare_headers=self.outer_prepare_headers,
        )


@dataclass(frozen=True, slots=True)
class AsyncTransportSessionProxyRecipe:
    """Async session is bound — optionally layer header policy, then :meth:`build`."""

    inner: AsyncTransportSession
    outer_prepare_headers: OuterPrepareHeaders | None = None

    def preparing_headers_with(self, outer: OuterPrepareHeaders) -> Self:
        """Layer an outer transformation after ``inner.prepare_headers`` (*last wins if chained*)."""
        return type(self)(
            inner=self.inner,
            outer_prepare_headers=outer,
        )

    def build(self) -> AsyncTransportSession:
        """Produce the forwarding async transport session."""
        return _ProxiedAsyncTransportSession(
            inner=self.inner,
            outer_prepare_headers=self.outer_prepare_headers,
        )


@final
class _ProxiedTransportSession:
    """Delegates ``stream_get`` faithfully; optionally layers ``prepare_headers`` after inner."""

    __slots__ = ("_inner", "_outer_prepare_headers")

    def __init__(
        self,
        *,
        inner: TransportSession,
        outer_prepare_headers: OuterPrepareHeaders | None,
    ) -> None:
        self._inner = inner
        self._outer_prepare_headers = outer_prepare_headers

    def prepare_headers(self, headers: TransportHeaders) -> TransportHeaders:
        prepared = self._inner.prepare_headers(headers)
        outer = self._outer_prepare_headers
        return prepared if outer is None else outer(prepared)

    def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> AbstractContextManager[TransportResponse]:
        return self._inner.stream_get(url, headers=headers, options=options)


@final
class _ProxiedAsyncTransportSession:
    __slots__ = ("_inner", "_outer_prepare_headers")

    def __init__(
        self,
        *,
        inner: AsyncTransportSession,
        outer_prepare_headers: OuterPrepareHeaders | None,
    ) -> None:
        self._inner = inner
        self._outer_prepare_headers = outer_prepare_headers

    def prepare_headers(self, headers: TransportHeaders) -> TransportHeaders:
        prepared = self._inner.prepare_headers(headers)
        outer = self._outer_prepare_headers
        return prepared if outer is None else outer(prepared)

    @asynccontextmanager
    async def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> AsyncIterator[AsyncTransportResponse]:
        async with self._inner.stream_get(url, headers=headers, options=options) as resp:
            yield resp


def transport_session_proxy() -> TransportSessionProxyPlanning:
    """Start a fluent chain for wrapping a synchronous :class:`TransportSession`."""
    return TransportSessionProxyPlanning()


def async_transport_session_proxy() -> AsyncTransportSessionProxyPlanning:
    """Start a fluent chain for wrapping an :class:`AsyncTransportSession`."""
    return AsyncTransportSessionProxyPlanning()


__all__ = [
    "AsyncTransportSessionProxyPlanning",
    "AsyncTransportSessionProxyRecipe",
    "TransportSessionProxyPlanning",
    "TransportSessionProxyRecipe",
    "async_transport_session_proxy",
    "transport_session_proxy",
]
