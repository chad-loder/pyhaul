"""Shared types and result containers for pyhaul."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import NewType
from urllib.parse import urlparse

ByteOffset = NewType("ByteOffset", int)
"""Absolute byte offset from the start of the download target."""

ByteLength = NewType("ByteLength", int)
"""Byte count — length of a range, piece, or buffer."""

Url = NewType("Url", str)
"""An http(s) URL that has passed :func:`parse_url`."""

ETag = NewType("ETag", str)
"""An HTTP ETag value (possibly empty) that has passed :func:`parse_etag`.

Stored verbatim as the server sent it — quotes and optional ``W/`` prefix
included — so that it can be echoed back in ``If-Range`` / ``If-Match``
byte-for-byte. An empty string means "no ETag for this resource".
"""

EMPTY_ETAG: ETag = ETag("")
"""Shared "no ETag" singleton. Used as the default for signatures where
inline ``ETag("")`` would trip B008."""

EMPTY_URL: Url = Url("")
"""Shared "no URL yet" sentinel for components constructed before the
downloader has a parsed URL to hand them (e.g. tests, trackers built
for size bookkeeping only)."""

_URL_SCHEMES: frozenset[str] = frozenset({"http", "https"})
_MIN_QUOTED_ETAG_LEN = 2  # two DQUOTEs wrap any valid entity-tag


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


def parse_etag(raw: object) -> ETag:
    """Normalize a raw ETag header value into an :data:`ETag`.

    Strips surrounding whitespace. Empty input becomes ``ETag("")`` to
    represent "no ETag". Values that clearly aren't well-formed
    entity-tags (per RFC 7232: optional ``W/`` then a quoted string) are
    also treated as absent, since echoing them back in ``If-Range`` /
    ``If-Match`` would be unsafe. Non-string input raises
    :class:`TypeError`.
    """
    if not isinstance(raw, str):
        raise TypeError(f"etag must be str, got {type(raw).__name__}")
    stripped = raw.strip()
    if not stripped:
        return EMPTY_ETAG
    candidate = stripped.removeprefix("W/")
    if len(candidate) < _MIN_QUOTED_ETAG_LEN or not candidate.startswith('"') or not candidate.endswith('"'):
        return EMPTY_ETAG
    return ETag(stripped)


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
    """

    is_complete: bool = False
    bytes_read: int = 0
    valid_length: int = 0
    block_size: int = 8 * 1024 * 1024
    hashes: list[bytes] = field(default_factory=list[bytes])


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
        self._total_pos = sum(len(h) for h in self.completed_hashes) * block_size  # inaccurate if we resumed at 0
        # Actually, let's keep it simpler. We assume we are fed bytes sequentially
        # starting from some offset.
        self._block_pos = 0  # bytes into the current block

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
