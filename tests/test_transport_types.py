"""Tests for ``pyhaul.transport`` value types."""

from __future__ import annotations

from collections.abc import Iterator

from pyhaul._types import HaulError
from pyhaul.transport import (
    TransportConnectionError,
    TransportError,
    TransportHTTPError,
    TransportResponse,
    TransportTLSError,
    TransportUnsupportedError,
)
from pyhaul.transport.types import TransportHeaders, TransportRequestOptions


class TestTransportHeadersFromPairs:
    def test_get_returns_first_value(self) -> None:
        h = TransportHeaders.from_pairs(
            [
                ("Content-Type", "text/plain"),
                ("X-Test", "first"),
                ("X-Test", "second"),
            ]
        )
        assert h.get("X-Test") == "first"
        assert h.get("content-type") == "text/plain"

    def test_get_all_preserves_order_and_duplicates(self) -> None:
        h = TransportHeaders.from_pairs(
            [
                ("Set-Cookie", "a=1"),
                ("Set-Cookie", "b=2"),
                ("ETag", '"v1"'),
            ]
        )
        assert h.get_all("set-cookie") == ("a=1", "b=2")
        assert h.get_all("SET-COOKIE") == ("a=1", "b=2")
        assert h.get_all("etag") == ('"v1"',)

    def test_get_missing_returns_empty_string(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        assert h.get("missing") == ""

    def test_get_all_missing_returns_empty_tuple(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        assert h.get_all("missing") == ()

    def test_names_are_case_insensitive(self) -> None:
        h = TransportHeaders.from_pairs([("ETag", '"x"')])
        assert h.get("etag") == '"x"'
        assert h.get("ETAG") == '"x"'

    def test_strips_names_and_values(self) -> None:
        h = TransportHeaders.from_pairs([("  ETag  ", '  "y"  ')])
        assert h.get("etag") == '"y"'

    def test_direct_construction_is_rejected(self) -> None:
        try:
            TransportHeaders(object(), ())
        except TypeError as exc:
            assert "from_pairs" in str(exc)
        else:
            raise AssertionError("expected TypeError")


class TestTransportHeadersFromMapping:
    def test_round_trip_single_values(self) -> None:
        h = TransportHeaders.from_mapping({"Content-Length": "42", "ETag": '"z"'})
        assert h.get("content-length") == "42"
        assert h.get_all("etag") == ('"z"',)

    def test_case_variants_in_dict_stack_as_pairs(self) -> None:
        """Same logical field with different spellings becomes two ``etag`` rows."""
        h = TransportHeaders.from_mapping({"ETag": "first", "etag": "second"})
        assert h.get("ETag") == "first"
        assert h.get_all("etag") == ("first", "second")


class TestTransportRequestOptions:
    def test_defaults_are_none(self) -> None:
        o = TransportRequestOptions()
        assert o.timeout is None
        assert o.verify is None
        assert o.allow_redirects is None

    def test_round_trip_fields(self) -> None:
        o = TransportRequestOptions(timeout=(3.0, 27.0), verify=False, allow_redirects=True)
        assert o.timeout == (3.0, 27.0)
        assert o.verify is False
        assert o.allow_redirects is True


def test_transport_errors_message() -> None:
    assert str(TransportError("base")) == "base"
    assert str(TransportConnectionError("conn")) == "conn"
    assert str(TransportTLSError("tls")) == "tls"
    assert str(TransportUnsupportedError("nope")) == "nope"


def test_transport_connection_error_mro() -> None:
    """``TransportConnectionError`` bridges pyhaul and stdlib connection errors."""
    exc = TransportConnectionError("boom")
    assert isinstance(exc, HaulError)
    assert isinstance(exc, TransportError)
    assert isinstance(exc, ConnectionError)
    assert isinstance(exc, OSError)


def test_transport_http_error_status() -> None:
    exc = TransportHTTPError("bad", status_code=503)
    assert str(exc) == "bad"
    assert exc.status_code == 503

    bare = TransportHTTPError("no code")
    assert bare.status_code is None


class _DummyTransportResponse:
    """Minimal object that should structurally satisfy :class:`TransportResponse`."""

    @property
    def status_code(self) -> int:
        return 200

    @property
    def headers(self) -> TransportHeaders:
        return TransportHeaders.from_mapping({})

    def raise_for_status(self) -> None:
        return None

    def iter_raw_bytes(self, *, chunk_size: int) -> Iterator[bytes]:
        if chunk_size < 1:
            raise ValueError("chunk_size must be positive")
        yield b"hello"
        yield b"world"


def test_transport_response_protocol_structural() -> None:
    r: TransportResponse = _DummyTransportResponse()
    assert r.status_code == 200
    assert list(r.iter_raw_bytes(chunk_size=1024)) == [b"hello", b"world"]
