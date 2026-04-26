"""Unit tests for :mod:`pyhaul.transport.httpx_adapter`."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast
from unittest.mock import MagicMock

import pytest

pytest.importorskip("httpx")
import httpx

from pyhaul._types import Url
from pyhaul.transport.errors import TransportConnectionError
from pyhaul.transport.httpx_adapter import (
    HttpxAdapter,
    HttpxTransportResponse,
    headers_from_httpx_response,
    httpx_transport,
)
from pyhaul.transport.protocols import TransportResponse, TransportSession
from pyhaul.transport.types import TransportRequestOptions


class _StreamCM:
    __slots__ = ("_chunks", "headers", "status_code")

    def __init__(self, *, status_code: int = 206, chunks: tuple[bytes, ...] = (b"z",)) -> None:
        self.status_code = status_code
        self.headers = httpx.Headers([("Content-Range", "bytes 0-0/1")])
        self._chunks = chunks

    def __enter__(self) -> _StreamCM:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("HTTP error")

    def iter_raw(self, chunk_size: int | None = None) -> Iterator[bytes]:
        del chunk_size
        yield from self._chunks


def test_headers_from_httpx_response_multi_value() -> None:
    h = httpx.Headers([("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")])
    r = MagicMock(spec=httpx.Response)
    r.headers = h
    th = headers_from_httpx_response(r)
    assert th.get_all("set-cookie") == ("a=1", "b=2")


def test_httpx_transport_response_protocol() -> None:
    inner = _StreamCM(chunks=(b"a", b"bc"))
    tr: TransportResponse = HttpxTransportResponse(cast("httpx.Response", inner))
    assert tr.status_code == 206
    assert list(tr.iter_raw_bytes(chunk_size=1024)) == [b"a", b"bc"]
    tr.raise_for_status()


def test_httpx_transport_response_remote_protocol_error_maps() -> None:
    class _BadStream(_StreamCM):
        def iter_raw(self, chunk_size: int | None = None) -> Iterator[bytes]:
            del chunk_size
            raise httpx.RemoteProtocolError("peer closed early")

    inner = _BadStream(chunks=())
    tr = HttpxTransportResponse(cast("httpx.Response", inner))
    with pytest.raises(TransportConnectionError) as ctx:
        list(tr.iter_raw_bytes(chunk_size=1024))
    assert isinstance(ctx.value.__cause__, httpx.RemoteProtocolError)


def test_httpx_adapter_stream_get() -> None:
    stream_r = _StreamCM()
    client = MagicMock(spec=httpx.Client)
    client.stream.return_value = stream_r

    adapter = HttpxAdapter(client)
    url = Url("https://example.test/file")

    opts = TransportRequestOptions(timeout=30.0, verify=False, allow_redirects=True)
    with adapter.stream_get(url, headers={"Range": "bytes=0-1"}, options=opts) as resp:
        assert resp.status_code == 206
        assert list(resp.iter_raw_bytes(chunk_size=4096)) == [b"z"]

    client.stream.assert_called_once_with(
        "GET",
        str(url),
        headers={"Range": "bytes=0-1"},
        timeout=30.0,
        follow_redirects=True,
    )


def test_httpx_transport_factory() -> None:
    client = httpx.Client()
    try:
        t: TransportSession = httpx_transport(client)
        assert isinstance(t, HttpxAdapter)
    finally:
        client.close()
