"""Unit tests for :mod:`pyhaul.etag` — parsing, serialization, semantics.

No HTTP clients or mock transports; pure string ↔ :class:`~pyhaul.etag.EntityTag` logic.
"""

from __future__ import annotations

import pytest

import pyhaul.etag as etag_module
from pyhaul import (
    EMPTY_ETAG,
    EntityTag,
    ETag,
    format_entity_tag_for_http_header,
    is_weak_validator,
    parse_etag,
    parse_url,
)
from pyhaul.etag import parse_etag as parse_etag_direct

# ─── Module helpers & strict construction ───────────────────────────


def test_entity_tag_post_init_rejects_invalid_wildcard_opaque() -> None:
    with pytest.raises(ValueError, match="wildcard"):
        EntityTag(opaque_tag="nope", is_weak=False, is_wildcard=True, raw_value="")


def test_entity_tag_post_init_rejects_weak_wildcard() -> None:
    with pytest.raises(ValueError, match="wildcard"):
        EntityTag(opaque_tag="*", is_weak=True, is_wildcard=True, raw_value="")


def test_entity_tag_post_init_rejects_weak_without_opaque() -> None:
    with pytest.raises(ValueError, match="non-empty opaque"):
        EntityTag(opaque_tag="", is_weak=True, is_wildcard=False, raw_value="")


def test_entity_tag_post_init_rejects_weak_when_absent() -> None:
    with pytest.raises(ValueError, match="absent validator cannot be weak"):
        EntityTag(opaque_tag=None, is_weak=True, is_wildcard=False, raw_value="")


# ─── parse_etag wrapper ─────────────────────────────────────────────


def test_parse_etag_delegates_to_parse_header_value() -> None:
    assert parse_etag('"x"') == EntityTag.parse_header_value('"x"')


def test_parse_etag_type_error_on_non_string() -> None:
    with pytest.raises(TypeError, match="etag must be str"):
        parse_etag(None)
    with pytest.raises(TypeError, match="etag must be str"):
        parse_etag(b'"bytes"')


@pytest.mark.parametrize("raw", ["", "   ", "\t"])
def test_parse_etag_empty_returns_empty_sentinel(raw: str) -> None:
    result = parse_etag(raw)
    assert result is EMPTY_ETAG
    assert result == EMPTY_ETAG
    assert not result


def test_parse_etag_normalizes_quoted_and_weak() -> None:
    assert parse_etag('"abc123"') == ETag.from_canonical('"abc123"')
    assert parse_etag('"abc123"') == ETag.from_canonical("abc123")
    assert parse_etag('W/"weak-hash"') == ETag.from_canonical('W/"weak-hash"')
    assert parse_etag('W/"weak-hash"') == ETag.from_canonical("W/weak-hash")


def test_parse_etag_strips_whitespace_outside_quotes_only() -> None:
    assert parse_etag('  "abc"  ') == ETag.from_canonical('"abc"')
    assert parse_etag('\t"a b"\n') == ETag.from_canonical('"a b"')


def test_parse_etag_preserves_whitespace_inside_quotes() -> None:
    assert parse_etag('"  spaced  "') == ETag.from_canonical('"  spaced  "')
    assert parse_etag('"\tx\t"') == ETag.from_canonical('"\tx\t"')
    assert parse_etag('"   "') == ETag.from_canonical('"   "')


def test_parse_etag_weak_strips_between_prefix_and_quote_not_inside_opaque() -> None:
    assert parse_etag('W/   "x y"') == ETag.from_canonical('W/"x y"')
    assert parse_etag('w/\t " z "') == ETag.from_canonical('W/" z "')


def test_parse_etag_accepts_unquoted_opaque() -> None:
    assert parse_etag("not-quoted") == ETag.from_canonical("not-quoted")
    assert parse_etag("59627771-19") == ETag.from_canonical("59627771-19")


def test_parse_etag_weak_unquoted() -> None:
    assert parse_etag("W/unquoted-token") == ETag.from_canonical("W/unquoted-token")


@pytest.mark.parametrize(
    "raw",
    [
        '"unterminated',
        'unopened"',
        "W/",
        "bad token",
        "bad,comma",
        "a b",
        "\x01ctrl",
        '"dup""',
    ],
)
def test_parse_etag_rejects_malformed_as_empty(raw: str) -> None:
    assert parse_etag(raw) == EMPTY_ETAG


def test_parse_etag_empty_quoted_opaque_is_valid_not_sentinel() -> None:
    """RFC 9110 allows empty opaque inside quotes; do not conflate with absent sentinel."""
    t = parse_etag('""')
    assert t.opaque_tag == ""
    assert not t.is_weak
    assert not t.is_wildcard
    assert t.raw_value == '""'
    assert t is not EMPTY_ETAG
    assert t != EMPTY_ETAG
    assert t  # present (distinct from absent despite empty opaque string)
    assert t.to_http_field_value() == '""'
    assert format_entity_tag_for_http_header(t) == '""'


def test_parse_etag_weak_empty_quoted_is_absent() -> None:
    """Weak tag requires non-empty opaque — treat ``W/""`` like malformed → absent."""
    assert parse_etag('W/""') is EMPTY_ETAG


def test_parse_etag_wildcard_exact_and_strip() -> None:
    star = parse_etag("*")
    assert star.is_wildcard
    assert star.opaque_tag == "*"
    assert parse_etag(" \t*\n").is_wildcard


def test_parse_double_star_is_strong_opaque_not_list_syntax() -> None:
    """Only the lone ``*`` token is the wildcard; ``**`` is a legal opaque string."""
    t = parse_etag("**")
    assert not t.is_wildcard
    assert t.opaque_tag == "**"


# ─── Quoted-pair escapes ────────────────────────────────────────────


def test_parse_etag_quoted_pairs_escape_quote_and_backslash() -> None:
    t = parse_etag(r'"a\"b"')
    assert t.opaque_tag == 'a"b'
    t2 = parse_etag(r'"x\\y"')
    assert t2.opaque_tag == r"x\y"


def test_parse_etag_quoted_backslash_at_end_invalid() -> None:
    assert parse_etag('"' + "x" + "\\") == EMPTY_ETAG


def test_to_canonical_escapes_opaque_specials_round_trip() -> None:
    inner = 'say "quote"\\tail'
    t = EntityTag(opaque_tag=inner, is_weak=False, is_wildcard=False, raw_value="synthetic")
    canon = t.to_canonical()
    assert parse_etag(canon) == t
    assert parse_etag(canon).opaque_tag == inner


# ─── from_canonical ─────────────────────────────────────────────────


def test_from_canonical_strips_outer_ows_only() -> None:
    assert ETag.from_canonical('  "z"  ') == parse_etag('"z"')


def test_from_canonical_blank_is_empty() -> None:
    assert ETag.from_canonical("") is EMPTY_ETAG
    assert ETag.from_canonical("  \t  ") is EMPTY_ETAG


def test_from_canonical_legacy_bare_token_still_loads() -> None:
    assert ETag.from_canonical("legacy-plain").opaque_tag == "legacy-plain"


# ─── to_canonical / round-trip ────────────────────────────────────────


def test_to_canonical_matches_http_field_value() -> None:
    t = parse_etag('"z"')
    assert t.to_canonical() == t.to_http_field_value() == '"z"'


@pytest.mark.parametrize(
    "raw",
    [
        '"abc"',
        'W/"weak"',
        '"  spaced  "',
        'W/"x y"',
        '""',
        "*",
    ],
)
def test_round_trip_canonical_parse(raw: str) -> None:
    tag = parse_etag(raw)
    assert tag.to_canonical() == raw
    assert ETag.from_canonical(tag.to_canonical()) == tag


def test_empty_tag_canonical_and_format_are_empty_string() -> None:
    assert EMPTY_ETAG.to_canonical() == ""
    assert EMPTY_ETAG.to_http_field_value() == ""
    assert str(EMPTY_ETAG) == ""
    assert format_entity_tag_for_http_header(EMPTY_ETAG) == ""


# ─── format_entity_tag_for_http_header ──────────────────────────────


def test_format_entity_tag_for_http_header_matches_parse() -> None:
    assert format_entity_tag_for_http_header(parse_etag('"z"')) == '"z"'
    assert format_entity_tag_for_http_header(parse_etag('W/"z"')) == 'W/"z"'
    assert format_entity_tag_for_http_header(ETag.from_canonical("plain")) == '"plain"'
    inner = "  round  "
    assert format_entity_tag_for_http_header(parse_etag(f'"{inner}"')) == f'"{inner}"'


# ─── is_weak_validator ──────────────────────────────────────────────


def test_is_weak_validator() -> None:
    assert is_weak_validator(EMPTY_ETAG) is False
    assert is_weak_validator(ETag.from_canonical("abc")) is False
    assert is_weak_validator(ETag.from_canonical("W/x")) is True
    assert is_weak_validator(parse_etag('W/"z"')) is True


def test_empty_etag_singleton_identity() -> None:
    assert (
        EntityTag(
            opaque_tag=None,
            is_weak=False,
            is_wildcard=False,
            raw_value="",
        )
        == EMPTY_ETAG
    )
    assert (
        EntityTag(
            opaque_tag=None,
            is_weak=False,
            is_wildcard=False,
            raw_value="",
        )
        is not EMPTY_ETAG
    )


def test_pickle_absent_and_strong_empty_round_trip() -> None:
    import pickle

    restored_absent = pickle.loads(pickle.dumps(EMPTY_ETAG))  # noqa: S301
    assert restored_absent == EMPTY_ETAG
    assert restored_absent.to_http_field_value() == ""

    strong_empty = parse_etag('""')
    restored_se = pickle.loads(pickle.dumps(strong_empty))  # noqa: S301
    assert restored_se == strong_empty
    assert restored_se.to_http_field_value() == '""'


# ─── __eq__, __hash__, __repr__, __str__ ────────────────────────────


def test_eq_respects_semantics_ignores_raw_value() -> None:
    a = EntityTag.parse_header_value('  "same"  ')
    b = EntityTag.parse_header_value('"same"')
    assert a == b
    assert hash(a) == hash(b)
    assert a.raw_value != b.raw_value


def test_eq_notimplemented_for_foreign_type() -> None:
    assert EMPTY_ETAG.__eq__(object()) is NotImplemented


def test_repr_contains_semantic_fields_not_raw() -> None:
    r = repr(parse_etag('"x"'))
    assert "opaque_tag='x'" in r
    assert "raw_value" not in r


def test_str_matches_http_field_value() -> None:
    t = parse_etag('W/"w"')
    assert str(t) == t.to_http_field_value()


# ─── strong_equals / weak_equals / usable_for_byte_range ────────────


def test_strong_equals_only_when_both_strong_same_opaque() -> None:
    s = parse_etag('"a"')
    assert s.strong_equals(parse_etag('"a"'))
    assert not s.strong_equals(parse_etag('W/"a"'))
    assert not s.strong_equals(parse_etag('"b"'))
    assert not s.strong_equals(parse_etag("*"))
    assert not EMPTY_ETAG.strong_equals(s)


def test_weak_equals_ignores_weak_bit_matches_opaque_only() -> None:
    a = parse_etag('"x"')
    w = parse_etag('W/"x"')
    assert not a.weak_equals(EMPTY_ETAG)
    assert not parse_etag("*").weak_equals(a)
    assert a.weak_equals(w)
    assert w.weak_equals(a)


def test_strong_equals_weak_equals_false_for_non_entity_tag() -> None:
    t = parse_etag('"x"')
    assert t.strong_equals('"x"') is False
    assert t.weak_equals('"x"') is False


def test_usable_for_byte_range_precondition() -> None:
    assert EMPTY_ETAG.usable_for_byte_range_precondition is False
    assert parse_etag("*").usable_for_byte_range_precondition is False
    assert parse_etag('W/"z"').usable_for_byte_range_precondition is False
    assert parse_etag('"ok"').usable_for_byte_range_precondition is True
    assert parse_etag('""').usable_for_byte_range_precondition is True


def test_strong_equals_strong_empty_opaque() -> None:
    assert parse_etag('""').strong_equals(parse_etag('""'))
    assert not parse_etag('""').strong_equals(EMPTY_ETAG)


# ─── Integration smoke vs Url NewType ───────────────────────────────


def test_entity_tag_is_concrete_class_distinct_from_newtyped_url() -> None:
    url = parse_url("http://example.com/")
    etag = parse_etag('"abc"')
    assert type(url) is str
    assert type(etag).__name__ == "EntityTag"


def test_parse_etag_direct_same_symbol_export_path() -> None:
    assert parse_etag_direct('"q"') == parse_etag('"q"')


def test_parse_etag_accepts_unicode_unquoted_opaque() -> None:
    """Unquoted opaques allow Unicode like decoded header libraries (e.g. httpx)."""
    q = parse_etag('"cafè"')
    u = parse_etag("cafè")
    assert q.opaque_tag == u.opaque_tag == "cafè"
    assert parse_etag('W/"cafè"').opaque_tag == parse_etag("W/cafè").opaque_tag == "cafè"
    assert parse_etag("W/cafè").is_weak
    latin = parse_etag("bad\xff")
    assert latin.opaque_tag == "bad\xff"


# ─── Controls / separators rejected in unquoted ──────────────────────


@pytest.mark.parametrize(
    "bad_unquoted",
    ["~bad\x7f", "\x01ctrl"],
)
def test_parse_rejects_controls_and_separators_unquoted(bad_unquoted: str) -> None:
    assert parse_etag(bad_unquoted) == EMPTY_ETAG


def test_private_helpers_edge_cases() -> None:
    """Cover trivial branches in grammar helpers (normally unreachable via public API)."""
    assert etag_module._opaque_ok_unquoted_token("") is False
    assert etag_module._parse_leading_quoted_opaque_whole("not-a-quoted-string") is None
    assert etag_module._parse_leading_quoted_opaque_whole('""') == ""


def test_weak_tag_escapes_opaque_in_canonical_round_trip() -> None:
    inner = 'w"k\\'
    w = EntityTag(opaque_tag=inner, is_weak=True, is_wildcard=False, raw_value="")
    assert parse_etag(w.to_canonical()).opaque_tag == inner
    assert parse_etag(w.to_canonical()).is_weak
