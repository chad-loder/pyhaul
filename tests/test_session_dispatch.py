"""Tests for pyhaul._session_dispatch — adapter factory chain."""

from __future__ import annotations

import pytest

import pyhaul._session_dispatch as session_dispatch
from pyhaul._session_dispatch import (
    coerce_async_session,
    coerce_sync_session,
    register_async_adapter,
    register_sync_adapter,
)
from pyhaul.transport.protocols import AsyncTransportSession, TransportSession

# ---------------------------------------------------------------------------
# Sync dispatch
# ---------------------------------------------------------------------------


class TestCoerceSyncSession:
    def test_niquests_session(self) -> None:
        niquests = pytest.importorskip("niquests")
        adapter = coerce_sync_session(niquests.Session())
        assert type(adapter).__name__ == "NiquestsAdapter"

    def test_requests_session(self) -> None:
        requests = pytest.importorskip("requests")
        adapter = coerce_sync_session(requests.Session())
        assert type(adapter).__name__ == "RequestsAdapter"

    def test_httpx_client(self) -> None:
        httpx = pytest.importorskip("httpx")
        client = httpx.Client()
        try:
            adapter = coerce_sync_session(client)
            assert type(adapter).__name__ == "HttpxAdapter"
        finally:
            client.close()

    def test_urllib3_poolmanager(self) -> None:
        urllib3 = pytest.importorskip("urllib3")
        adapter = coerce_sync_session(urllib3.PoolManager())
        assert type(adapter).__name__ == "Urllib3Adapter"

    def test_urllib3_proxymanager(self) -> None:
        urllib3 = pytest.importorskip("urllib3")
        adapter = coerce_sync_session(urllib3.ProxyManager("http://localhost:8080"))
        assert type(adapter).__name__ == "Urllib3Adapter"

    def test_already_wrapped_passes_through(self) -> None:
        niquests = pytest.importorskip("niquests")
        adapter = coerce_sync_session(niquests.Session())
        rewrapped = coerce_sync_session(adapter)
        assert adapter is rewrapped

    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(TypeError, match="No sync adapter"):
            coerce_sync_session(object())

    def test_string_raises(self) -> None:
        with pytest.raises(TypeError, match="No sync adapter"):
            coerce_sync_session("https://example.com")


# ---------------------------------------------------------------------------
# Async dispatch
# ---------------------------------------------------------------------------


class TestCoerceAsyncSession:
    def test_httpx_async_client(self) -> None:
        httpx = pytest.importorskip("httpx")
        client = httpx.AsyncClient()
        adapter = coerce_async_session(client)
        assert type(adapter).__name__ == "AsyncHttpxAdapter"

    def test_niquests_async_session(self) -> None:
        niquests = pytest.importorskip("niquests")
        if not hasattr(niquests, "AsyncSession"):
            pytest.skip("niquests.AsyncSession not available")
        adapter = coerce_async_session(niquests.AsyncSession())
        assert type(adapter).__name__ == "AsyncNiquestsAdapter"

    def test_already_wrapped_passes_through(self) -> None:
        httpx = pytest.importorskip("httpx")
        adapter = coerce_async_session(httpx.AsyncClient())
        rewrapped = coerce_async_session(adapter)
        assert adapter is rewrapped

    def test_unsupported_type_raises(self) -> None:
        with pytest.raises(TypeError, match="No async adapter"):
            coerce_async_session(object())


# ---------------------------------------------------------------------------
# Registration API
# ---------------------------------------------------------------------------


class TestRegisterSyncAdapter:
    def test_custom_factory_called(self) -> None:
        sentinel = object()
        calls: list[object] = []

        def fake_factory(obj: object) -> TransportSession | None:
            calls.append(obj)
            return None

        saved = session_dispatch._sync_factories
        register_sync_adapter(fake_factory)
        try:
            with pytest.raises(TypeError):
                coerce_sync_session(sentinel)
            assert sentinel in calls
        finally:
            session_dispatch._sync_factories = saved
            assert len(session_dispatch._sync_factories) == len(saved)

    def test_custom_factory_wins(self) -> None:
        niquests = pytest.importorskip("niquests")
        from pyhaul.transport.niquests_adapter import NiquestsAdapter

        custom_adapter = NiquestsAdapter(niquests.Session())

        def always_match(obj: object) -> TransportSession | None:
            return custom_adapter

        saved = session_dispatch._sync_factories
        register_sync_adapter(always_match)
        try:
            result = coerce_sync_session(object())
            assert result is custom_adapter
        finally:
            session_dispatch._sync_factories = saved


class TestRegisterAsyncAdapter:
    def test_custom_factory_called(self) -> None:
        calls: list[object] = []

        def fake_factory(obj: object) -> AsyncTransportSession | None:
            calls.append(obj)
            return None

        saved = session_dispatch._async_factories
        register_async_adapter(fake_factory)
        try:
            with pytest.raises(TypeError):
                coerce_async_session(object())
            assert len(calls) == 1
        finally:
            session_dispatch._async_factories = saved
            assert len(session_dispatch._async_factories) == len(saved)
