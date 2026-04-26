"""Tests for parse_url / parse_etag branding factories, CompleteHaul,
PartialHaulError (exception), and HaulState (mutable bag)."""

from __future__ import annotations

import pytest

from pyhaul import (
    CompleteHaul,
    ETag,
    HaulState,
    PartialHaulError,
    Url,
    parse_etag,
    parse_url,
)
from pyhaul._types import EMPTY_ETAG, EMPTY_URL


@pytest.mark.parametrize(
    "raw",
    [
        "http://example.com/",
        "https://example.com/path/to/file.zip?token=abc",
        "http://user:pass@host:8080/",
        "https://[::1]:8443/ipv6",
    ],
)
def test_parse_url_accepts_valid_http_urls(raw: str) -> None:
    parsed = parse_url(raw)
    assert parsed == raw
    assert isinstance(parsed, str)


@pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
def test_parse_url_empty_returns_sentinel(raw: str) -> None:
    assert parse_url(raw) == EMPTY_URL
    assert parse_url(raw) == ""


@pytest.mark.parametrize(
    "raw",
    [
        "ftp://example.com/",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "gopher://example.com",
    ],
)
def test_parse_url_rejects_unsupported_scheme(raw: str) -> None:
    with pytest.raises(ValueError, match="scheme"):
        parse_url(raw)


@pytest.mark.parametrize("raw", ["http://", "https:///path", "http:/missing-host"])
def test_parse_url_rejects_missing_host(raw: str) -> None:
    with pytest.raises(ValueError, match=r"host|scheme"):
        parse_url(raw)


def test_parse_etag_strong_and_weak_are_preserved_verbatim() -> None:
    assert parse_etag('"abc123"') == '"abc123"'
    assert parse_etag('W/"weak-hash"') == 'W/"weak-hash"'


def test_parse_etag_strips_whitespace() -> None:
    assert parse_etag('  "abc"  ') == '"abc"'


@pytest.mark.parametrize("raw", ["", "   ", "\t"])
def test_parse_etag_empty_returns_empty_sentinel(raw: str) -> None:
    result = parse_etag(raw)
    assert result == EMPTY_ETAG
    assert result == ""


@pytest.mark.parametrize(
    "raw",
    [
        "not-quoted",
        '"unterminated',
        'unopened"',
        '"',
        "W/unquoted",
    ],
)
def test_parse_etag_rejects_malformed_as_empty(raw: str) -> None:
    """Malformed ETags become EMPTY_ETAG rather than raise — we refuse to
    echo garbage back in If-Range, but don't want to blow up the download."""
    assert parse_etag(raw) == EMPTY_ETAG


def test_parse_etag_type_error_on_non_string() -> None:
    with pytest.raises(TypeError):
        parse_etag(None)
    with pytest.raises(TypeError):
        parse_etag(b'"bytes"')


def test_newtype_identity_at_runtime() -> None:
    """NewType is a no-op at runtime — the brand only lives in mypy's view."""
    url = parse_url("http://example.com/")
    etag = parse_etag('"abc"')
    assert type(url) is str  # not a subclass
    assert type(etag) is str
    assert Url("x") == "x"
    assert ETag("y") == "y"


def test_download_complete_carries_success_only_fields() -> None:
    result = CompleteHaul(
        elapsed=1.0,
        sha256="abc",
        etag=ETag('"v1"'),
        content_type="application/octet-stream",
    )
    assert result.sha256 == "abc"
    assert result.etag == '"v1"'


def test_download_partial_is_exception() -> None:
    """PartialHaulError is a lean exception — only carries reason."""
    exc = PartialHaulError("network error")
    assert isinstance(exc, Exception)
    assert exc.reason == "network error"
    assert str(exc) == "network error"
    for missing in ("sha256", "etag", "content_type", "bytes_read", "valid_length"):
        assert not hasattr(exc, missing)


def test_download_partial_default_reason() -> None:
    exc = PartialHaulError()
    assert exc.reason == ""


def test_download_complete_is_immutable() -> None:
    """Frozen dataclasses prevent accidental mutation of terminal state."""
    complete = CompleteHaul(
        elapsed=0.0,
        sha256="",
        etag=EMPTY_ETAG,
        content_type="",
    )
    with pytest.raises(Exception, match=r"frozen|cannot assign"):
        complete.sha256 = "xxx"  # type: ignore[misc]


def test_download_state_is_mutable() -> None:
    state = HaulState()
    assert state.is_complete is False
    assert state.bytes_read == 0
    assert state.valid_length == 0

    state.is_complete = True
    state.bytes_read = 100
    state.valid_length = 500
    assert state.is_complete is True
    assert state.bytes_read == 100
    assert state.valid_length == 500
