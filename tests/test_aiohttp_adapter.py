"""Unit tests for :mod:`pyhaul.transport.aiohttp_adapter`."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import MagicMock

import pytest

pytest.importorskip("aiohttp")
import aiohttp

from pyhaul._types import Url
from pyhaul.transport.aiohttp_adapter import (
    AiohttpTransportResponse,
    AsyncAiohttpAdapter,
    async_aiohttp_transport,
    headers_from_aiohttp_response,
)
from pyhaul.transport.errors import TransportHTTPError
from pyhaul.transport.protocols import AsyncTransportSession
from pyhaul.transport.types import TransportRequestOptions


class _FakeContent:
    """Simulate aiohttp's StreamReader.iter_chunked."""

    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks

    def iter_chunked(self, chunk_size: int) -> AsyncIterator[bytes]:
        del chunk_size
        return self._aiter()

    async def _aiter(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _FakeResponse:
    """Minimal aiohttp.ClientResponse stand-in for unit tests."""

    def __init__(self, *, status: int = 206, chunks: tuple[bytes, ...] = (b"z",)) -> None:
        self.status = status
        from multidict import CIMultiDict, CIMultiDictProxy

        self.headers = CIMultiDictProxy(CIMultiDict([("Content-Range", "bytes 0-0/1")]))
        self.content = _FakeContent(chunks)

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=self.status,
                message="error",
            )


def test_headers_from_aiohttp_response_multi_value() -> None:
    from multidict import CIMultiDict, CIMultiDictProxy

    r = MagicMock(spec=aiohttp.ClientResponse)
    r.headers = CIMultiDictProxy(CIMultiDict([("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")]))
    th = headers_from_aiohttp_response(r)
    assert th.get_all("set-cookie") == ("a=1", "b=2")


@pytest.mark.anyio
async def test_aiohttp_transport_response_protocol() -> None:
    inner = _FakeResponse(chunks=(b"a", b"bc"))
    tr = AiohttpTransportResponse(cast("aiohttp.ClientResponse", inner))
    assert tr.status_code == 206
    chunks = [c async for c in tr.aiter_raw_bytes(chunk_size=1024)]
    assert chunks == [b"a", b"bc"]
    tr.raise_for_status()


@pytest.mark.anyio
async def test_aiohttp_transport_response_http_error_maps() -> None:
    inner = _FakeResponse(status=500, chunks=())
    tr = AiohttpTransportResponse(cast("aiohttp.ClientResponse", inner))
    with pytest.raises(TransportHTTPError) as ctx:
        tr.raise_for_status()
    assert ctx.value.status_code == 500


@pytest.mark.anyio
async def test_aiohttp_adapter_stream_get() -> None:
    fake_resp = _FakeResponse()
    session = MagicMock(spec=aiohttp.ClientSession)
    session.get.return_value = fake_resp

    adapter = AsyncAiohttpAdapter(session)
    url = Url("https://example.test/file")

    opts = TransportRequestOptions(timeout=30.0, verify=False, allow_redirects=True)
    async with adapter.stream_get(url, headers={"Range": "bytes=0-1"}, options=opts) as resp:
        assert resp.status_code == 206
        chunks = [c async for c in resp.aiter_raw_bytes(chunk_size=4096)]
        assert chunks == [b"z"]

    session.get.assert_called_once_with(
        str(url),
        headers={"Range": "bytes=0-1"},
        auto_decompress=False,
        raise_for_status=False,
        timeout=aiohttp.ClientTimeout(total=30.0),
        allow_redirects=True,
        ssl=False,
    )


@pytest.mark.anyio
async def test_aiohttp_adapter_timeout_tuple() -> None:
    fake_resp = _FakeResponse()
    session = MagicMock(spec=aiohttp.ClientSession)
    session.get.return_value = fake_resp

    adapter = AsyncAiohttpAdapter(session)
    url = Url("https://example.test/file")
    opts = TransportRequestOptions(timeout=(5.0, 30.0))
    async with adapter.stream_get(url, headers={}, options=opts) as resp:
        assert resp.status_code == 206

    call_kwargs = session.get.call_args.kwargs
    assert call_kwargs["timeout"] == aiohttp.ClientTimeout(sock_connect=5.0, sock_read=30.0)


def test_aiohttp_transport_factory() -> None:
    session = MagicMock(spec=aiohttp.ClientSession)
    t: AsyncTransportSession = async_aiohttp_transport(session)
    assert isinstance(t, AsyncAiohttpAdapter)


@pytest.mark.anyio
async def test_aiohttp_dispatch_coercion() -> None:
    from pyhaul._session_dispatch import coerce_async_session

    session = MagicMock(spec=aiohttp.ClientSession)
    adapter = coerce_async_session(session)
    assert type(adapter).__name__ == "AsyncAiohttpAdapter"


class _AutoRaiseResponse(_FakeResponse):
    """Simulates aiohttp's raise_for_status=True behavior on the session.

    When a session is created with ``raise_for_status=True``, aiohttp calls
    ``response.raise_for_status()`` automatically inside ``__aenter__`` —
    *unless* the per-request ``raise_for_status=False`` override is set.
    This mock faithfully reproduces that precedence.
    """

    def __init__(self, *, status: int = 206, chunks: tuple[bytes, ...] = (b"z",), auto_raise: bool = True) -> None:
        super().__init__(status=status, chunks=chunks)
        self._auto_raise = auto_raise

    async def __aenter__(self) -> _AutoRaiseResponse:
        if self._auto_raise:
            self.raise_for_status()
        return self


def _make_auto_raise_session(status: int) -> MagicMock:
    """Build a mock session that honors the per-request raise_for_status kwarg.

    When the adapter passes ``raise_for_status=False``, the returned response
    skips the auto-raise — exactly like real aiohttp.
    """
    session = MagicMock(spec=aiohttp.ClientSession)

    def _get(*args: object, **kwargs: object) -> _AutoRaiseResponse:
        per_request = bool(kwargs.get("raise_for_status", True))
        return _AutoRaiseResponse(status=status, auto_raise=per_request)

    session.get.side_effect = _get
    return session


@pytest.mark.anyio
async def test_aiohttp_raise_for_status_true_must_not_block_416() -> None:
    """Adapter must suppress session-level raise_for_status for 416.

    416 (Range Not Satisfiable) is part of pyhaul's normal resume protocol —
    the engine reads the status code and decides what to do.  If the aiohttp
    session was created with ``raise_for_status=True``, the auto-raise fires
    inside ``__aenter__`` *before* the engine ever sees the response, silently
    breaking resume.

    The adapter must override this per-request so the engine always gets the
    raw response.
    """
    session = _make_auto_raise_session(status=416)
    adapter = AsyncAiohttpAdapter(session)
    url = Url("https://example.test/file")

    async with adapter.stream_get(url, headers={"Range": "bytes=0-"}) as resp:
        assert resp.status_code == 416
