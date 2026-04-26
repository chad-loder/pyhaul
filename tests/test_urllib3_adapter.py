"""Unit tests for :mod:`pyhaul.transport.urllib3_adapter`."""

from __future__ import annotations

from collections.abc import Iterator
from typing import cast
from unittest.mock import MagicMock

import pytest
import urllib3

from pyhaul._types import Url
from pyhaul.transport.errors import TransportConnectionError, TransportHTTPError
from pyhaul.transport.protocols import TransportResponse, TransportSession
from pyhaul.transport.types import TransportRequestOptions
from pyhaul.transport.urllib3_adapter import (
    Urllib3Adapter,
    Urllib3TransportResponse,
    headers_from_urllib3_response,
    urllib3_transport,
)


class _FakeResponse:
    """Minimal stand-in for ``urllib3.HTTPResponse`` with streaming."""

    __slots__ = ("_chunks", "headers", "status")

    def __init__(
        self,
        *,
        status: int = 206,
        headers: dict[str, str] | None = None,
        chunks: tuple[bytes, ...] = (b"z",),
    ) -> None:
        self.status = status
        self.headers = urllib3.HTTPHeaderDict(headers or {"Content-Range": "bytes 0-0/1"})
        self._chunks = chunks

    def stream(self, amt: int, decode_content: bool = True) -> Iterator[bytes]:
        del amt, decode_content
        yield from self._chunks

    def release_conn(self) -> None:
        pass


def test_headers_from_urllib3_response_multi_value() -> None:
    resp = _FakeResponse()
    resp.headers = urllib3.HTTPHeaderDict()
    resp.headers.add("Set-Cookie", "a=1")
    resp.headers.add("Set-Cookie", "b=2")
    th = headers_from_urllib3_response(cast("urllib3.HTTPResponse", resp))
    assert th.get_all("set-cookie") == ("a=1", "b=2")


def test_urllib3_transport_response_protocol() -> None:
    inner = _FakeResponse(chunks=(b"a", b"bc"))
    tr: TransportResponse = Urllib3TransportResponse(cast("urllib3.HTTPResponse", inner))
    assert tr.status_code == 206
    assert list(tr.iter_raw_bytes(chunk_size=1024)) == [b"a", b"bc"]
    tr.raise_for_status()


def test_urllib3_transport_response_raise_for_status_on_error() -> None:
    inner = _FakeResponse(status=503, chunks=())
    tr = Urllib3TransportResponse(cast("urllib3.HTTPResponse", inner))
    with pytest.raises(TransportHTTPError) as ctx:
        tr.raise_for_status()
    assert ctx.value.status_code == 503


def test_urllib3_transport_response_incomplete_read_maps() -> None:
    class _BadStream(_FakeResponse):
        def stream(self, amt: int, decode_content: bool = True) -> Iterator[bytes]:
            del amt, decode_content
            raise urllib3.exceptions.IncompleteRead(
                partial=1024,
                expected=2048,
            )

    inner = _BadStream(chunks=())
    tr = Urllib3TransportResponse(cast("urllib3.HTTPResponse", inner))
    with pytest.raises(TransportConnectionError) as ctx:
        list(tr.iter_raw_bytes(chunk_size=1024))
    assert isinstance(ctx.value.__cause__, urllib3.exceptions.IncompleteRead)


def test_urllib3_adapter_stream_get() -> None:
    fake_resp = _FakeResponse()
    pool = MagicMock(spec=urllib3.PoolManager)
    pool.request.return_value = fake_resp

    adapter = Urllib3Adapter(pool)
    url = Url("https://example.test/file")

    opts = TransportRequestOptions(timeout=30.0, allow_redirects=True)
    with adapter.stream_get(url, headers={"Range": "bytes=0-1"}, options=opts) as resp:
        assert resp.status_code == 206
        assert list(resp.iter_raw_bytes(chunk_size=4096)) == [b"z"]

    pool.request.assert_called_once_with(
        "GET",
        str(url),
        headers={"Range": "bytes=0-1"},
        preload_content=False,
        redirect=True,
        timeout=30.0,
    )


def test_urllib3_adapter_stream_get_tuple_timeout() -> None:
    fake_resp = _FakeResponse()
    pool = MagicMock(spec=urllib3.PoolManager)
    pool.request.return_value = fake_resp

    adapter = Urllib3Adapter(pool)
    url = Url("https://example.test/file")

    opts = TransportRequestOptions(timeout=(5.0, 30.0))
    with adapter.stream_get(url, headers={}, options=opts) as resp:
        assert resp.status_code == 206

    call_kwargs = pool.request.call_args
    timeout_arg = call_kwargs.kwargs.get("timeout") or call_kwargs[1].get("timeout")
    assert isinstance(timeout_arg, urllib3.Timeout)


def test_urllib3_adapter_release_conn_called() -> None:
    """Connection is returned to the pool even if iteration raises."""
    fake_resp = MagicMock(spec=urllib3.HTTPResponse)
    fake_resp.status = 200
    fake_resp.headers = urllib3.HTTPHeaderDict()
    pool = MagicMock(spec=urllib3.PoolManager)
    pool.request.return_value = fake_resp

    adapter = Urllib3Adapter(pool)
    url = Url("https://example.test/file")

    with adapter.stream_get(url, headers={}) as _resp:
        pass

    fake_resp.release_conn.assert_called_once()


def test_urllib3_transport_factory() -> None:
    pool = urllib3.PoolManager()
    try:
        t: TransportSession = urllib3_transport(pool)
        assert isinstance(t, Urllib3Adapter)
    finally:
        pool.clear()
