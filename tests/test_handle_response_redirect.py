"""Tests for redirect-specific messaging in :func:`pyhaul._engine_common.handle_response`."""

from __future__ import annotations

from pathlib import Path

import pytest

from pyhaul._engine_common import PrepareHaul, handle_response
from pyhaul._types import EMPTY_ETAG, HaulState, UnexpectedStatusError, Url
from pyhaul.transport._headers import TransportHeaders


def _minimal_prep() -> PrepareHaul:
    return PrepareHaul(
        dest_path=Path("/tmp/pyhaul-test.bin"),
        parsed_url=Url("http://example.com/file.bin"),
        part_path=Path("/tmp/pyhaul-test.bin.part"),
        ctrl_path=Path("/tmp/pyhaul-test.bin.part.ctrl"),
        start=0,
        cursor=0,
        stored_etag=EMPTY_ETAG,
        hashes=[],
        tail_hash=None,
        block_size=8 * 1024 * 1024,
        request_byte=0,
        merged_headers=TransportHeaders.from_pairs([]),
        t0=0.0,
    )


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_not_followed_includes_location_and_plain_message(status: int) -> None:
    headers = TransportHeaders.from_pairs([("Location", "https://cdn.example/other.bin"), ("Server", "test")])
    with pytest.raises(UnexpectedStatusError) as ctx:
        handle_response(status, headers, _minimal_prep(), HaulState())
    exc = ctx.value
    assert exc.status_code == status
    assert "were not followed" in exc.reason
    assert "https://cdn.example/other.bin" in exc.reason
    assert "redirect" in exc.reason.lower()


def test_redirect_without_location_message() -> None:
    headers = TransportHeaders.from_pairs([])
    with pytest.raises(UnexpectedStatusError) as ctx:
        handle_response(302, headers, _minimal_prep(), HaulState())
    assert ctx.value.status_code == 302
    assert "were not followed" in ctx.value.reason
    assert "another URL" in ctx.value.reason


def test_non_redirect_unexpected_status_generic_reason() -> None:
    headers = TransportHeaders.from_pairs([])
    with pytest.raises(UnexpectedStatusError) as ctx:
        handle_response(418, headers, _minimal_prep(), HaulState())
    assert ctx.value.reason == "unexpected HTTP 418"
