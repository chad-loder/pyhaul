"""Transport session proxy fluent builder."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager

from pyhaul._types import Url, parse_url
from pyhaul.transport.protocols import (
    AsyncTransportSession,
    TransportResponse,
    TransportSession,
)
from pyhaul.transport.proxy_transport_session import (
    AsyncTransportSessionProxyPlanning,
    TransportSessionProxyPlanning,
    async_transport_session_proxy,
    transport_session_proxy,
)
from pyhaul.transport.types import TransportHeaders, TransportRequestOptions


class StubResponse(TransportResponse):
    def __init__(self) -> None:
        self._hdrs = TransportHeaders.build()

    @property
    def status_code(self) -> int:
        return 200

    @property
    def headers(self) -> TransportHeaders:
        return self._hdrs

    def raise_for_status(self) -> None:
        pass

    def iter_raw_bytes(self, *, chunk_size: int) -> Iterator[bytes]:
        yield b""


class StubTransport(TransportSession):
    """Minimal structural session for proxy tests."""

    def __init__(self) -> None:
        self.prepared_sequence: list[TransportHeaders] = []
        self.stream_get_calls = 0
        self.stream_head_calls = 0

    def prepare_headers(self, headers: TransportHeaders) -> TransportHeaders:
        self.prepared_sequence.append(headers)
        return headers.with_added("X-Inner", "1")

    @contextmanager
    def stream_head(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> Iterator[StubResponse]:
        self.stream_head_calls += 1
        yield StubResponse()

    @contextmanager
    def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> Iterator[StubResponse]:
        self.stream_get_calls += 1
        yield StubResponse()


def test_transport_session_proxy_chain_order() -> None:
    inner = StubTransport()
    calls: list[str] = []

    def outer(h: TransportHeaders) -> TransportHeaders:
        calls.append("outer")
        assert h["X-Inner"] == "1"
        return h.with_added("X-Outer", "2")

    proxy = transport_session_proxy().around(inner).preparing_headers_with(outer).build()
    th = TransportHeaders.build(Accept="*/*")

    got = proxy.prepare_headers(th)

    assert got["Accept"] == "*/*"
    assert got["X-Inner"] == "1"
    assert got["X-Outer"] == "2"
    assert calls == ["outer"]
    assert len(inner.prepared_sequence) == 1


def test_build_without_outer_prepare_forwards_prepare_headers_only() -> None:
    inner = StubTransport()
    proxy = transport_session_proxy().around(inner).build()
    got = proxy.prepare_headers(TransportHeaders.build())
    assert got["X-Inner"] == "1"


def test_stream_get_forwarded_but_not_called_during_prepare_only() -> None:
    inner = StubTransport()
    proxy = transport_session_proxy().around(inner).build()
    proxy.prepare_headers(TransportHeaders.build())

    assert inner.stream_get_calls == 0


def test_transport_session_proxy_returns_planning_stage() -> None:
    p = transport_session_proxy()
    assert isinstance(p, TransportSessionProxyPlanning)


def test_planning_stage_has_no_build() -> None:
    pending = transport_session_proxy()
    assert not hasattr(pending, "build")


def test_recipe_stage_has_no_around_second_inner_unrepresentable_by_types() -> None:
    inner = StubTransport()
    recipe = transport_session_proxy().around(inner)

    assert not hasattr(recipe, "around")


def test_outer_last_call_wins() -> None:
    inner = StubTransport()

    proxy = (
        transport_session_proxy()
        .around(inner)
        .preparing_headers_with(lambda h: h.with_added("X", "a"))
        .preparing_headers_with(lambda h: h.with_added("X", "b"))
        .build()
    )
    built = proxy.prepare_headers(TransportHeaders.build())

    assert built["X"] == "b"


class _MiniAsyncResp:
    def __init__(self) -> None:
        self._h = TransportHeaders.build()

    @property
    def status_code(self) -> int:
        return 200

    @property
    def headers(self) -> TransportHeaders:
        return self._h

    def raise_for_status(self) -> None:
        pass

    async def aiter_raw_bytes(self, *, chunk_size: int) -> AsyncIterator[bytes]:
        if False:
            yield b""


class AsyncStubTransport(AsyncTransportSession):
    def __init__(self) -> None:
        self.prepared: list[TransportHeaders] = []
        self.stream_head_calls = 0

    def prepare_headers(self, headers: TransportHeaders) -> TransportHeaders:
        self.prepared.append(headers)
        return headers.with_added("Y-Inner", "1")

    @asynccontextmanager
    async def stream_head(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> AsyncIterator[_MiniAsyncResp]:
        self.stream_head_calls += 1
        yield _MiniAsyncResp()

    @asynccontextmanager
    async def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> AsyncIterator[_MiniAsyncResp]:
        yield _MiniAsyncResp()


def test_async_transport_session_proxy_prepare_order() -> None:
    inner = AsyncStubTransport()

    def outer(h: TransportHeaders) -> TransportHeaders:
        return h.with_added("Y-Outer", "2")

    proxy = async_transport_session_proxy().around(inner).preparing_headers_with(outer).build()
    got = proxy.prepare_headers(TransportHeaders.build())

    assert got["Y-Inner"] == "1"
    assert got["Y-Outer"] == "2"


def test_async_planning_is_distinct_type() -> None:
    p = async_transport_session_proxy()
    assert isinstance(p, AsyncTransportSessionProxyPlanning)


def test_stream_get_forwards_to_inner() -> None:
    inner = StubTransport()
    proxy = transport_session_proxy().around(inner).build()
    u = parse_url("http://example.test/file")

    with proxy.stream_get(u, headers={"Accept": "*/*"}) as resp:
        assert resp.status_code == 200

    assert inner.stream_get_calls == 1


def test_stream_head_forwards_to_inner() -> None:
    inner = StubTransport()
    proxy = transport_session_proxy().around(inner).build()
    u = parse_url("http://example.test/file")

    with proxy.stream_head(u, headers={"Accept": "*/*"}) as resp:
        assert resp.status_code == 200

    assert inner.stream_head_calls == 1
