# Design

**See also:** [Specification (on-disk format)](SPEC.md) · [Why pyhaul exists](WHY.md) · [README](../README.md)

## Exceptions

| Exception | When | Retryable? |
| --- | --- | --- |
| `PartialHaulError` | Stream ended before all bytes arrived. | Yes — call `haul()` again |
| `UnexpectedStatusError` | Server returned a non-download status (408, 429, 5xx, 404, …). | Lets caller decide — check `exc.is_transient` / `exc.is_server_error` |
| `ServerMisconfiguredError` | Server violated HTTP in a way that prevents safe resume. | No |
| `ContentRangeError` | 206 Content-Range doesn't match the requested range. | Often yes |
| `ControlFileError` | `.part.ctrl` is corrupt or version-mismatched. Discarded on next attempt. | Auto-recovers |
| `DestinationError` | Destination path can't accommodate sidecar files. | No |
| *(native)* | Transport errors (`httpx.ReadTimeout`, `requests.ConnectionError`, etc.) pass through unwrapped — catch the types you already know. | Usually yes |

## Transport adapters

`haul()` and `haul_sync()` auto-detect these client types and
wrap them internally:

| Extra | Client | Async | Notes |
| --- | --- | --- | --- |
| `niquests` | `niquests.Session` / `niquests.AsyncSession` | Yes | HTTP/2+3 |
| `httpx` | `httpx.Client` / `httpx.AsyncClient` | Yes | Full async support |
| `requests` | `requests.Session` | No | Sync only |
| `urllib3` | `urllib3.PoolManager` | No | Minimal dep; sync only |

If you need a custom transport (different HTTP library, mock for
testing, etc.), implement the `TransportSession` protocol: a single
`stream_get()` context manager that yields a response with
`.status_code`, `.headers`, and `.iter_raw_bytes()`. See
`pyhaul.transport.protocols` for the full contract.

## Lifecycle

- **In-flight.** Two sidecar files: `<dest>.part` (data) and
  `<dest>.part.ctrl` (binary checkpoint with cursor position, ETag,
  block-level hashes, etc.)
- **Interrupted.** Both files remain. Next `haul()` resumes
  automatically.
- **Complete.** `.part` is atomically renamed to `dest`; `.ctrl` is
  deleted. SHA-256 is computed and returned.
- **Discard.** Delete both `.part` and `.part.ctrl` to force a
  restart.

## How resume works

1. `haul()` reads `.part.ctrl` (if it exists) to recover the cursor
   position and stored ETag.
2. Sends `Range: bytes=<cursor>-` with `If-Range: <etag>` when a **strong**
   ETag is stored — omitted when there is no ETag **or** when only a **weak**
   ETag is available (weak validators are not used for byte-range preconditioning).
3. **206 Partial Content** — server honors the range. Stream appends
   from the cursor.
4. **200 OK** — server ignores the range (resource changed, or server
   doesn't support ranges). Cursor resets to 0; stream overwrites from
   the beginning.
5. **416 Range Not Satisfiable** — the server’s reported total matches
   the cursor (already complete) or the representation shrank (checkpoint
   reset, next call restarts).

The engine handles each case without caller intervention.
