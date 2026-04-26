"""Tests for CDN-safe headers and ETag churn validation."""

from __future__ import annotations

from pyhaul._types import EMPTY_ETAG, ServerMeta, parse_etag
from pyhaul.headers import DEFAULT_HEADERS, is_file_changed


def test_default_headers_has_no_transform() -> None:
    assert "no-transform" in DEFAULT_HEADERS["Cache-Control"]


def test_default_headers_identity_encoding() -> None:
    assert DEFAULT_HEADERS["Accept-Encoding"] == "identity"


def test_default_headers_no_cache_absent() -> None:
    assert "no-cache" not in DEFAULT_HEADERS.get("Cache-Control", "")
    assert "Pragma" not in DEFAULT_HEADERS


# -----------------------------------------------------------------------
# is_file_changed
# -----------------------------------------------------------------------


def test_same_etag_means_unchanged() -> None:
    old = ServerMeta(etag=parse_etag('"abc"'), total_length=100)
    new = ServerMeta(etag=parse_etag('"abc"'), total_length=100)
    assert not is_file_changed(old, new)


def test_different_etag_same_length_and_mtime_means_unchanged() -> None:
    old = ServerMeta(etag=parse_etag('"abc"'), total_length=100, last_modified="Wed, 01 Jan 2025 00:00:00 GMT")
    new = ServerMeta(etag=parse_etag('"xyz"'), total_length=100, last_modified="Wed, 01 Jan 2025 00:00:00 GMT")
    assert not is_file_changed(old, new)


def test_different_etag_different_length_means_changed() -> None:
    old = ServerMeta(etag=parse_etag('"abc"'), total_length=100)
    new = ServerMeta(etag=parse_etag('"xyz"'), total_length=200)
    assert is_file_changed(old, new)


def test_different_etag_same_length_no_mtime_means_changed() -> None:
    old = ServerMeta(etag=parse_etag('"abc"'), total_length=100)
    new = ServerMeta(etag=parse_etag('"xyz"'), total_length=100)
    assert is_file_changed(old, new)


def test_both_empty_etags_same_length_no_mtime_means_changed() -> None:
    old = ServerMeta(etag=EMPTY_ETAG, total_length=100)
    new = ServerMeta(etag=EMPTY_ETAG, total_length=100)
    assert is_file_changed(old, new)


def test_one_empty_etag_falls_through_to_length_mtime() -> None:
    old = ServerMeta(etag=parse_etag('"abc"'), total_length=100, last_modified="x")
    new = ServerMeta(etag=EMPTY_ETAG, total_length=100, last_modified="x")
    assert not is_file_changed(old, new)
