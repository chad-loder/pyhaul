"""Live redirect matrix: every supported backend by redirect on/off.

Redirect policy is pinned via :class:`~tests.redirect_support.PinnedRedirectSyncTransport`
/ :class:`~tests.redirect_support.PinnedRedirectAsyncTransport` so tests exercise explicit
``TransportRequestOptions.allow_redirects`` for each adapter (see :mod:`tests.redirect_support`).
"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest

from pyhaul._types import CompleteHaul, UnexpectedStatusError, Url
from pyhaul.async_engine import haul_async
from pyhaul.engine import haul
from tests.live_backends import LIVE_BACKENDS, close_native
from tests.redirect_support import (
    LIVE_ASYNC_BACKENDS,
    PinnedRedirectAsyncTransport,
    async_native_session,
    build_sync_pinned_transport,
    make_async_inner_transport,
    redirect_server_context,
)


@pytest.fixture
def redirect_server_url() -> Generator[tuple[Url, bytes], None, None]:
    with redirect_server_context() as pair:
        yield pair


@pytest.mark.parametrize("allow_redirects", [True, False])
@pytest.mark.parametrize("backend", LIVE_BACKENDS)
def test_sync_redirect_follow_setting(
    tmp_path: Path,
    redirect_server_url: tuple[Url, bytes],
    backend: str,
    allow_redirects: bool,
) -> None:
    pytest.importorskip(backend)
    url, expected_body = redirect_server_url
    dest = tmp_path / f"sync_{backend}_{allow_redirects}.bin"

    native, transport = build_sync_pinned_transport(backend, allow_redirects=allow_redirects)
    try:
        if allow_redirects:
            result = haul(url, transport, dest=str(dest))
            assert isinstance(result, CompleteHaul)
            assert dest.read_bytes() == expected_body
        else:
            with pytest.raises(UnexpectedStatusError) as ctx:
                haul(url, transport, dest=str(dest))
            assert ctx.value.status_code == 302
            assert "were not followed" in ctx.value.reason
            assert "redirect" in ctx.value.reason.lower()
    finally:
        close_native(native)


@pytest.mark.parametrize("allow_redirects", [True, False])
@pytest.mark.parametrize("backend", LIVE_ASYNC_BACKENDS)
@pytest.mark.anyio
async def test_async_redirect_follow_setting(
    tmp_path: Path,
    redirect_server_url: tuple[Url, bytes],
    backend: str,
    allow_redirects: bool,
) -> None:
    pytest.importorskip(backend)
    url, expected_body = redirect_server_url
    dest = tmp_path / f"async_{backend}_{allow_redirects}.bin"

    async with async_native_session(backend) as native:
        inner = make_async_inner_transport(backend, native)
        transport = PinnedRedirectAsyncTransport(inner, allow_redirects=allow_redirects)

        if allow_redirects:
            result = await haul_async(url, transport, dest=str(dest))
            assert isinstance(result, CompleteHaul)
            assert dest.read_bytes() == expected_body
        else:
            with pytest.raises(UnexpectedStatusError) as ctx:
                await haul_async(url, transport, dest=str(dest))
            assert ctx.value.status_code == 302
            assert "were not followed" in ctx.value.reason
            assert "redirect" in ctx.value.reason.lower()
