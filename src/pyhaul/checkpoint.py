"""Thread-safe, versioned checkpoint encoding for the control file.

The registry holds one :class:`CheckpointCodec` per supported on-disk
version.  Today only *v1* (binary ``HAUL``) exists; the version byte
in the header is the literal ``1``.  A future *v2* would add another
codec and bump :data:`LATEST_VERSION`.
"""

from __future__ import annotations

import struct
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import IntEnum, unique
from typing import Final, Protocol, runtime_checkable

from pyhaul._types import ControlFileError, ETag, parse_etag

# On-disk / wire version (v1 is the only format today).
V1_BINARY: Final = 1
LATEST_VERSION: Final = V1_BINARY

_MIN_BINARY_SIZE: Final = 5
_REPORTED_LEN_U64_SIZE: Final = 8
_CRC_SIZE: Final = 4
_ALIGNMENT: Final = 8


@unique
class Tag(IntEnum):
    """TLV tags for variable-length metadata in the binary format."""

    ETAG = 1
    REPORTED_LENGTH = 2
    TAIL_HASH = 3


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
    tail_hash: bytes | None = None
    reported_length: int | None = None


# --- Codec interface ---


@runtime_checkable
class CheckpointCodec(Protocol):
    """Stateless, version-tagged (de)serialization. Subclass for each on-disk version."""

    @property
    def version(self) -> int:
        """The format version number in the binary header (byte index 4 of ``HAUL`` files)."""
        ...

    def encode(self, cp: Checkpoint) -> bytes:
        """Serialize a checkpoint into the wire format for :attr:`version`."""
        ...

    def decode(self, data: bytes) -> Checkpoint:
        """Parse bytes for this codec's format into a :class:`Checkpoint`."""
        ...


# --- v1 (binary) ---


class V1BinaryCodec:
    """Framed TLVs and 8-byte hash payload alignment."""

    version: int = V1_BINARY

    # Magic(4s), Ver(B), Reserved(B), HeaderSize(H), Cursor(Q), BlockSize(Q), Extent(Q), Start(Q)
    _CORE_FORMAT: Final = "<4sBBHQQQQ"
    _CORE_SIZE: Final = struct.calcsize(_CORE_FORMAT)
    _MAGIC: Final = b"HAUL"

    def _pack_tlv(self, tag: Tag, value: bytes) -> bytes:
        """Pack a TLV block with a trailing CRC32."""
        header = struct.pack("<BH", tag, len(value))
        payload = header + value
        crc = zlib.crc32(payload)
        return payload + struct.pack("<I", crc)

    def encode(self, cp: Checkpoint) -> bytes:
        """Serialize checkpoint to bytes."""
        extensions = bytearray()

        if cp.etag:
            extensions.extend(self._pack_tlv(Tag.ETAG, cp.etag.encode("utf-8")))

        if cp.reported_length is not None:
            extensions.extend(self._pack_tlv(Tag.REPORTED_LENGTH, struct.pack("<Q", cp.reported_length)))

        if cp.tail_hash:
            extensions.extend(self._pack_tlv(Tag.TAIL_HASH, cp.tail_hash))

        unaligned_header_size = self._CORE_SIZE + len(extensions)
        padding_needed = (_ALIGNMENT - (unaligned_header_size % _ALIGNMENT)) % _ALIGNMENT
        extensions.extend(b"\x00" * padding_needed)

        if cp.tail_hash:
            extensions.extend(struct.pack("<BH", Tag.TAIL_HASH, len(cp.tail_hash)))
            extensions.extend(cp.tail_hash)

        header_size = self._CORE_SIZE + len(extensions)

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

        payload = bytearray(core)
        payload.extend(extensions)
        for h in cp.hashes:
            payload.extend(h)

        return bytes(payload)

    def decode(self, data: bytes) -> Checkpoint:
        """Parse bytes in this format into a :class:`Checkpoint`."""
        if len(data) < self._CORE_SIZE:
            raise ControlFileError("file too small for binary header")

        magic, ver, _, h_size, cursor, b_size, extent, start = struct.unpack(self._CORE_FORMAT, data[: self._CORE_SIZE])

        if magic != self._MAGIC:
            raise ControlFileError(f"invalid magic bytes: {magic!r}")

        etag, reported_len, tail_hash = self._parse_extensions(data, h_size)

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
            tail_hash=tail_hash,
            reported_length=reported_len,
        )

    def _parse_extensions(self, data: bytes, header_size: int) -> tuple[ETag, int | None, bytes | None]:
        etag = ETag("")
        reported_len: int | None = None
        tail_hash: bytes | None = None

        ptr = self._CORE_SIZE
        while ptr < header_size:
            if data[ptr] == 0:
                break

            if ptr + 3 > header_size:
                raise ControlFileError("truncated TLV header")

            tag_val, v_len = struct.unpack("<BH", data[ptr : ptr + 3])
            chunk_total_len = 3 + v_len + _CRC_SIZE

            if ptr + chunk_total_len > header_size:
                raise ControlFileError(f"TLV {tag_val} length {v_len} exceeds header bounds")

            chunk_data = data[ptr : ptr + 3 + v_len]
            stored_crc = struct.unpack("<I", data[ptr + 3 + v_len : ptr + chunk_total_len])[0]

            if zlib.crc32(chunk_data) != stored_crc:
                raise ControlFileError(f"CRC mismatch in TLV Tag {tag_val}")

            value = data[ptr + 3 : ptr + 3 + v_len]

            if tag_val == Tag.ETAG:
                etag = parse_etag(value.decode("utf-8"))
            elif tag_val == Tag.REPORTED_LENGTH and v_len == _REPORTED_LEN_U64_SIZE:
                reported_len = struct.unpack("<Q", value)[0]
            elif tag_val == Tag.TAIL_HASH:
                tail_hash = value

            ptr += chunk_total_len

        return etag, reported_len, tail_hash


# --- Registry ---


class CheckpointRegistry:
    """Dispatch :meth:`load` / :meth:`dump` to the codec for each format version."""

    def __init__(self, codecs: Mapping[int, CheckpointCodec]) -> None:
        self._codecs = dict(codecs)
        for v, c in self._codecs.items():
            if c.version != v:
                msg = f"codec map mismatch: key {v} != codec.version {c.version}"
                raise ValueError(msg)

    def load(self, data: bytes) -> Checkpoint:
        """Decode raw control-file bytes to a :class:`Checkpoint` using the known codec version."""
        if not data:
            raise ControlFileError("checkpoint file is empty")
        if not data.startswith(b"HAUL"):
            raise ControlFileError("unrecognized checkpoint format")
        if len(data) < _MIN_BINARY_SIZE:
            raise ControlFileError("binary header truncated")
        version = data[4]
        codec = self._codecs.get(version)
        if codec is None:
            raise ControlFileError(f"unsupported checkpoint version: {version}")
        return codec.decode(data)

    def dump(self, cp: Checkpoint) -> bytes:
        """Serialize a :class:`Checkpoint` to the wire format of :data:`LATEST_VERSION`."""
        if cp.version != LATEST_VERSION:
            cp = Checkpoint(
                version=LATEST_VERSION,
                start=cp.start,
                extent=cp.extent,
                valid_length=cp.valid_length,
                etag=cp.etag,
                block_size=cp.block_size,
                hashes=cp.hashes,
                tail_hash=cp.tail_hash,
                reported_length=cp.reported_length,
            )
        codec = self._codecs.get(cp.version)
        if codec is None:
            raise ControlFileError(f"no codec registered for version {cp.version}")
        return codec.encode(cp)


registry: Final = CheckpointRegistry(
    {
        V1_BINARY: V1BinaryCodec(),
    }
)
