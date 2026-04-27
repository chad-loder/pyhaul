"""Thread-safe, versioned checkpoint codecs and migration management.

This module follows a registry pattern for format versions.  All codecs are
stateless and thread-safe.  The Registry handles dispatching and transparent
migration from legacy formats (like the original JSON V3).
"""

from __future__ import annotations

import struct
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, unique
from typing import Final, Protocol, runtime_checkable

from pyhaul._types import ControlFileError, ETag, parse_etag

# --- Version Constants ---

V3_JSON: Final = 3
V4_BINARY: Final = 4
LATEST_VERSION: Final = V4_BINARY

_MIN_BINARY_SIZE: Final = 5
_RL_FIELD_SIZE: Final = 8


@unique
class Tag(IntEnum):
    """TLV tags for variable-length metadata in the binary format."""

    ETAG = 1
    RESOURCE_LENGTH = 2


# --- Domain Model ---


@dataclass(frozen=True, slots=True, kw_only=True)
class Checkpoint:
    """Version-agnostic download state. Codecs map this to/from bytes."""

    version: int
    start: int
    extent: int | None
    valid_length: int
    etag: ETag
    block_size: int
    hashes: list[bytes] = field(default_factory=list[bytes])
    resource_length: int | None = None


# --- Codec Interface ---


@runtime_checkable
class CheckpointCodec(Protocol):
    """Protocol for version-specific serialization logic."""

    @property
    def version(self) -> int:
        """The format version number this codec handles."""
        ...

    def encode(self, cp: Checkpoint) -> bytes:
        """Serialize a Checkpoint into raw bytes."""
        ...

    def decode(self, data: bytes) -> Checkpoint:
        """Parse raw bytes into a Checkpoint."""
        ...


# --- V4 Binary Implementation ---


class V4BinaryCodec:
    """Binary format: [Magic][Ver][Res][HdrSize][Cursor][BlkSize][Ext][Start][TLV...][Hashes...]."""

    version: int = V4_BINARY

    # Magic(4s), Ver(B), Reserved(B), HeaderSize(H), Cursor(Q), BlockSize(Q), Extent(Q), Start(Q)
    _CORE_FORMAT: Final = "<4sBBHQQQQ"
    _CORE_SIZE: Final = struct.calcsize(_CORE_FORMAT)
    _MAGIC: Final = b"HAUL"

    def encode(self, cp: Checkpoint) -> bytes:
        """Serialize Checkpoint to V4 binary format."""
        # 1. Build TLV Extensions
        extensions = bytearray()

        # ETag
        etag_bytes = cp.etag.encode("utf-8")
        extensions.extend(struct.pack("<BH", Tag.ETAG, len(etag_bytes)))
        extensions.extend(etag_bytes)

        # Resource Length
        if cp.resource_length is not None:
            extensions.extend(struct.pack("<BHQ", Tag.RESOURCE_LENGTH, _RL_FIELD_SIZE, cp.resource_length))

        header_size = self._CORE_SIZE + len(extensions)

        # 2. Pack Header
        core = struct.pack(
            self._CORE_FORMAT,
            self._MAGIC,
            self.version,
            0,  # Reserved
            header_size,
            cp.valid_length,
            cp.block_size,
            cp.extent or 0,
            cp.start,
        )

        # 3. Assemble full payload
        payload = bytearray(core)
        payload.extend(extensions)
        for h in cp.hashes:
            payload.extend(h)

        return bytes(payload)

    def decode(self, data: bytes) -> Checkpoint:
        """Parse V4 binary bytes into a Checkpoint."""
        if len(data) < self._CORE_SIZE:
            raise ControlFileError("file too small for binary header")

        magic, ver, _, h_size, cursor, b_size, extent, start = struct.unpack(self._CORE_FORMAT, data[: self._CORE_SIZE])

        if magic != self._MAGIC:
            raise ControlFileError(f"invalid magic bytes: {magic!r}")

        # Parse TLV extensions
        etag = ETag("")
        res_len = None

        ptr = self._CORE_SIZE
        while ptr < h_size:
            if ptr + 3 > len(data):
                break
            tag_val, v_len = struct.unpack("<BH", data[ptr : ptr + 3])
            ptr += 3

            val = data[ptr : ptr + v_len]
            ptr += v_len

            if tag_val == Tag.ETAG:
                etag = parse_etag(val.decode("utf-8"))
            elif tag_val == Tag.RESOURCE_LENGTH and v_len == _RL_FIELD_SIZE:
                res_len = struct.unpack("<Q", val)[0]

        # Remaining bytes are 32-byte hashes
        hashes_data = data[h_size:]
        if len(hashes_data) % 32 != 0:
            raise ControlFileError("corrupt hash payload: not a multiple of 32 bytes")

        hashes = [hashes_data[i : i + 32] for i in range(0, len(hashes_data), 32)]

        return Checkpoint(
            version=ver,
            start=start,
            extent=extent if extent > 0 else None,
            valid_length=cursor,
            etag=etag,
            block_size=b_size,
            hashes=hashes,
            resource_length=res_len,
        )


# --- Legacy V3 JSON Codec ---


class V3LegacyCodec:
    """Minimal shim for migrating from old JSON checkpoints."""

    version: int = V3_JSON

    def encode(self, cp: Checkpoint) -> bytes:
        """V3 is read-only."""
        raise NotImplementedError("V3 is read-only; use V4 for writing")

    def decode(self, data: bytes) -> Checkpoint:
        """Parse legacy JSON bytes into a modern Checkpoint."""
        import json

        try:
            obj = json.loads(data.decode("utf-8"))
            return Checkpoint(
                version=self.version,
                start=obj.get("start", 0),
                extent=obj.get("extent"),
                valid_length=obj.get("valid_length", 0),
                etag=parse_etag(str(obj.get("etag", ""))),
                block_size=8 * 1024 * 1024,  # Migrated files adopt the default
                hashes=[],
                resource_length=obj.get("resource_length"),
            )
        except Exception as e:
            raise ControlFileError(f"corrupt legacy JSON: {e}") from e


# --- Registry and Dispatch ---


class CheckpointRegistry:
    """Thread-safe dispatcher for checkpoint serialization."""

    def __init__(self, codecs: Mapping[int, CheckpointCodec]) -> None:
        self._codecs = dict(codecs)

    def load(self, data: bytes) -> Checkpoint:
        """Decode any supported version into a modern Checkpoint."""
        if not data:
            raise ControlFileError("checkpoint file is empty")

        # 1. Probe for Binary Magic
        if data.startswith(b"HAUL"):
            if len(data) < _MIN_BINARY_SIZE:
                raise ControlFileError("binary header truncated")
            version = data[4]
        # 2. Probe for JSON (Legacy V3)
        elif data.startswith(b"{"):
            version = V3_JSON
        else:
            raise ControlFileError("unrecognized checkpoint format")

        codec = self._codecs.get(version)
        if not codec:
            raise ControlFileError(f"unsupported checkpoint version: {version}")

        return codec.decode(data)

    def dump(self, cp: Checkpoint) -> bytes:
        """Serialize Checkpoint using the codec matching its version."""
        codec = self._codecs.get(cp.version)
        if not codec:
            raise ControlFileError(f"no codec registered for version {cp.version}")
        return codec.encode(cp)


# Global stateless registry instance
registry: Final = CheckpointRegistry(
    {
        V3_JSON: V3LegacyCodec(),
        V4_BINARY: V4BinaryCodec(),
    }
)
