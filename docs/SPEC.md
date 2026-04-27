# pyhaul Specification v1.0

## Status of this Memo

This document specifies the binary format and operational rules for the
pyhaul resumable downloader. It is intended for implementers of the
pyhaul protocol and compatible tools.

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

## 3. Control File Binary Format (v4)

All multi-byte integers are encoded in Little-Endian byte order.

### 3.1. Header Structure

```text
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                      Magic (b"HAUL")                          |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|  Version (4)  |  Reserved (0) |         HeaderSize            |
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
|     TLV Extensions ... (Variable Length)                      |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|     Hashes Payload ... (32-byte SHA-256 blocks)               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

### 3.2. Field Definitions

- **Magic (4 bytes):** The literal ASCII string `b"HAUL"`.
- **Version (1 byte):** Unsigned integer. Currently `4`.
- **Reserved (1 byte):** Must be `0`.
- **HeaderSize (2 bytes):** Total offset in bytes from the start of the file to the beginning of the Hashes Payload.
- **Cursor (8 bytes):** The number of valid bytes currently in the `.part` file relative to the `Start` offset.
- **BlockSize (8 bytes):** The fixed size of each hashing block (default 8MiB).
- **Extent (8 bytes):** The total expected size of the resource (or `0` if unknown).
- **Start (8 bytes):** The byte offset in the remote resource where this download begins.

### 3.3. TLV Extensions

Variable-length metadata is stored as Tag-Length-Value (TLV) blocks:

```text
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|      Tag      |     Length    |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|     Value ... (Variable)      |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

- **Tag (1 byte):** Field identifier.
- **Length (2 bytes):** Size of the Value field in bytes.
- **Value (N bytes):** Field data.

Supported Tags:

- `1`: ETag (UTF-8 string)
- `2`: Resource Length (64-bit unsigned integer)
- `3`: Tail Hash (32-byte SHA-256 binary digest)

### 3.4. Hashes Payload

A contiguous sequence of raw 32-byte SHA-256 digests. Each hash corresponds to
a completed `BlockSize` chunk of data. The number of hashes is implicitly
calculated as `(FileSize - HeaderSize) / 32`.

## 4. Operational Rules

### 4.1. Write Sequencing (Crash Safety)

To maintain the invariant that the Control File never "lies" about the state of the data on disk, implementers MUST follow this sequence:

1. Write data to the `.part` file.
2. Perform an **`fdatasync`** (or `fsync`) on the `.part` file descriptor.
3. Prepare the new Control File content.
4. Perform an **Atomic Write** of the Control File:
   - Write to a `.tmp` sidecar.
   - `fsync` the `.tmp` file.
   - `rename` (or `replace`) the `.tmp` over the existing `.ctrl` file.

### 4.2. Resume Validation

When resuming a download, the engine MUST perform the following validations before appending network data:

1. **ETag Match:** If the server provides an ETag on the `206 Partial Content` response, it MUST match the ETag stored in the checkpoint.
2. **Range Alignment:** The server's `Content-Range` start MUST match the `Start + Cursor` position.
3. **Tail Integrity:**
   - Calculate the length of the partial tail: `TailLen = Cursor % BlockSize`.
   - If `TailLen > 0`:
     - Re-read `TailLen` bytes from the `.part` file starting at the last block boundary.
     - Compute the SHA-256 of these bytes.
     - Verify against the `TailHash` in the Control File.
     - If mismatch, raise a `ControlFileError`.

### 4.3. Finalization

Upon reaching the `Extent`:

1. Verify the total length of the `.part` file.
2. Truncate any junk data past the `Cursor`.
3. Rename `.part` to the final destination path.
4. Unlink the `.ctrl` file.
5. Compute and return the **Tree Hash Fingerprint**:
   - `SHA256(Concatenated Block Hashes) + "-" + NumBlocks`.
