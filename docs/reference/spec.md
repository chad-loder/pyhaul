# pyhaul Control File Format Specification, Version 1.0

This specification defines **version 1** of the on-disk control file: the
one-byte `Version` field in the header is the unsigned value `1` (octet
`0x01`). It specifies
binary layout, TLV metadata, and operational requirements for the pyhaul
resumable downloader. The intended audience is implementers and authors of
compatible tools.

## 1. Introduction

pyhaul is a cursor-based, single-range HTTP downloader designed for
crash-safe, integrity-verified resumes. It uses two sidecar files alongside
the destination file to maintain state and verify the integrity of local
data before appending new network bytes.

## 2. File Artifacts

For a destination file `dest.bin`, the following artifacts are managed:

- `dest.bin.part`: The partial data downloaded so far.
- `dest.bin.part.ctrl`: The binary checkpoint (the "Control File").
- `dest.bin`: The final, verified product (only exists upon success).

## 3. Control File Binary Format (v1)

All multi-byte integers are encoded in Little-Endian byte order.

### 3.1. Header Structure

The control file begins with a 40-byte fixed core header, followed by variable-length
framed TLV extensions, null padding for alignment, and finally the hash payload.

```text
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                      Magic (b"HAUL")                          |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|  Ver. (=1)    |  Reserved (0) |         HeaderSize            |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
+                         Cursor (64-bit)                       +
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
+                        BlockSize (64-bit)                     +
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
+                         Extent (64-bit)                       +
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
+                         Start (64-bit)                        +
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|     Framed TLV Extensions ... (Variable Length)               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|     Null Alignment Padding (0-7 bytes)                        |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|     Hashes Payload ... (Contiguous 32-byte SHA-256 blocks)    |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

### 3.2. Field Definitions

- **Magic (4 bytes):** The literal ASCII string `b"HAUL"`.
- **Version (1 byte):** Unsigned integer. Currently `1` (the only defined format).
- **Reserved (1 byte):** Must be `0`.
- **HeaderSize (2 bytes):** Total offset in bytes from the start of the file to
  the beginning of the Hashes Payload. This pointer allows parsers to jump to
  the payload even if they do not recognize all TLV extensions.
- **Cursor (8 bytes):** The number of valid bytes currently in the `.part` file
  relative to the `Start` offset.
- **BlockSize (8 bytes):** The fixed size of each hashing block (default 8MiB).
- **Extent (8 bytes):** The total size of the range being downloaded (or `0`
  if unknown).
- **Start (8 bytes):** The byte offset in the remote resource where this
  download begins.

### 3.3. Framed TLV Extensions

Metadata blocks are stored as CRC-verified chunks. This prevents bitrot in the
control file from causing the parser to misinterpret lengths or ETag strings.

```text
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|      Tag      |     Length    |     Value ... (Variable)      |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                      Chunk CRC32 (IEEE)                       |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

- **Tag (1 byte):** Field identifier.
- **Length (2 bytes):** Size of the Value field in bytes.
- **Value (N bytes):** Field data.
- **Chunk CRC32 (4 bytes):** IEEE CRC32 calculated over the bytes
  of `(Tag + Length + Value)`.

Supported Tags:

- `1`: ETag (UTF-8 string — RFC ``entity-tag`` text: ``"<opaque>"``,
  ``W/"<opaque>"``, or ``*``, as produced by
  :meth:`EntityTag.to_canonical <pyhaul.etag.EntityTag.to_canonical>`;
  legacy bare tokens still load via
  :meth:`EntityTag.from_canonical <pyhaul.etag.EntityTag.from_canonical>`)
- `2`: Reported length (64-bit unsigned integer — server-claimed full size)
- `3`: Tail Hash (32-byte SHA-256 binary digest of the current partial block)

### 3.4. Hashes Payload

A sequence of raw 32-byte SHA-256 digests. Each hash represents a completed
`BlockSize` chunk of data. The number of hashes is implicitly calculated as
`(FileSize - HeaderSize) / 32`. The payload is guaranteed to start on an
8-byte boundary (achieved via null padding after the TLV area).

## 4. Operational Rules

### 4.1. Write Sequencing (Crash Safety)

To maintain the invariant that the Control File never "lies" about the state
of the data on disk, implementers MUST follow this sequence:

1. Write data to the `.part` file.
2. Perform an **`fdatasync`** on the `.part` file descriptor.
3. Prepare the new Control File content.
4. Perform an **Atomic Write** of the Control File:
    - Write to a `.tmp` sidecar.
    - `fsync` the `.tmp` file.
    - `rename` (or `replace`) the `.tmp` over the existing `.ctrl` file.

### 4.2. Reading Strategy

A robust implementer SHOULD parse the file using this strategy:

1. Read the first 40 bytes and verify Magic/Version.
2. Extract `HeaderSize` to establish the payload boundary.
3. Iterate through the TLV area (from byte 40 to `HeaderSize`):
    - If the current byte is `0x00`, assume alignment padding and stop TLV
    parsing.
    - Read Tag and Length.
    - Calculate and verify the Chunk CRC32 before interpreting the Value.
    - If a Tag is unknown, skip it using its Length.
4. Jump to `HeaderSize` and read the remaining bytes as a flat list of
   32-byte hashes.

### 4.3. Resume Validation

Before appending network data, the engine MUST validate:

1. **ETag Match:** If a new ETag is received, it must match the one in the
   checkpoint.
2. **Tail Integrity:**
    - If `Cursor % BlockSize > 0`:
      - Re-read the partial tail from `.part`.
      - Compute its SHA-256 and verify against the `TailHash` TLV.
      - Raise `ControlFileError` on mismatch.

### 4.4. Finalization

Upon completion:

1. Truncate junk past `Cursor`.
2. Move `.part` to destination.
3. Unlink `.ctrl`.
4. Return Tree Hash Fingerprint: `SHA256(Concatenated Hashes) + "-" + Count`.
