"""Shared types and result containers for pyhaul."""

from __future__ import annotations

import hashlib
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC
from email.utils import parsedate_to_datetime
from http import HTTPStatus
from pathlib import Path
from typing import NewType
from urllib.parse import urlparse

import pyhaul.etag as _etag
from pyhaul.transport._headers import TransportHeaders

EMPTY_ETAG = _etag.EMPTY_ETAG
ETag = _etag.ETag
EntityTag = _etag.EntityTag
format_entity_tag_for_http_header = _etag.format_entity_tag_for_http_header
is_weak_validator = _etag.is_weak_validator
parse_etag = _etag.parse_etag

ByteOffset = NewType("ByteOffset", int)
"""Absolute byte offset from the start of the download target."""

ByteLength = NewType("ByteLength", int)
"""Byte count — length of a range, piece, or buffer."""

Url = NewType("Url", str)
"""An http(s) URL that has passed :func:`parse_url`."""

EMPTY_URL: Url = Url("")
"""Shared "no URL yet" sentinel for components constructed before the
downloader has a parsed URL to hand them (e.g. tests, trackers built
for size bookkeeping only)."""

_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})

# Status codes CDNs and origins often use for temporary overload, upstream failure,
# or rate limiting — conservative superset for default retry hints.
_TRANSIENT_HTTP_STATUSES: frozenset[int] = frozenset(
    {
        HTTPStatus.REQUEST_TIMEOUT,
        HTTPStatus.TOO_EARLY,
        HTTPStatus.TOO_MANY_REQUESTS,
        HTTPStatus.INTERNAL_SERVER_ERROR,
        HTTPStatus.BAD_GATEWAY,
        HTTPStatus.SERVICE_UNAVAILABLE,
        HTTPStatus.GATEWAY_TIMEOUT,
        520,  # Cloudflare: unknown error
        522,  # Cloudflare: connection timed out
        524,  # Cloudflare: a timeout occurred
    },
)


def _retry_after_raw_to_seconds(
    raw: str | None,
    *,
    now: Callable[[], float] | None = None,
) -> float | None:
    """Parse ``Retry-After`` into seconds after ``now()`` — RFC 9110 §10.2.3 form.

    Tries ``HTTP-date`` first (same order as common clients), then ``delay-seconds``.
    Returns ``None`` if absent or unparsable. HTTP-dates in the past map to ``0.0``.
    Does **not** cap values — callers choose retry policy.
    """
    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    clock = time.time if now is None else now

    try:
        dt = parsedate_to_datetime(stripped)
    except (TypeError, ValueError):
        dt = None
    if dt is not None:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        delta = dt.timestamp() - clock()
        out = max(0.0, delta)
        return float(out)

    if stripped.isdigit():
        sec = int(stripped)
        return float(sec)

    return None


def parse_url(raw: str) -> Url:
    """Validate *raw* as an http(s) URL and brand it as :data:`Url`.

    Empty / whitespace-only input returns :data:`EMPTY_URL` ("no URL
    yet") — callers that forbid that case (e.g. the CLI) must check
    separately. Non-empty input must have an ``http`` or ``https``
    scheme and a host, or :class:`ValueError` is raised.
    """
    if not raw or not raw.strip():
        return EMPTY_URL
    parsed = urlparse(raw)
    if parsed.scheme.lower() not in _URL_SCHEMES:
        raise ValueError(
            f"unsupported url scheme {parsed.scheme!r}; expected http or https",
        )
    if not parsed.netloc:
        raise ValueError(f"url is missing a host: {raw!r}")
    return Url(raw)


@dataclass(frozen=True, slots=True, kw_only=True)
class ServerMeta:
    """Typed view of the response headers pyhaul cares about.

    ``total_length`` is ``None`` when the server omits ``Content-Length``
    or returns a non-integer value.  Every string field is ``""`` when
    the corresponding header is absent; ``is_file_changed`` treats
    missing ``last_modified`` on either side as "cannot prove unchanged".
    """

    etag: ETag = EMPTY_ETAG
    total_length: int | None = None
    last_modified: str = ""
    content_type: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ProbeResult:
    """Structured remote metadata from :func:`~pyhaul.probe.probe` / :func:`~pyhaul.async_probe.probe_async`.

    The probe sequence mirrors common CDN/origin behaviour: send ``HEAD``, then — when
    metadata is still incomplete — issue ``GET`` with ``Range: bytes=0-0`` (see pypdl-style
    discovery). Values are best-effort hints for planners (concurrent range shards,
    progress UI); they are not a substitute for download-time validation.
    """

    url: Url
    status_code: int
    """HTTP status from the response that supplied the merged snapshot (usually GET)."""

    total_length: int | None
    """Total entity length when inferable from ``Content-Length`` or ``Content-Range``."""

    etag: ETag
    last_modified: str
    content_type: str
    content_disposition: str
    """Raw ``Content-Disposition`` field value, if any."""

    accept_ranges_bytes: bool
    """True when ``Accept-Ranges: bytes`` is advertised."""

    head_attempted: bool
    head_status_code: int | None
    ranged_get_used: bool

    @property
    def supports_concurrent_byte_ranges(self) -> bool:
        """Whether byte-range sharding is plausible: ranges supported and size known."""
        return self.accept_ranges_bytes and self.total_length is not None


@dataclass(slots=True)
class HaulState:
    """Mutable progress bag updated in-place by ``haul()`` / ``haul_async()``.

    Pass an instance as the optional *state* parameter; the engine
    updates it throughout the download.  After the call — whether it
    returned, raised :class:`PartialHaulError`, or let a transport
    exception fly — the bag reflects the state at the point of exit.

    ``valid_length`` is **not monotonic across attempts**: a server
    response that invalidates prior progress rewinds the cursor to
    zero.

    ``reported_length`` is the total size of the resource as claimed by
    the server, derived from the ``Content-Range`` (complete-length) or
    ``Content-Length`` headers.  It may be ``None`` if the server omits
    the total, and may not match the final on-disk size if the server
    misbehaves.  (Useful for progress UIs.)
    """

    is_complete: bool = False
    bytes_read: int = 0
    valid_length: int = 0
    reported_length: int | None = None
    block_size: int = 8 * 1024 * 1024
    hashes: list[bytes] = field(default_factory=list[bytes])


AsyncProgressCallback = Callable[[HaulState], None | Awaitable[None]]
"""Progress hook for :func:`~pyhaul.async_engine.haul_async`.

May be an ordinary function (returns ``None``) or return an awaitable
(e.g. ``async def`` without awaiting it yourself); the engine awaits it
when applicable so callers need not ``asyncio.create_task`` each chunk.
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class CompleteHaul:
    """Returned by ``haul()`` on success.

    Carries completion metadata that only exists once the file is done.
    Progress counters (``bytes_read``, ``valid_length``) live in
    :class:`HaulState`, not here.
    """

    elapsed: float
    sha256: str  # This is the "Tree Hash" result (hash-of-hashes-N)
    etag: ETag
    content_type: str


class HaulError(Exception):
    """Base exception for pyhaul downloader errors."""


class ServerMisconfiguredError(HaulError):
    """The server violated protocol in a way that prevents safe download."""


class UnexpectedStatusError(HaulError):
    """Server returned a non-download HTTP status code.

    Carries structured metadata so callers can branch on status codes,
    inspect headers (e.g. ``Retry-After``), or log the server's reason
    phrase without parsing the message string.
    """

    status_code: int
    """The HTTP status code (e.g. 429, 503, 404)."""

    headers: TransportHeaders
    """Response headers — immutable, case-insensitive."""

    reason: str
    """Human-readable summary, e.g. ``"unexpected HTTP 429"``."""

    def __init__(
        self,
        status_code: int,
        headers: TransportHeaders,
        reason: str = "",
    ) -> None:
        self.status_code = status_code
        self.headers = headers
        self.reason = reason or f"unexpected HTTP {status_code}"
        super().__init__(self.reason)

    @property
    def is_transient(self) -> bool:
        """True when the status usually indicates a temporary condition worth retrying.

        Includes common overload / upstream / CDN signals (e.g. ``408``, ``425``,
        ``429``, ``5xx`` gateway and origin errors, and Cloudflare ``520``/``522``/``524``).
        For any HTTP 5xx without branching on this set, see :attr:`is_server_error`.
        """
        return self.status_code in _TRANSIENT_HTTP_STATUSES

    @property
    def is_server_error(self) -> bool:
        """True for HTTP 5xx responses (status ``500`` ≤ code ≤ ``599``)."""
        return self.status_code // 100 == 5  # noqa: PLR2004 -- RFC 9110 status class 5 (server error)

    @property
    def retry_after(self) -> str | None:
        """Raw ``Retry-After`` header value, if present (seconds or HTTP-date string)."""
        return self.headers.get("Retry-After")

    @property
    def retry_after_seconds(self) -> float | None:
        """Seconds until retry per ``Retry-After``, or ``None`` if absent/unparsable.

        Accepts ``delay-seconds`` (integer) and ``HTTP-date`` forms (RFC 9110 §10.2.3).
        For dates in the past, returns ``0.0``. Parsing only — pyhaul does not retry or cap sleeps.
        """
        return _retry_after_raw_to_seconds(self.retry_after)


class ContentRangeError(HaulError):
    """Content-Range header doesn't match the expected range boundaries."""


class DestinationError(HaulError):
    """The destination path cannot accommodate sidecar files (``.part``, ``.part.ctrl``)."""


class ControlFileError(HaulError):
    """The control file is corrupt, unreadable, or inconsistent with the part file."""


class PartialHaulError(HaulError):
    """Stream ended before the full resource was retrieved.

    Raised by ``haul()`` / ``haul_async()`` when the server closes
    the connection or the stream ends before all bytes arrive.  The
    ``.part`` and ``.part.ctrl`` files persist on disk; a subsequent
    call resumes from where this one stopped.

    Progress counters live in the :class:`HaulState` bag, not here.
    """

    def __init__(self, reason: str = "") -> None:
        super().__init__(reason)
        self.reason = reason


class HashBuilder:
    """Incremental SHA-256 block-level accumulator."""

    def __init__(self, block_size: int, initial_hashes: list[bytes] | None = None) -> None:
        self.block_size = block_size
        self.completed_hashes = initial_hashes or []
        self._current_hash = hashlib.sha256()
        self._block_pos = 0  # bytes into the current block

    @property
    def current_digest(self) -> bytes | None:
        """Return the digest of the current partial block, if any."""
        if self._block_pos > 0:
            return self._current_hash.digest()
        return None

    def update(self, data: bytes) -> list[bytes]:
        """Feed data. Returns any hashes completed during this update."""
        newly_completed: list[bytes] = []

        offset = 0
        to_process = len(data)

        while to_process > 0:
            remaining_in_block = self.block_size - self._block_pos
            take = min(to_process, remaining_in_block)

            self._current_hash.update(data[offset : offset + take])
            self._block_pos += take
            offset += take
            to_process -= take

            if self._block_pos == self.block_size:
                h = self._current_hash.digest()
                self.completed_hashes.append(h)
                newly_completed.append(h)
                self._current_hash = hashlib.sha256()
                self._block_pos = 0

        return newly_completed

    def finalize(self) -> str:
        """Finish the current block (if any) and return the tree hash."""
        if self._block_pos > 0:
            self.completed_hashes.append(self._current_hash.digest())
            self._block_pos = 0

        if not self.completed_hashes:
            return "empty-0"

        # Fingerprint: SHA-256 of all binary hashes concatenated
        h = hashlib.sha256()
        h.update(b"".join(self.completed_hashes))
        return f"{h.hexdigest()}-{len(self.completed_hashes)}"

    @staticmethod
    def hash_file(path: str | Path, block_size: int = 8 * 1024 * 1024) -> str:
        """Compute the tree hash of a complete file on disk."""
        hb = HashBuilder(block_size=block_size)
        with Path(path).open("rb") as f:
            while True:
                chunk = f.read(1 << 20)  # 1MB I/O chunks
                if not chunk:
                    break
                hb.update(chunk)
        return hb.finalize()
