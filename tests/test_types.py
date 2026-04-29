"""Tests for parse_url, CompleteHaul, PartialHaulError (exception), and HaulState."""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import format_datetime

import pytest

from pyhaul import (
    EMPTY_ETAG,
    CompleteHaul,
    ETag,
    HaulError,
    HaulState,
    PartialHaulError,
    UnexpectedStatusError,
    parse_url,
)
from pyhaul._types import EMPTY_URL, _retry_after_raw_to_seconds
from pyhaul.transport._headers import TransportHeaders


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


def test_download_complete_carries_success_only_fields() -> None:
    result = CompleteHaul(
        elapsed=1.0,
        sha256="abc",
        etag=ETag.from_canonical("v1"),
        content_type="application/octet-stream",
    )
    assert result.sha256 == "abc"
    assert result.etag == ETag.from_canonical("v1")


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
    assert state.reported_length is None

    state.is_complete = True
    state.bytes_read = 100
    state.valid_length = 500
    assert state.is_complete is True
    assert state.bytes_read == 100
    assert state.valid_length == 500


# ── UnexpectedStatusError ──────────────────────────────────────────


class TestUnexpectedStatusError:
    """Tests for the structured HTTP status exception."""

    def _make(
        self,
        status: int = 429,
        headers: TransportHeaders | None = None,
        reason: str = "",
    ) -> UnexpectedStatusError:
        h = TransportHeaders.from_pairs([("Retry-After", "120")]) if headers is None else headers
        return UnexpectedStatusError(status, h, reason)

    def test_is_haul_error(self) -> None:
        exc = self._make()
        assert isinstance(exc, HaulError)

    def test_status_code(self) -> None:
        exc = self._make(503)
        assert exc.status_code == 503

    def test_headers_attached(self) -> None:
        exc = self._make()
        assert exc.headers["retry-after"] == "120"

    def test_default_reason(self) -> None:
        exc = self._make(404)
        assert str(exc) == "unexpected HTTP 404"
        assert exc.reason == "unexpected HTTP 404"

    def test_custom_reason(self) -> None:
        exc = self._make(429, reason="rate limited")
        assert str(exc) == "rate limited"
        assert exc.reason == "rate limited"

    def test_is_transient_common_overload_and_upstream_codes(self) -> None:
        assert self._make(408).is_transient is True
        assert self._make(425).is_transient is True
        assert self._make(429).is_transient is True
        assert self._make(500).is_transient is True
        assert self._make(502).is_transient is True
        assert self._make(503).is_transient is True
        assert self._make(504).is_transient is True
        assert self._make(520).is_transient is True
        assert self._make(522).is_transient is True
        assert self._make(524).is_transient is True

    def test_is_not_transient_404(self) -> None:
        assert self._make(404).is_transient is False

    def test_is_server_error_5xx(self) -> None:
        assert self._make(500).is_server_error is True
        assert self._make(503).is_server_error is True
        assert self._make(599).is_server_error is True

    def test_is_not_server_error_4xx(self) -> None:
        assert self._make(429).is_server_error is False
        assert self._make(404).is_server_error is False

    def test_is_server_error_excludes_non_http_class(self) -> None:
        assert self._make(200).is_server_error is False

    def test_retry_after_present(self) -> None:
        exc = self._make()
        assert exc.retry_after == "120"
        assert exc.retry_after_seconds == 120.0

    def test_retry_after_absent(self) -> None:
        exc = self._make(headers=TransportHeaders())
        assert exc.retry_after is None
        assert exc.retry_after_seconds is None

    def test_retry_after_seconds_invalid_header(self) -> None:
        exc = self._make(
            headers=TransportHeaders.from_pairs([("Retry-After", "not-a-number-or-date")]),
        )
        assert exc.retry_after_seconds is None

    def test_catchable_as_haul_error(self) -> None:
        with pytest.raises(HaulError):
            raise self._make()

    def test_headers_immutable(self) -> None:
        exc = self._make()
        with pytest.raises(AttributeError):
            exc.headers.x = "nope"


class TestRetryAfterParsing:
    """Unit tests for :func:`pyhaul._types._retry_after_raw_to_seconds`."""

    def test_delay_seconds(self) -> None:
        assert _retry_after_raw_to_seconds("45", now=lambda: 0.0) == 45.0

    def test_whitespace_delay(self) -> None:
        assert _retry_after_raw_to_seconds("  90  ", now=lambda: 0.0) == 90.0

    def test_http_date_future(self) -> None:
        fixed_ts = 1_700_000_000.0
        when = datetime.fromtimestamp(fixed_ts + 37.0, tz=UTC)
        assert _retry_after_raw_to_seconds(format_datetime(when), now=lambda: fixed_ts) == pytest.approx(37.0)

    def test_http_date_past_clamps_zero(self) -> None:
        fixed_ts = 1_700_000_000.0
        when = datetime.fromtimestamp(fixed_ts - 60.0, tz=UTC)
        assert _retry_after_raw_to_seconds(format_datetime(when), now=lambda: fixed_ts) == 0.0

    def test_large_delay_integer_passes_through(self) -> None:
        assert _retry_after_raw_to_seconds("999999", now=lambda: 0.0) == 999999.0

    def test_large_http_date_passes_through(self) -> None:
        fixed_ts = 1_700_000_000.0
        delta_sec = 999_999.0
        when = datetime.fromtimestamp(fixed_ts + delta_sec, tz=UTC)
        assert _retry_after_raw_to_seconds(format_datetime(when), now=lambda: fixed_ts) == pytest.approx(
            delta_sec,
        )

    def test_none_absent(self) -> None:
        assert _retry_after_raw_to_seconds(None, now=lambda: 0.0) is None

    def test_empty_string(self) -> None:
        assert _retry_after_raw_to_seconds("   ", now=lambda: 0.0) is None
