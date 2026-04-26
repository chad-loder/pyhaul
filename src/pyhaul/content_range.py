"""Tolerant-but-honest Content-Range header parser.

Accepts the canonical form and common server deviations (``bytes=`` instead
of ``bytes ``, omitted unit token, benign OWS).  Rejects when interpretation
would require guessing.  Bias: false reject over false accept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pyhaul._types import ContentRangeError

# Re-export under a parser-specific name so callers can catch precisely.
ContentRangeParseError = ContentRangeError

_SATISFIED_RE = re.compile(r"^\s*(?:bytes\s*[= ]?\s*)?(\d+)\s*-\s*(\d+)\s*/\s*(\d+|\*)\s*$")

_UNSATISFIED_RE = re.compile(r"^\s*(?:bytes\s*[= ]?\s*)?\*\s*/\s*(\d+)\s*$")


@dataclass(frozen=True, slots=True)
class ContentRange:
    """Parsed Content-Range header value.

    For a satisfied range (206): ``start``, ``end`` are ints,
    ``instance_length`` is ``int | None`` (None means ``*``).

    For an unsatisfied range (416): ``start`` and ``end`` are None,
    ``instance_length`` is an int.
    """

    start: int | None
    end: int | None
    instance_length: int | None

    @property
    def is_unsatisfied(self) -> bool:
        return self.start is None

    @property
    def content_length(self) -> int:
        """Number of bytes in the range (end - start + 1).

        Only valid for satisfied ranges.
        """
        if self.start is None or self.end is None:
            msg = "content_length is not defined for unsatisfied ranges"
            raise ValueError(msg)
        return self.end - self.start + 1


def parse_content_range(raw: str) -> ContentRange:  # noqa: C901
    """Parse a Content-Range header value.

    Raises :class:`ContentRangeParseError` on invalid or ambiguous input.
    """
    stripped = raw.strip()
    if not stripped:
        raise ContentRangeParseError("empty Content-Range header")

    # Reject non-bytes units early.
    lower = stripped.lower()
    for bad_unit in ("items", "none"):
        if lower.startswith(bad_unit):
            raise ContentRangeParseError(f"unsupported unit in Content-Range: {stripped!r}")

    # Reject commas (multiple ranges).
    if "," in stripped:
        raise ContentRangeParseError(f"multiple ranges not supported: {stripped!r}")

    # Try unsatisfied form first (bytes */N).
    m = _UNSATISFIED_RE.match(stripped)
    if m is not None:
        instance_length = int(m.group(1))
        if instance_length <= 0:
            raise ContentRangeParseError(f"unsatisfied instance-length must be positive: {stripped!r}")
        return ContentRange(start=None, end=None, instance_length=instance_length)

    # Try satisfied form (bytes start-end/length_or_star).
    m = _SATISFIED_RE.match(stripped)
    if m is None:
        raise ContentRangeParseError(f"cannot parse Content-Range: {stripped!r}")

    start = int(m.group(1))
    end = int(m.group(2))
    length_token = m.group(3)

    if start > end:
        raise ContentRangeParseError(f"start > end in Content-Range: {start} > {end}")

    if length_token == "*":
        return ContentRange(start=start, end=end, instance_length=None)

    instance_length = int(length_token)
    if instance_length <= 0:
        raise ContentRangeParseError(f"instance-length must be positive: {stripped!r}")

    return ContentRange(start=start, end=end, instance_length=instance_length)
