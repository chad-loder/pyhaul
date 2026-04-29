"""HTTP ``entity-tag`` parsing and serialization for pyhaul.

Immutable :class:`EntityTag` values model absent validators, strong / weak tags,
and the ``*`` wildcard used with ``If-Match`` / ``If-None-Match``.

Absent validators use ``opaque_tag is None`` — distinct from a present strong tag
whose opaque is the empty string (wire form ``""``).

Equality and hashing intentionally ignore :attr:`~EntityTag.raw_value` — only
the semantic triple ``(opaque_tag, is_weak, is_wildcard)`` matters — so the same
logical validator compares equal regardless of quoting quirks in the original
header bytes.

Checkpoint TLV payloads use :meth:`EntityTag.to_canonical` / :meth:`EntityTag.from_canonical`:
RFC ``entity-tag`` shaped strings (strong ``"<opaque>"``, weak ``W/"<opaque>"``, or ``*``),
so delimiters are explicit and every opaque round-trips byte-identically.
Legacy bare-token blobs (``abc`` / ``W/abc``) still decode via :meth:`from_canonical`.

Unquoted opaques allow non-ASCII Unicode (matching tolerant HTTP header field decoding as in
libraries like httpx: ascii / utf-8 / latin-1 fallbacks on the wire). Structural characters
(SP, HTAB, CR, LF, ``"``, ``,``), other ASCII controls (below SP), and DEL are still rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, final

# ─── Module-private grammar helpers (no allocations on hot absent path) ───

_MIN_WHOLE_QUOTED_CHARS = 2  # opening ``"`` + closing ``"`` (fast path: no escapes)

# Unquoted opaque: allow Unicode (obs-text-friendly); still forbid RFC-ish separators and C0 + DEL.
_UNQUOTED_DISALLOWED_CHARS = frozenset(' \t\r\n",')
_UNQUOTED_MIN_CODE_POINT = 32  # exclude ASCII controls (SP handled via disallowed set)
_UNQUOTED_DEL_CODE_POINT = 127


def _opaque_ok_unquoted_token(value: str) -> bool:
    """True if *value* is usable as a legacy bare opaque (no surrounding quotes).

    Allows non-ASCII (consistent with quoted-string results once headers are decoded). Still
    rejects whitespace / quote / comma and ASCII control characters (C0 + DEL), which cannot
    appear in a stable bare token.
    """
    if not value:
        return False
    if any(c in _UNQUOTED_DISALLOWED_CHARS for c in value):
        return False
    return all((o := ord(c)) >= _UNQUOTED_MIN_CODE_POINT and o != _UNQUOTED_DEL_CODE_POINT for c in value)


def _parse_leading_quoted_opaque_whole(value: str) -> str | None:
    """Parse ``quoted-string`` consuming *value* entirely (RFC 9110 ``quoted-string``)."""
    if not value.startswith('"'):
        return None
    # Fast path: no backslashes → no quoted-pairs; require exactly two ``"`` so the only
    # closing quote is the final octet (rejects ``"a"b"`` without falling through).
    if (
        len(value) >= _MIN_WHOLE_QUOTED_CHARS
        and value.endswith('"')
        and "\\" not in value
        and value.count('"') == _MIN_WHOLE_QUOTED_CHARS
    ):
        return value[1:-1]
    i = 1
    chunks: list[str] = []
    while i < len(value):
        c = value[i]
        if c == "\\":
            if i + 1 >= len(value):
                return None
            chunks.append(value[i + 1])
            i += 2
        elif c == '"':
            if i != len(value) - 1:
                return None
            return "".join(chunks)
        else:
            chunks.append(c)
            i += 1
    return None


def _split_weak_prefix(value: str) -> tuple[bool, str]:
    """Strip ``W/`` / ``w/`` and liberal OWS before the tag payload."""
    if value.casefold().startswith("w/"):
        return True, value[2:].lstrip()
    return False, value


def _escape_opaque_for_dquotes(opaque: str) -> str:
    return opaque.replace("\\", "\\\\").replace('"', '\\"')


# ─── Public model ───


@final
@dataclass(frozen=True, slots=True, eq=False)
class EntityTag:
    """Immutable HTTP entity-tag with explicit weak / wildcard discrimination.

    ``opaque_tag is None`` means absent (no validator). Otherwise ``opaque_tag`` is the
    opaque string inside the RFC grammar (possibly ``""`` for ``ETag: ""``).

    Use :meth:`parse_header_value` for wire values (``ETag`` response header).
    Use :meth:`from_canonical` for pyhaul ``.ctrl`` TLV UTF-8 payloads.

    Instances compare equal by ``(opaque_tag, is_weak, is_wildcard)`` only.
    """

    opaque_tag: str | None
    is_weak: bool
    is_wildcard: bool
    raw_value: str

    def __post_init__(self) -> None:
        """Validate invariants for wildcard vs absent vs weak-strong tags."""
        if self.is_wildcard:
            if self.opaque_tag != "*" or self.is_weak:
                msg = "wildcard entity-tag requires opaque_tag='*' and is_weak=False"
                raise ValueError(msg)
            return
        if self.opaque_tag is None:
            if self.is_weak:
                msg = "absent validator cannot be weak"
                raise ValueError(msg)
            return
        if self.opaque_tag == "" and self.is_weak:
            msg = "weak validator requires a non-empty opaque_tag"
            raise ValueError(msg)

    @classmethod
    def parse_header_value(cls, raw: str) -> EntityTag:  # noqa: PLR0911
        """Parse an ``ETag`` / ``If-Match`` style field-value fragment.

        Accepts RFC ``quoted-string`` and liberal unquoted tokens. A strong empty
        quoted opaque (``""``) is valid grammar and preserved on the instance
        (including :attr:`raw_value`). ``W/""`` yields :data:`EMPTY_ETAG` because weak
        validators require a non-empty opaque here. Malformed input yields
        :data:`EMPTY_ETAG`. Whitespace-only → :data:`EMPTY_ETAG`.
        """
        stripped = raw.strip()
        if not stripped:
            return EMPTY_ETAG

        if stripped == "*":
            return cls(
                opaque_tag="*",
                is_weak=False,
                is_wildcard=True,
                raw_value=raw,
            )

        weak, body = _split_weak_prefix(stripped)
        if not body:
            return EMPTY_ETAG

        if body.startswith('"'):
            opaque = _parse_leading_quoted_opaque_whole(body)
            if opaque is None:
                return EMPTY_ETAG
        elif _opaque_ok_unquoted_token(body):
            opaque = body
        else:
            return EMPTY_ETAG

        if weak and not opaque:
            return EMPTY_ETAG

        return cls(
            opaque_tag=opaque,
            is_weak=weak,
            is_wildcard=False,
            raw_value=raw,
        )

    @classmethod
    def from_canonical(cls, value: str) -> EntityTag:
        """Rebuild from UTF-8 stored by :meth:`to_canonical`.

        Expects quoted RFC ``entity-tag`` text for values written by current pyhaul.
        Leading / trailing ASCII OWS is stripped once (surrounding the whole blob).
        Legacy control files may hold bare tokens (no quotes); those still parse when
        they satisfy the same rules as :meth:`parse_header_value`.
        """
        stripped = value.strip()
        if not stripped:
            return EMPTY_ETAG
        return cls.parse_header_value(stripped)

    def __bool__(self) -> bool:
        """True when this tag is present (wildcard or any opaque, including empty)."""
        return self.is_wildcard or self.opaque_tag is not None

    def __eq__(self, other: object) -> bool:
        """Equality by semantic triple; :attr:`raw_value` is ignored."""
        if not isinstance(other, EntityTag):
            return NotImplemented
        return (
            self.opaque_tag == other.opaque_tag
            and self.is_weak == other.is_weak
            and self.is_wildcard == other.is_wildcard
        )

    def __hash__(self) -> int:
        """Hash matches :meth:`__eq__` (semantic triple only)."""
        return hash((self.opaque_tag, self.is_weak, self.is_wildcard))

    def __repr__(self) -> str:
        """Debug representation without :attr:`raw_value` (can be large)."""
        return (
            f"{type(self).__name__}(opaque_tag={self.opaque_tag!r}, "
            f"is_weak={self.is_weak}, is_wildcard={self.is_wildcard})"
        )

    def __str__(self) -> str:
        """Canonical HTTP ``entity-tag`` field-value (empty when absent)."""
        return self.to_http_field_value()

    # --- Semantics (RFC 9110 §8.8.3 strong / weak comparison) ---

    def strong_equals(self, other: object) -> bool:
        """Strong comparison: both strong validators and opaque_tags byte-equal."""
        if not isinstance(other, EntityTag):
            return False
        if not self or not other:
            return False
        if self.is_wildcard or other.is_wildcard:
            return False
        if self.is_weak or other.is_weak:
            return False
        so, oo = self.opaque_tag, other.opaque_tag
        if so is None or oo is None:
            return False
        return so == oo

    def weak_equals(self, other: object) -> bool:
        """Weak comparison: opaque_tags equal; weak/strong flags ignored (wildcards excluded)."""
        if not isinstance(other, EntityTag):
            return False
        if not self or not other:
            return False
        if self.is_wildcard or other.is_wildcard:
            return False
        so, oo = self.opaque_tag, other.opaque_tag
        if so is None or oo is None:
            return False
        return so == oo

    @property
    def usable_for_byte_range_precondition(self) -> bool:
        """Strong, non-wildcard tag suitable for ``If-Range`` / strict 206 equality."""
        return bool(self) and not self.is_weak and not self.is_wildcard

    # --- Serialization ---

    def to_http_field_value(self) -> str:
        """RFC ``entity-tag`` for outbound headers (``If-Range``, ``If-Match``, …)."""
        if self.opaque_tag is None:
            return ""
        if self.is_wildcard:
            return "*"
        esc = _escape_opaque_for_dquotes(self.opaque_tag)
        if self.is_weak:
            return f'W/"{esc}"'
        return f'"{esc}"'

    def to_canonical(self) -> str:
        r"""RFC-shaped ``entity-tag`` text for ``.part.ctrl`` TLV UTF-8 (or ``""`` when absent).

        Uses the same quoting as :meth:`to_http_field_value` so the opaque is always
        framed by ``quoted-string`` rules (escaping ``\`` and ``"`` inside the opaque).
        """
        return self.to_http_field_value()


EMPTY_ETAG: Final[EntityTag] = EntityTag(
    opaque_tag=None,
    is_weak=False,
    is_wildcard=False,
    raw_value="",
)

# Historical public name — ``ETag`` was previously a ``typing.NewType`` over ``str``.
ETag = EntityTag


def parse_etag(raw: object) -> EntityTag:
    """Backward-compatible wrapper around :meth:`EntityTag.parse_header_value`.

    Non-string raises :class:`TypeError`.
    """
    if not isinstance(raw, str):
        raise TypeError(f"etag must be str, got {type(raw).__name__}")
    return EntityTag.parse_header_value(raw)


def format_entity_tag_for_http_header(etag: EntityTag) -> str:
    """Serialize for merged request headers; absent tags yield ``""``."""
    return etag.to_http_field_value()


def is_weak_validator(etag: EntityTag) -> bool:
    """True if *etag* is a weak validator (``W/…``).

    Weak validators are not used for byte-range preconditioning in pyhaul.
    """
    return bool(etag) and etag.is_weak
