"""Comprehensive tests for :class:`TransportHeaders`."""

from __future__ import annotations

import copy
import pickle
from collections.abc import Mapping
from typing import assert_type

import pytest

from pyhaul.transport._headers import TransportHeaders

# ======================================================================
# Construction
# ======================================================================


class TestFromPairs:
    def test_basic(self) -> None:
        h = TransportHeaders.from_pairs([("Content-Type", "text/html"), ("ETag", '"abc"')])
        assert h["content-type"] == "text/html"
        assert h["etag"] == '"abc"'

    def test_preserves_duplicates(self) -> None:
        h = TransportHeaders.from_pairs(
            [
                ("Set-Cookie", "a=1"),
                ("Set-Cookie", "b=2"),
            ]
        )
        assert h.get_all("set-cookie") == ("a=1", "b=2")

    def test_strips_names_and_values(self) -> None:
        h = TransportHeaders.from_pairs([("  ETag  ", '  "y"  ')])
        assert h["etag"] == '"y"'

    def test_empty(self) -> None:
        h = TransportHeaders.from_pairs([])
        assert len(h) == 0
        assert not h

    def test_wire_order(self) -> None:
        h = TransportHeaders.from_pairs(
            [
                ("X-A", "1"),
                ("X-B", "2"),
                ("X-A", "3"),
            ]
        )
        assert h.get_all("x-a") == ("1", "3")
        assert list(h) == ["x-a", "x-b"]


class TestDirectConstruction:
    """``TransportHeaders(...)`` must normalize like :meth:`TransportHeaders.from_pairs`."""

    def test_bracket_access_case_insensitive_for_mixed_case_constructor_names(self) -> None:
        h = TransportHeaders([("My-Header", "Value")])
        assert h["My-Header"] == "Value"
        assert h["my-header"] == "Value"
        assert h.raw_items == (("my-header", "Value"),)

    def test_constructor_strips_names_and_values(self) -> None:
        h = TransportHeaders([("  Z  ", "  padded  ")])
        assert h["z"] == "padded"
        assert list(h) == ["z"]


class TestFromMapping:
    def test_round_trip(self) -> None:
        h = TransportHeaders.from_mapping({"Content-Length": "42", "ETag": '"z"'})
        assert h["content-length"] == "42"
        assert h.get_all("etag") == ('"z"',)

    def test_case_variants_stack(self) -> None:
        h = TransportHeaders.from_mapping({"ETag": "first", "etag": "second"})
        assert h["etag"] == "first"
        assert h.get_all("etag") == ("first", "second")


class TestBuild:
    def test_from_dict(self) -> None:
        h = TransportHeaders.build({"Content-Type": "text/html"})
        assert h["content-type"] == "text/html"

    def test_from_pairs_iterable(self) -> None:
        h = TransportHeaders.build([("A", "1"), ("B", "2")])
        assert h["a"] == "1"
        assert h["b"] == "2"

    def test_kwargs_underscore_to_dash(self) -> None:
        h = TransportHeaders.build(x_request_id="abc-123")
        assert h["x-request-id"] == "abc-123"

    def test_combined(self) -> None:
        h = TransportHeaders.build({"A": "1"}, x_extra="val")
        assert h["a"] == "1"
        assert h["x-extra"] == "val"

    def test_none_source(self) -> None:
        h = TransportHeaders.build(None, x_only="yes")
        assert h["x-only"] == "yes"

    def test_empty(self) -> None:
        h = TransportHeaders.build()
        assert len(h) == 0


# ======================================================================
# Mapping protocol — bracket access, get, contains, len, iter
# ======================================================================


class TestMappingProtocol:
    @pytest.fixture
    def h(self) -> TransportHeaders:
        return TransportHeaders.from_pairs(
            [
                ("Content-Type", "text/plain"),
                ("ETag", '"v1"'),
                ("Set-Cookie", "a=1"),
                ("Set-Cookie", "b=2"),
            ]
        )

    def test_getitem_first_value(self, h: TransportHeaders) -> None:
        assert h["set-cookie"] == "a=1"

    def test_getitem_case_insensitive(self, h: TransportHeaders) -> None:
        assert h["ETAG"] == '"v1"'
        assert h["etag"] == '"v1"'
        assert h["ETag"] == '"v1"'

    def test_getitem_missing_raises(self, h: TransportHeaders) -> None:
        with pytest.raises(KeyError, match="X-Missing"):
            h["X-Missing"]

    def test_get_returns_value(self, h: TransportHeaders) -> None:
        assert h.get("etag") == '"v1"'

    def test_get_missing_returns_none(self, h: TransportHeaders) -> None:
        assert h.get("x-missing") is None

    def test_get_with_default(self, h: TransportHeaders) -> None:
        assert h.get("x-missing", "fallback") == "fallback"

    def test_contains(self, h: TransportHeaders) -> None:
        assert "etag" in h
        assert "ETAG" in h
        assert "ETag" in h
        assert "x-missing" not in h

    def test_contains_non_string(self, h: TransportHeaders) -> None:
        assert 42 not in h  # type: ignore[comparison-overlap]

    def test_len_counts_unique_names(self, h: TransportHeaders) -> None:
        assert len(h) == 3

    def test_iter_unique_first_seen_order(self, h: TransportHeaders) -> None:
        assert list(h) == ["content-type", "etag", "set-cookie"]

    def test_keys_values_items(self, h: TransportHeaders) -> None:
        assert list(h.keys()) == ["content-type", "etag", "set-cookie"]
        assert list(h.values()) == ["text/plain", '"v1"', "a=1"]
        assert list(h.items()) == [
            ("content-type", "text/plain"),
            ("etag", '"v1"'),
            ("set-cookie", "a=1"),
        ]

    def test_is_mapping(self, h: TransportHeaders) -> None:
        assert isinstance(h, Mapping)


# ======================================================================
# Multi-value access
# ======================================================================


class TestGetAll:
    def test_returns_all(self) -> None:
        h = TransportHeaders.from_pairs(
            [
                ("Set-Cookie", "a=1"),
                ("ETag", '"x"'),
                ("Set-Cookie", "b=2"),
            ]
        )
        assert h.get_all("Set-Cookie") == ("a=1", "b=2")

    def test_missing_returns_empty_tuple(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        assert h.get_all("missing") == ()


# ======================================================================
# Boolean / emptiness
# ======================================================================


class TestBool:
    def test_empty_is_falsy(self) -> None:
        assert not TransportHeaders.from_pairs([])

    def test_nonempty_is_truthy(self) -> None:
        assert TransportHeaders.from_pairs([("A", "1")])


# ======================================================================
# Equality and hashing
# ======================================================================


class TestEqualityAndHashing:
    def test_equal_instances(self) -> None:
        a = TransportHeaders.from_pairs([("A", "1"), ("B", "2")])
        b = TransportHeaders.from_pairs([("A", "1"), ("B", "2")])
        assert a == b
        assert hash(a) == hash(b)

    def test_order_matters(self) -> None:
        a = TransportHeaders.from_pairs([("A", "1"), ("B", "2")])
        b = TransportHeaders.from_pairs([("B", "2"), ("A", "1")])
        assert a != b

    def test_hashable_as_dict_key(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        d = {h: "value"}
        assert d[h] == "value"

    def test_hashable_in_set(self) -> None:
        a = TransportHeaders.from_pairs([("A", "1")])
        b = TransportHeaders.from_pairs([("A", "1")])
        assert len({a, b}) == 1

    def test_eq_with_plain_dict(self) -> None:
        h = TransportHeaders.from_pairs([("content-type", "text/html")])
        assert h == {"content-type": "text/html"}

    def test_eq_plain_mapping_false_when_self_has_duplicate_field_names(self) -> None:
        """``Mapping.items()`` collapses to one value per key — cannot match full wire state.

        A :class:`dict` (or any mapping consumed via ``dict(their.items())``) cannot
        represent duplicate header names. Equality must not succeed on first-value
        coincidence alone.
        """
        h = TransportHeaders.from_pairs(
            [
                ("Set-Cookie", "a=1"),
                ("Set-Cookie", "b=2"),
            ]
        )
        assert h.get_all("set-cookie") == ("a=1", "b=2")
        naive_single = {"set-cookie": "a=1"}
        assert h != naive_single
        assert naive_single != h

    def test_ne_with_non_mapping(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        assert h != 42
        assert h != "not a mapping"


# ======================================================================
# Merge via |
# ======================================================================


class TestMerge:
    def test_or_headers(self) -> None:
        a = TransportHeaders.from_pairs([("A", "1")])
        b = TransportHeaders.from_pairs([("B", "2")])
        merged = a | b
        assert merged["a"] == "1"
        assert merged["b"] == "2"
        assert len(merged) == 2

    def test_or_dict(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        merged = h | {"B": "2"}
        assert merged["b"] == "2"
        assert isinstance(merged, TransportHeaders)

    def test_ror_dict(self) -> None:
        h = TransportHeaders.from_pairs([("B", "2")])
        merged = {"A": "1"} | h
        assert isinstance(merged, TransportHeaders)
        assert merged["a"] == "1"
        assert merged["b"] == "2"

    def test_or_preserves_order(self) -> None:
        a = TransportHeaders.from_pairs([("X", "1")])
        b = TransportHeaders.from_pairs([("Y", "2")])
        assert list((a | b).items()) == [("x", "1"), ("y", "2")]

    def test_or_from_iterable_of_pairs(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        merged = h | [("B", "2"), ("C", "3")]
        assert merged["b"] == "2"
        assert merged["c"] == "3"

    def test_or_type_error(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        with pytest.raises(TypeError):
            _ = h | 42

    def test_ror_type_error(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        with pytest.raises(TypeError):
            _ = 42 | h

    def test_or_bad_iterable_type_error(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        with pytest.raises(TypeError):
            _ = h | [1, 2, 3]


# ======================================================================
# Functional update
# ======================================================================


class TestFunctionalUpdate:
    def test_with_added(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        h2 = h.with_added("B", "2")
        assert "b" in h2
        assert "b" not in h

    def test_without(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1"), ("B", "2"), ("A", "3")])
        h2 = h.without("A")
        assert "a" not in h2
        assert h2["b"] == "2"
        assert len(h2) == 1

    def test_replace(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1"), ("B", "2"), ("A", "old")])
        h2 = h.replace("A", "new")
        assert h2["a"] == "new"
        assert h2.get_all("a") == ("new",)
        assert h2["b"] == "2"


# ======================================================================
# Multi-value extras
# ======================================================================


class TestMultiValueExtras:
    def test_getlist_alias(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1"), ("A", "2")])
        assert h.getlist("a") == ("1", "2")

    def test_multi_items(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1"), ("B", "2"), ("A", "3")])
        assert list(h.multi_items()) == [("a", "1"), ("b", "2"), ("a", "3")]


# ======================================================================
# Immutability
# ======================================================================


class TestImmutability:
    def test_setattr_blocked(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        with pytest.raises(AttributeError, match="immutable"):
            h.x = "nope"

    def test_delattr_blocked(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        with pytest.raises(AttributeError, match="immutable"):
            del h._items


# ======================================================================
# Repr — sensitive header redaction
# ======================================================================


class TestRepr:
    def test_basic_repr(self) -> None:
        h = TransportHeaders.from_pairs([("Content-Type", "text/html")])
        r = repr(h)
        assert r == "TransportHeaders([('content-type', 'text/html')])"

    def test_repr_list_shape_preserves_duplicate_field_names(self) -> None:
        """List-of-tuples ``repr`` mirrors wire rows (dict-shaped ``repr`` would lie)."""
        h = TransportHeaders.from_pairs(
            [
                ("X-Trace", "first"),
                ("X-Trace", "second"),
            ]
        )
        assert repr(h) == "TransportHeaders([('x-trace', 'first'), ('x-trace', 'second')])"

    def test_redacts_authorization(self) -> None:
        h = TransportHeaders.from_pairs(
            [
                ("Authorization", "Bearer sk-secret-key"),
                ("Content-Type", "text/html"),
            ]
        )
        r = repr(h)
        assert "sk-secret-key" not in r
        assert "[redacted]" in r
        assert "text/html" in r

    def test_redacts_proxy_authorization(self) -> None:
        h = TransportHeaders.from_pairs([("Proxy-Authorization", "Basic dXNlcjpwYXNz")])
        r = repr(h)
        assert "dXNlcjpwYXNz" not in r
        assert "[redacted]" in r

    def test_redacts_cookie(self) -> None:
        h = TransportHeaders.from_pairs([("Cookie", "session=abc123secret")])
        r = repr(h)
        assert "abc123secret" not in r
        assert "[redacted]" in r

    def test_redacts_set_cookie(self) -> None:
        h = TransportHeaders.from_pairs([("Set-Cookie", "sid=xyz; HttpOnly; Secure")])
        r = repr(h)
        assert "xyz" not in r
        assert "[redacted]" in r

    def test_repr_duplicate_set_cookie_rows_redacted_but_distinct(self) -> None:
        """Sensitive names stay redacted; tuple count still shows multiple wire rows."""
        h = TransportHeaders.from_pairs([("Set-Cookie", "a=1"), ("Set-Cookie", "b=2")])
        assert repr(h) == ("TransportHeaders([('set-cookie', '[redacted]'), ('set-cookie', '[redacted]')])")


class TestToSafeDict:
    def test_redacts_sensitive(self) -> None:
        h = TransportHeaders.from_pairs(
            [
                ("Authorization", "Bearer sk-secret"),
                ("Content-Type", "text/html"),
                ("Proxy-Authorization", "Basic creds"),
                ("Cookie", "session=abc"),
                ("Set-Cookie", "sid=xyz"),
            ]
        )
        safe = h.to_safe_dict()
        assert safe["authorization"] == "[redacted]"
        assert safe["proxy-authorization"] == "[redacted]"
        assert safe["cookie"] == "[redacted]"
        assert safe["set-cookie"] == "[redacted]"
        assert safe["content-type"] == "text/html"

    def test_empty(self) -> None:
        assert TransportHeaders.from_pairs([]).to_safe_dict() == {}

    def test_multi_value_last_wins(self) -> None:
        h = TransportHeaders.from_pairs([("X-A", "1"), ("X-A", "2")])
        assert h.to_safe_dict() == {"x-a": "2"}


class TestRawItems:
    def test_returns_tuple_of_tuples(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1"), ("B", "2")])
        assert h.raw_items == (("a", "1"), ("b", "2"))


# ======================================================================
# Wire / pickle / copy / match
# ======================================================================


class TestWire:
    def test_to_wire(self) -> None:
        h = TransportHeaders.from_pairs([("Content-Type", "text/html"), ("ETag", '"v1"')])
        assert h.to_wire() == b'content-type: text/html\r\netag: "v1"\r\n'

    def test_to_wire_utf8_non_ascii(self) -> None:
        h = TransportHeaders.from_pairs([("X-Greeting", "café")])
        assert h.to_wire() == "x-greeting: café\r\n".encode()


class TestPickle:
    def test_round_trip(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1"), ("B", "2")])
        h2 = pickle.loads(pickle.dumps(h))  # noqa: S301
        assert h == h2
        assert isinstance(h2, TransportHeaders)


class TestCopy:
    def test_copy_returns_self(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        assert copy.copy(h) is h

    def test_deepcopy_returns_self(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        assert copy.deepcopy(h) is h


class TestMatch:
    def test_match_args(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        match h:
            case TransportHeaders(items):
                assert items == (("a", "1"),)
            case _:
                pytest.fail("match failed")


# ======================================================================
# Type safety — assert_type checks (verified by type checkers, not runtime)
# ======================================================================


class TestTypeSafety:
    def test_get_returns_str_or_none(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        result = h.get("A")
        assert_type(result, str | None)

    def test_get_with_str_default_returns_str(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        result = h.get("A", "default")
        assert_type(result, str)

    def test_getitem_returns_str(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        result = h["A"]
        assert_type(result, str)

    def test_get_all_returns_tuple_of_str(self) -> None:
        h = TransportHeaders.from_pairs([("A", "1")])
        result = h.get_all("A")
        assert_type(result, tuple[str, ...])
