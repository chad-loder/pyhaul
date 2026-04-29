"""Tests for ``probe`` / ``probe_async`` metadata discovery."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager

import pytest

from pyhaul._probe_common import run_probe_async, run_probe_sync
from pyhaul._types import UnexpectedStatusError, Url
from pyhaul.transport.protocols import (
    AsyncTransportResponse,
    AsyncTransportSession,
    TransportResponse,
    TransportSession,
)
from pyhaul.transport.types import TransportHeaders, TransportRequestOptions

_URL = "https://example.com/resource.bin"


class _Resp:
    """Minimal transport response with optional body for ranged GET."""

    __slots__ = ("_body", "headers", "status_code")

    def __init__(self, status_code: int, headers: dict[str, str], body: bytes = b"") -> None:
        self.status_code = status_code
        self.headers = TransportHeaders.from_mapping(headers)
        self._body = body

    def raise_for_status(self) -> None:
        pass

    def iter_raw_bytes(self, *, chunk_size: int) -> Iterator[bytes]:
        del chunk_size
        if self._body:
            yield self._body

    async def aiter_raw_bytes(self, *, chunk_size: int) -> AsyncIterator[bytes]:
        del chunk_size
        if self._body:
            yield self._body


class FakeSyncTransport(TransportSession):
    def __init__(self, head: _Resp, get: _Resp) -> None:
        self._head = head
        self._get = get
        self.head_calls = 0
        self.get_calls = 0

    def prepare_headers(self, headers: TransportHeaders) -> TransportHeaders:
        return headers

    @contextmanager
    def stream_head(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> Iterator[TransportResponse]:
        del url, headers, options
        self.head_calls += 1
        yield self._head

    @contextmanager
    def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> Iterator[TransportResponse]:
        del url, headers, options
        self.get_calls += 1
        yield self._get


class FakeAsyncTransport(AsyncTransportSession):
    def __init__(self, head: _Resp, get: _Resp) -> None:
        self._head = head
        self._get = get
        self.head_calls = 0
        self.get_calls = 0

    def prepare_headers(self, headers: TransportHeaders) -> TransportHeaders:
        return headers

    @asynccontextmanager
    async def stream_head(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> AsyncIterator[AsyncTransportResponse]:
        del url, headers, options
        self.head_calls += 1
        yield self._head

    @asynccontextmanager
    async def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> AsyncIterator[AsyncTransportResponse]:
        del url, headers, options
        self.get_calls += 1
        yield self._get


def test_probe_sync_head_complete_skips_get() -> None:
    head = _Resp(
        200,
        {
            "Accept-Ranges": "bytes",
            "ETag": '"abc"',
            "Content-Disposition": 'attachment; filename="a.bin"',
            "Content-Length": "100",
        },
    )
    bad = _Resp(500, {})
    t = FakeSyncTransport(head, bad)
    r = run_probe_sync(t, _URL, headers=None, options=None)
    assert t.head_calls == 1
    assert t.get_calls == 0
    assert not r.ranged_get_used
    assert r.total_length == 100
    assert r.supports_concurrent_byte_ranges
    assert r.status_code == 200


def test_probe_sync_range_followup_fills_total() -> None:
    head = _Resp(200, {"Content-Length": "50"})
    get = _Resp(
        206,
        {
            "Content-Range": "bytes 0-0/999",
            "Content-Length": "1",
            "Accept-Ranges": "bytes",
            "ETag": '"z"',
        },
        body=b"x",
    )
    t = FakeSyncTransport(head, get)
    r = run_probe_sync(t, _URL, headers=None, options=None)
    assert t.get_calls == 1
    assert r.ranged_get_used
    assert r.total_length == 999
    assert r.status_code == 206


def test_probe_sync_unexpected_status_raises() -> None:
    head = _Resp(404, {})
    get = _Resp(404, {})
    t = FakeSyncTransport(head, get)
    with pytest.raises(UnexpectedStatusError) as ei:
        run_probe_sync(t, _URL, headers=None, options=None)
    assert ei.value.status_code == 404


@pytest.mark.anyio
async def test_probe_async_range_followup() -> None:
    head = _Resp(200, {})
    get = _Resp(
        206,
        {"Content-Range": "bytes 0-0/42", "Content-Length": "1", "Accept-Ranges": "bytes"},
        body=b"!",
    )
    t = FakeAsyncTransport(head, get)
    r = await run_probe_async(t, _URL, headers=None, options=None)
    assert t.head_calls == 1
    assert t.get_calls == 1
    assert r.total_length == 42


def test_public_exports_probe_result() -> None:
    from pyhaul import ProbeResult, probe, probe_async

    assert ProbeResult.__name__ == "ProbeResult"
    assert callable(probe)
    assert callable(probe_async)
