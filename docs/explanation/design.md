# Design & Architecture

**See also:** [Why pyhaul Exists](why.md) · [Control File Spec](../reference/spec.md)

## How resume works

1. `haul()` reads `.part.ctrl` (if it exists) to recover the cursor
   position and stored ETag.
2. Sends `Range: bytes=<cursor>-` with `If-Range: <etag>` (omitted
   when no ETag is stored).
3. **206 Partial Content** — server honors the range. Stream appends
   from the cursor.
4. **200 OK** — server ignores the range (resource changed, or server
   doesn't support ranges). Cursor resets to 0; stream overwrites from
   the beginning.
5. **416 Range Not Satisfiable** — the server's reported total matches
   the cursor (already complete) or the representation shrank (checkpoint
   reset, next call restarts).

The engine handles each case without caller intervention.

## Download lifecycle

- **In-flight.** Two sidecar files: `<dest>.part` (data) and
  `<dest>.part.ctrl` (binary checkpoint with cursor position, ETag,
  block-level hashes, etc.)
- **Interrupted.** Both files remain. Next `haul()` resumes
  automatically.
- **Complete.** `.part` is atomically renamed to `dest`; `.ctrl` is
  deleted. SHA-256 is computed and returned.
- **Discard.** Delete both `.part` and `.part.ctrl` to force a
  restart.

## Sidecar file naming and preflight

All sidecar files are derived from the caller-provided destination path by
appending suffixes:

| File | Derivation | Purpose |
| --- | --- | --- |
| `<dest>.part` | `dest.with_suffix(dest.suffix + ".part")` | Partial data |
| `<dest>.part.ctrl` | `<dest>.part` + `.ctrl` | Binary checkpoint |
| `<dest>.part.ctrl.tmp` | `<dest>.part.ctrl` + `.tmp` | Ephemeral temp for atomic checkpoint writes |

For a destination `data/2024-vol01.csv.gz`, the files are
`data/2024-vol01.csv.gz.part`, `data/2024-vol01.csv.gz.part.ctrl`, and
(transiently) `data/2024-vol01.csv.gz.part.ctrl.tmp`.

### Preflight path validation

Before making any HTTP request, `haul()` checks that the destination path plus
the longest sidecar suffix (`.part.ctrl`) fits within the filesystem's limits.
This catches two independent constraints:

1. **Filename component length** — `NAME_MAX` for the target directory (255
   bytes on ext4/APFS/XFS, 255 UTF-16 code units on NTFS). On macOS, filenames
   are measured in NFD-normalized UTF-8 bytes to match what APFS actually
   stores.
2. **Full path length** — `PATH_MAX` for the target filesystem. On Windows,
   paths beyond 250 characters automatically get the `\\?\` extended-length
   prefix, raising the effective limit to 32,767.

If either check fails, `haul()` raises [`DestinationError`][pyhaul._types.DestinationError] immediately — before
opening any network connection or creating any files.

## Transport adapter architecture

`haul()` and `haul_async()` auto-detect supported client types and
wrap them internally via a registry of adapter factories. The adapter
protocol is deliberately minimal: a single `stream_get()` context manager.

This design means:

- **Your session is borrowed, not owned.** pyhaul never creates,
  configures, or closes sessions. Auth headers, proxy config, connection
  pools — everything passes through unchanged.
- **Transport errors propagate unwrapped.** `httpx.ReadTimeout` stays
  `httpx.ReadTimeout`. You catch the types you already know.
- **Custom transports are easy.** Implement [`TransportSession`][pyhaul.transport.protocols.TransportSession] (one method)
  and register it. See [Writing a Custom Adapter](../guides/custom-transport.md).

For the supported client types and per-adapter notes, see
[HTTP Client Adapters](../guides/adapters.md).

## Exception design

pyhaul's exception hierarchy separates retryable from non-retryable errors:

- [`PartialHaulError`][pyhaul._types.PartialHaulError] — the stream ended early, but progress is saved. Retry.
- [`ServerMisconfiguredError`][pyhaul._types.ServerMisconfiguredError] — the server did something that makes safe resume
  impossible. Don't retry.
- [`ControlFileError`][pyhaul._types.ControlFileError] — the checkpoint file is corrupt. Auto-recovers on next
  attempt.

Transport errors from the underlying HTTP library pass through unwrapped to
preserve the caller's existing error-handling code.

For the full exception table, see [Exceptions Reference](../reference/exceptions.md).

## Crash safety

The order of writes matters. pyhaul uses a strict three-phase
sequence throughout:

1. **Data first.** Write bytes to the `.part` file, then call
   `fdatasync` to flush them to durable storage.
2. **Checkpoint second.** Serialize the new checkpoint, write it to
   `<dest>.part.ctrl.tmp`, `fsync` the temp file, then `rename` it
   over `<dest>.part.ctrl`. Because `rename` is atomic on POSIX (and
   `replace` on Windows), the checkpoint on disk is always either the
   old version or the new version — never a half-written mix.
3. **Finalization last.** On completion, atomically rename `.part` to
   the destination path, then delete `.part.ctrl`.

### Why atomic rename

The alternative — writing directly to the destination file — leaves a
window where a crash produces a partial file at the final path.
Readers of that file have no way to distinguish "complete" from
"half-written." The `rename` syscall is the standard POSIX mechanism
for atomic file replacement: the kernel updates a single directory
entry, so the operation either fully succeeds or has no effect. There
is no intermediate state visible to other processes.

### Why same-volume matters

`rename()` is only atomic when source and destination are on the same
filesystem. A cross-filesystem "rename" is actually copy-then-delete,
which is neither atomic nor crash-safe. pyhaul ensures this by
constructing every sidecar file as a **sibling of the destination** —
the `.part`, `.part.ctrl`, and `.part.ctrl.tmp` files all live in the
same directory as the final destination. Since they share a directory,
they are guaranteed to share a filesystem, and every rename in the
pipeline is truly atomic.

This is also why pyhaul does not use `tempfile.mkstemp()` or the
system temp directory (`/tmp`, `%TEMP%`). Those may reside on a
different filesystem or partition than the destination, which would
break the atomicity guarantee on the final rename.

### The guarantees

The payoff is a set of invariants you can verify by inspecting a
directory with no other context:

- **If the destination file exists at the requested path, it is
  complete and uncorrupted.** There is no in-between state where a
  partially-written file sits at the final name.
- **If `.part` and `.part.ctrl` exist instead, the first
  `valid_length` bytes of `.part` are durable and correct.** The rest
  is junk from preallocation or unflushed writes that gets trimmed on
  completion. A subsequent `haul()` call against the same destination
  picks up from byte `valid_length`.

## Control file structure

The `.part.ctrl` file is a compact binary checkpoint. It packs everything
pyhaul needs to resume into a small, flat structure:

- **40-byte fixed header** — magic bytes (`HAUL`), format version, cursor
  position, block size, extent (total download size if known), and start
  offset.
- **CRC-protected TLV extensions** — variable-length metadata: the server's
  ETag (for change detection on resume), server-reported total length (if
  known), and a tail hash (SHA-256 of the current partial block). Each
  TLV chunk includes a CRC32 to detect corruption in the checkpoint itself.
- **Hash payload** — a flat sequence of 32-byte SHA-256 digests, one per
  completed 8 MiB block.

The full binary format is specified in the
[Control File Spec](../reference/spec.md).

### Why block-level hashes

The naive approach to resume validation is: re-read the entire `.part` file,
hash it, and compare against a stored hash. For small files that's fine. For
a 50 GB file over a flaky satellite link where `haul()` gets called hundreds
of times, re-reading and re-hashing 50 GB on every resume attempt would
dominate the total download time.

Block-level hashing solves this. pyhaul hashes each 8 MiB block
independently as bytes stream in, and stores the completed block hashes in
the control file. On resume, only the **last partial block** (at most 8 MiB)
needs to be re-read and verified against its stored tail hash. All
previously completed blocks are trusted via their stored digests. This makes
resume validation O(block_size), not O(file_size).

On completion, the final file hash is computed as
`SHA-256(concatenated block hashes)`, formatted as `<hex>-<count>`. This is
a tree hash — the digest of digests — not a flat hash of the file content.
It can be computed incrementally without ever holding the entire file in
memory or re-reading it from disk.

### Control file size overhead

Even with a hash per block, the control file stays small. The 8 MiB block
size means one 32-byte hash per 8 MiB of download data — a ratio of
roughly 0.0004%:

| Part file | Blocks | Ctrl file size |
| --- | ---: | ---: |
| 100 MB | 12 | 474 B |
| 1 GB | 120 | 3.8 KiB |
| 10 GB | 1,193 | 37 KiB |
| 100 GB | 11,921 | 373 KiB |
| 1 TB | 119,210 | 3.6 MiB |

A 100 GB download produces a checkpoint under 400 KiB. The control file is
negligible relative to the data it describes.

### Why SHA-256 and not BLAKE3

A tree hash like [BLAKE3](https://github.com/BLAKE3-team/BLAKE3) would be a
more natural fit here — BLAKE3 is inherently a Merkle tree, so block-level
incremental hashing is built into the algorithm rather than layered on top. It
would also be faster (BLAKE3 uses SIMD and parallelism internally).

However, pyhaul is a **zero-dependency pure-Python library**. `hashlib.sha256`
is in the standard library on every Python installation; BLAKE3 would require
a compiled C/Rust extension (`blake3` on PyPI). Since the hashing overhead is
small relative to network I/O and disk writes, SHA-256's performance is
adequate, and the manual block-level tree construction achieves the same
incremental-verification goal that BLAKE3 would provide natively.
