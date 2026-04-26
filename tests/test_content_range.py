"""Table-driven tests for the Content-Range parser."""

from __future__ import annotations

import pytest

from pyhaul.content_range import ContentRange, ContentRangeParseError, parse_content_range

# ---------------------------------------------------------------------------
# Accept cases: (raw header value, expected ContentRange)
# ---------------------------------------------------------------------------

ACCEPT_CASES: list[tuple[str, ContentRange]] = [
    # Canonical 206
    ("bytes 0-499/1000", ContentRange(start=0, end=499, instance_length=1000)),
    ("bytes 500-999/1000", ContentRange(start=500, end=999, instance_length=1000)),
    # Unknown instance length
    ("bytes 0-499/*", ContentRange(start=0, end=499, instance_length=None)),
    # Single byte
    ("bytes 0-0/1", ContentRange(start=0, end=0, instance_length=1)),
    # Large values
    (
        "bytes 0-104857599/104857600",
        ContentRange(start=0, end=104857599, instance_length=104857600),
    ),
    # Benign OWS around the value
    ("  bytes 0-499/1000  ", ContentRange(start=0, end=499, instance_length=1000)),
    # OWS after unit token
    ("bytes  0-499/1000", ContentRange(start=0, end=499, instance_length=1000)),
    # bytes= instead of bytes (common server mistake)
    ("bytes=0-499/1000", ContentRange(start=0, end=499, instance_length=1000)),
    # No unit token (some servers omit it)
    ("0-499/1000", ContentRange(start=0, end=499, instance_length=1000)),
    # 416 unsatisfied: bytes */N
    ("bytes */1000", ContentRange(start=None, end=None, instance_length=1000)),
    # 416 with equals
    ("bytes=*/1000", ContentRange(start=None, end=None, instance_length=1000)),
    # 416 bare
    ("*/1000", ContentRange(start=None, end=None, instance_length=1000)),
    # OWS around slash in unsatisfied
    ("bytes * / 1000", ContentRange(start=None, end=None, instance_length=1000)),
]


@pytest.mark.parametrize(
    ("raw", "expected"),
    ACCEPT_CASES,
    ids=[c[0].strip() for c in ACCEPT_CASES],
)
def test_accept(raw: str, expected: ContentRange) -> None:
    result = parse_content_range(raw)
    assert result == expected


# ---------------------------------------------------------------------------
# Reject cases: (raw header value, reason fragment for the error message)
# ---------------------------------------------------------------------------

REJECT_CASES: list[tuple[str, str]] = [
    # Empty / whitespace
    ("", "empty"),
    ("   ", "empty"),
    # Non-bytes unit
    ("items 0-499/1000", "unit"),
    # Start > end
    ("bytes 500-499/1000", "start > end"),
    # Negative numbers
    ("bytes -1-499/1000", ""),
    # Non-decimal in range
    ("bytes abc-499/1000", ""),
    ("bytes 0-xyz/1000", ""),
    ("bytes 0-499/abc", ""),
    # Multiple ranges (not a Content-Range)
    ("bytes 0-499/1000, 500-999/1000", ""),
    # Missing slash
    ("bytes 0-499", ""),
    # Missing dash
    ("bytes 0/1000", ""),
    # Empty segments
    ("bytes -/1000", ""),
    ("bytes 0-/1000", ""),
    ("bytes /1000", ""),
    # Whitespace inside numbers
    ("bytes 0-49 9/1000", ""),
    # Instance length of 0 in unsatisfied
    ("bytes */0", ""),
    # Negative instance length via overflow (just garbage)
    ("bytes 0-499/-1", ""),
    # Star in satisfied range position
    ("bytes *-499/1000", ""),
    ("bytes 0-*/1000", ""),
    # Boolean-ish
    ("true", ""),
]


@pytest.mark.parametrize(
    ("raw", "_reason"),
    REJECT_CASES,
    ids=[c[0] if c[0].strip() else repr(c[0]) for c in REJECT_CASES],
)
def test_reject(raw: str, _reason: str) -> None:
    with pytest.raises(ContentRangeParseError):
        parse_content_range(raw)


# ---------------------------------------------------------------------------
# ContentRange properties
# ---------------------------------------------------------------------------


class TestContentRangeProperties:
    def test_satisfied_range(self) -> None:
        cr = ContentRange(start=100, end=199, instance_length=1000)
        assert cr.is_unsatisfied is False
        assert cr.content_length == 100

    def test_unsatisfied(self) -> None:
        cr = ContentRange(start=None, end=None, instance_length=1000)
        assert cr.is_unsatisfied is True

    def test_unknown_instance_length(self) -> None:
        cr = ContentRange(start=0, end=99, instance_length=None)
        assert cr.instance_length is None
        assert cr.content_length == 100

    def test_frozen(self) -> None:
        cr = ContentRange(start=0, end=99, instance_length=100)
        with pytest.raises(Exception, match=r"frozen|cannot assign"):
            cr.start = 5  # type: ignore[misc]
