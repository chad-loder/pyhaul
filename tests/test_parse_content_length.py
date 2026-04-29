"""Unit tests for :func:`pyhaul._engine_common._parse_content_length`."""

from __future__ import annotations

import pytest

from pyhaul._engine_common import _parse_content_length


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        (" ", None),
        ("42", 42),
        ("  99  ", 99),
        ("1234, 1234", 1234),
        ("1234,1234", 1234),
        ("1000, 1000, 1000", 1000),
    ],
)
def test_parse_content_length_ok(raw: str | None, expected: int | None) -> None:
    assert _parse_content_length(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "abc",
        "12a",
        "12 34",
        "+12",
        "-5",
        "1.5",
        "1, 2",
        "10, 11",
        "10,,11",
    ],
)
def test_parse_content_length_invalid(raw: str) -> None:
    assert _parse_content_length(raw) is None
