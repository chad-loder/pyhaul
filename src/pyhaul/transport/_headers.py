"""Case-insensitive, immutable, multi-value HTTP response headers.

Implements `collections.abc.Mapping[str, str]` so bracket access,
``in``, ``len()``, iteration, ``.keys()``, ``.values()``, and
``.items()`` all work out of the box.

::

    headers["Content-Type"]        # first value or KeyError
    headers.get("Retry-After")     # first value or None
    "etag" in headers              # case-insensitive membership
    headers.get_all("Set-Cookie")  # all values in wire order
    headers | {"X-Extra": "val"}   # merge, returns new instance
    new = headers.replace("Content-Type", "text/plain")  # functional update

Immutable and hashable -- safe to attach to exceptions, log, or cache.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from typing import Self, cast, final, overload, override

type Pair = tuple[str, str]
type Pairs = tuple[Pair, ...]

_SENSITIVE: frozenset[str] = frozenset(
    {
        "authorization",
        "cookie",
        "proxy-authorization",
        "set-cookie",
        "x-api-key",
    }
)


def _norm_name(name: str) -> str:
    return name.strip().lower()


def _norm_value(value: str) -> str:
    return value.strip()


@final
class TransportHeaders(Mapping[str, str]):
    """Immutable, case-insensitive HTTP headers with multi-value support.

    Field names are matched case-insensitively per HTTP semantics.
    Duplicate names are preserved in wire order.

    Constructor arguments are normalized (names lowercased/stripped, values
    stripped) — same rules as :meth:`from_pairs`. Prefer :meth:`build`,
    :meth:`from_pairs`, or :meth:`from_mapping` for readability.
    """

    __slots__ = ("_hash", "_index", "_items")
    _items: Pairs
    _index: dict[str, tuple[int, ...]]
    _hash: int | None

    def __init__(self, items: Iterable[Pair] = (), /) -> None:
        norm_items: Pairs = tuple((_norm_name(k), _norm_value(v)) for k, v in items)
        index: dict[str, list[int]] = {}
        for i, (k, _) in enumerate(norm_items):
            index.setdefault(k, []).append(i)
        object.__setattr__(self, "_items", norm_items)
        object.__setattr__(self, "_index", {k: tuple(v) for k, v in index.items()})
        object.__setattr__(self, "_hash", None)

    # ------------------------------------------------------------------
    # Frozen-instance contract
    # ------------------------------------------------------------------

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError(
            f"{type(self).__name__} is immutable; use .with_added(), .without(), or .replace()",
        )

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def build(
        cls,
        source: Mapping[str, str] | Iterable[Pair] | None = None,
        /,
        **extra: str,
    ) -> Self:
        """Build from a mapping or pair-iterable, with optional kwargs.

        Underscores in keyword names are translated to dashes::

            TransportHeaders.build(
                {"Content-Type": "text/html"},
                x_request_id="abc-123",
            )
        """
        pairs: list[Pair] = []
        if isinstance(source, Mapping):
            pairs.extend((str(k), str(v)) for k, v in cast("Mapping[str, str]", source).items())
        elif source is not None:
            pairs.extend(source)
        pairs.extend((k.replace("_", "-"), v) for k, v in extra.items())
        return cls.from_pairs(pairs)

    @classmethod
    def from_pairs(cls, pairs: Iterable[Pair]) -> Self:
        """Build from an ordered ``(name, value)`` iterable.

        Preserves duplicate names and wire order for `get_all`.
        Normalization matches direct :class:`TransportHeaders` construction.
        """
        return cls(tuple(pairs))

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, str]) -> Self:
        """Build from a single-value-per-key mapping.

        Keys that differ only by case become separate entries.
        """
        return cls.from_pairs(mapping.items())

    # ------------------------------------------------------------------
    # Mapping[str, str] — the three abstract methods
    # ------------------------------------------------------------------

    @override
    def __getitem__(self, key: str) -> str:
        idx = self._index.get(_norm_name(key))
        if idx is None:
            raise KeyError(key)
        return self._items[idx[0]][1]

    @override
    def __iter__(self) -> Iterator[str]:
        return iter(self._index)

    @override
    def __len__(self) -> int:
        return len(self._index)

    # ------------------------------------------------------------------
    # Multi-value access
    # ------------------------------------------------------------------

    def get_all(self, name: str) -> tuple[str, ...]:
        """Return every value for *name* in wire order; ``()`` if absent."""
        idx = self._index.get(_norm_name(name))
        return tuple(self._items[i][1] for i in idx) if idx else ()

    getlist = get_all
    """Alias for `get_all` (werkzeug / multidict naming)."""

    def multi_items(self) -> Iterator[Pair]:
        """Yield every ``(name, value)`` pair, including duplicates."""
        return iter(self._items)

    # ------------------------------------------------------------------
    # Mapping.get — override for correct str | None typing
    # ------------------------------------------------------------------

    @overload
    def get(self, key: str, /) -> str | None: ...
    @overload
    def get(self, key: str, default: str, /) -> str: ...
    @overload
    def get[T](self, key: str, default: T, /) -> str | T: ...

    @override
    def get[T](self, key: str, default: T | None = None, /) -> str | T | None:
        """Return the first value for *key*, or *default* if absent."""
        idx = self._index.get(_norm_name(key))
        return self._items[idx[0]][1] if idx is not None else default

    # ------------------------------------------------------------------
    # Membership
    # ------------------------------------------------------------------

    @override
    def __contains__(self, key: object) -> bool:
        return isinstance(key, str) and _norm_name(key) in self._index

    # ------------------------------------------------------------------
    # Equality and hashing (immutable → hashable)
    # ------------------------------------------------------------------

    @override
    def __eq__(self, other: object) -> bool:
        if isinstance(other, TransportHeaders):
            return self._items == other._items
        if isinstance(other, Mapping):
            m = cast("Mapping[str, str]", other)
            # ``dict(Mapping.items())`` keeps only one value per key; ``self.items()``
            # only exposes the first value per normalized name. Never treat as equal
            # when this header bag carries multiple wire rows for the same field name.
            if len(self._items) != len(self._index):
                return False
            return dict(self.items()) == dict(m.items())
        return NotImplemented

    @override
    def __hash__(self) -> int:
        h = self._hash
        if h is None:
            h = hash(self._items)
            object.__setattr__(self, "_hash", h)
        return h

    # ------------------------------------------------------------------
    # Boolean / emptiness
    # ------------------------------------------------------------------

    def __bool__(self) -> bool:
        """Empty headers are falsy."""
        return len(self._items) > 0

    # ------------------------------------------------------------------
    # Merge / functional update
    # ------------------------------------------------------------------

    def __or__(self, other: object) -> TransportHeaders:
        """``headers | other`` returns new headers with *other* appended."""
        extra = self._coerce(other)
        if extra is None:
            return NotImplemented
        return TransportHeaders(self._items + extra)

    def __ror__(self, other: object) -> TransportHeaders:
        """``other | headers`` returns new headers with *other* prepended."""
        extra = self._coerce(other)
        if extra is None:
            return NotImplemented
        return TransportHeaders(extra + self._items)

    @staticmethod
    def _coerce(other: object) -> Pairs | None:
        if isinstance(other, TransportHeaders):
            return other.raw_items
        if isinstance(other, Mapping):
            m = cast("Mapping[str, str]", other)
            return tuple((_norm_name(k), _norm_value(v)) for k, v in m.items())
        if isinstance(other, Iterable) and not isinstance(other, (str, bytes)):
            try:
                it = cast("Iterable[tuple[str, str]]", other)
                return tuple((_norm_name(k), _norm_value(v)) for k, v in it)
            except (TypeError, ValueError):
                return None
        return None

    def with_added(self, name: str, value: str) -> Self:
        """Append ``(name, value)`` and return a new instance."""
        pair: Pair = (_norm_name(name), _norm_value(value))
        return type(self)((*self._items, pair))

    def without(self, name: str) -> Self:
        """Drop all entries for *name* and return a new instance."""
        needle = _norm_name(name)
        return type(self)(tuple(p for p in self._items if p[0] != needle))

    def replace(self, name: str, value: str) -> Self:
        """Drop all entries for *name* then append a single new one."""
        return self.without(name).with_added(name, value)

    # ------------------------------------------------------------------
    # Repr — ``[(name, value), ...]`` wire shape (not a dict literal); redacts sensitive
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        chunks: list[str] = []
        for k, v in self._items:
            if k in _SENSITIVE:
                chunks.append(f"({k!r}, '[redacted]')")
            else:
                chunks.append(f"({k!r}, {v!r})")
        return f"{type(self).__name__}([{', '.join(chunks)}])"

    # ------------------------------------------------------------------
    # Safe dict for structured logging
    # ------------------------------------------------------------------

    def to_safe_dict(self) -> dict[str, str]:
        """Header dict with sensitive values redacted.

        Suitable for passing to structured loggers like structlog::

            logger.info("response", headers=headers.to_safe_dict())
        """
        return {k: "[redacted]" if k in _SENSITIVE else v for k, v in self._items}

    # ------------------------------------------------------------------
    # Wire / pickle / copy / match
    # ------------------------------------------------------------------

    def to_wire(self) -> bytes:
        r"""Serialize to ``b'name: value\r\n...'`` form.

        Uses UTF-8 for the combined header lines. HTTP/1 historically treated
        field values as ISO-8859-1 octets; UTF-8 is common for Unicode today,
        and matches how aiohttp serializes outgoing prelude lines and how httpx
        defaults header encoding when emitting bytes from ``str`` values.
        """
        return b"".join(f"{k}: {v}\r\n".encode() for k, v in self._items)

    __match_args__ = ("raw_items",)

    @property
    def raw_items(self) -> Pairs:
        """All ``(lowercase_name, value)`` pairs in wire order."""
        return self._items

    def __copy__(self) -> Self:
        return self

    def __deepcopy__(self, memo: dict[int, object]) -> Self:
        return self

    def __reduce__(self) -> tuple[type[Self], tuple[Pairs]]:
        return type(self), (self._items,)
