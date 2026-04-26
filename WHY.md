# Why pyhaul exists

I wanted something that made resuming HTTP downloads easy. Not just
"add a Range header" easy — actually easy, where you call a function,
it either finishes or it doesn't, and in both cases the state on disk
is correct.

## Bring your own transport

You hack on different codebases — some are async, some are legacy
`requests` code, some use totally custom in-house HTTP stacks. They
all share the same abstractions, they just expose them differently.
`pyhaul` should work with any of them.

## Zero dependencies

The base `pyhaul` package is pure Python with zero required
dependencies. You can't always install a native binary like `aria2`
(as good as it is). The HTTP client adapters are optional extras you
install if you want them.

## Real sync and async

Not a fake async wrapper around sync code — a real async engine that
shares logic with the sync path but uses `async with` / `async for`
natively.

## Not a session manager, not a retry policy

`pyhaul` shouldn't initiate connections, create or own sessions, or
contain its own retry or threading logic. I wanted to use it in
contexts where sessions come out of a pool, where they're configured
over separate proxies, where app code has added custom authentication
headers. One `haul()` = one request. Retries are the caller's
concern.

## Not a weekend project

And it had to correctly handle the weird, real-world corner cases that
cause silent corruption. Whether that's a hardware crash, a flaky
connection that drops every few seconds, your container getting
OOM-killed, or a CDN edge server doing something unexpected with
compression or range boundaries.

When you first look at "resumable HTTP downloads," it seems fairly
simple. Then you hit the first bug. You fix it. Then files are
sometimes truncated. Oh, this particular client library
auto-negotiates gzip compression — how does that interact with byte
ranges? What about chunked transfer encoding? What about servers that
just ignore your Range header entirely? What about a server that was
serving version A of a file when you started, and version B when you
resume two days later?

And then there's the streaming layer itself. You can't use your HTTP
library's convenient high-level body methods —
`response.iter_content()` in requests, `response.iter_bytes()` in
httpx — because those transparently decompress the body. Even when
the server respects `Accept-Encoding: identity`, the decompressed
byte count won't match the `Content-Range` or `Content-Length` values
the server sent, so your checkpoint cursor drifts and your next
resume starts from the wrong offset. Instead you have to drop down
to the raw streaming layer: `response.raw.stream(decode_content=False)`
for requests/urllib3, `response.iter_raw()` / `response.aiter_raw()`
for httpx and niquests. These give you post-transfer-encoding,
pre-content-encoding bytes — the exact bytes the server framed. But
now you're responsible for understanding that "raw" doesn't mean
"straight off the socket": on a persistent HTTP/1.1 connection,
multiple responses share the same TCP stream, delimited by
`Content-Length` or chunked transfer encoding. The library still
handles that framing for you in the raw layer, but if you get the
byte accounting wrong — or if the server lies about `Content-Length`,
or a proxy silently re-compresses the stream despite `no-transform` —
you can overread into the next response on the connection, corrupting
both it and your download. These are the kinds of edges that only
show up in production, on somebody else's CDN, at 3 AM.

## How existing tools handle resume

I looked at curl, wget, and aria2 — mature, battle-tested tools — to
see how they approach these problems. Verified against their source
code, not their documentation.

| | curl | wget | aria2 | pyhaul |
| --- | :---: | :---: | :---: | :---: |
| ETag / `If-Range` on resume | ✗ | ✗ | ✗ | ✓ |
| Graceful 200 recovery | ✗ | ✗ | ✗ | ✓ |
| Range-safe compression | ✗ | partial | ✗ | ✓ |
| `Cache-Control: no-transform` | ✗ | ✗ | ✗ | ✓ |
| Data flushed before checkpoint | ✗ | ✗ | partial | ✓ |
| Atomic file completion | ✗ | ✗ | ✗ | ✓ |
| Pure Python / embeddable | ✗ | ✗ | ✗ | ✓ |

**Notes on the table above:**

- **curl + 200 recovery:** returns `CURLE_RANGE_ERROR` and stops.
- **wget + 200 recovery:** skips leading bytes and appends. Works if
  the resource hasn't changed; silently corrupts if it has, because
  there's no ETag check.
- **aria2 + 200 recovery:** aborts with `CANNOT_RESUME`.
- **wget + compression:** sends `Accept-Encoding: identity` (good),
  but doesn't send `Cache-Control: no-transform`, so an intermediate
  proxy or CDN can still re-compress the response.
- **aria2 + flush ordering:** calls `fsync` on close/save, but
  doesn't enforce strict data-before-checkpoint ordering.

None of these tools send `If-Range` with an ETag on resume. That
means if the resource at a URL changes between your first attempt and
your retry, all three will either fail or silently produce a file
that's half version A and half version B. `pyhaul` records the ETag
the server sent on the first request and sends it back via `If-Range`
on every subsequent attempt. If the ETag still matches, the server
appends. If it doesn't, the server returns the full new resource from
byte 0 — no corruption.

## The details

### Range requests that might be ignored

The server can honor the range (`206 Partial Content`), decide to
resend the whole thing (`200 OK`), or reject it as out of bounds
(`416`). All three happen in the wild. `pyhaul` handles each outcome
without caller intervention — including rewinding the on-disk cursor
when the server signals that prior progress is invalid (e.g. a `200`
to a resume request), so fresh bytes are never spliced onto a stale
prefix.

### Mid-download resource changes

Suppose your network drops and you retry 48 hours later. By then the
object at that URL may have changed. `pyhaul` records the
[ETag](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/ETag)
the server sent — a short fingerprint of that exact representation —
and on resume sends it back via `If-Range`. If the current ETag still
matches, the server splices the next bytes onto the ones already on
disk. If it doesn't, the server returns the new body from byte 0
instead of corrupting the file with a half-old, half-new mix.

### Lengths nobody knows in advance

Some servers send `Content-Length: 0`, omit the header, or use chunked
transfer encoding because they don't know (or don't want to compute)
the full length up front. Chunked TE is HTTP's intended mechanism for
exactly this: length-prefixed chunks ending in a zero-length sentinel
that means "that's the end." The cost is that neither side knows the
total until the last chunk arrives — easy to mishandle if your code
assumes `Content-Length` is always present. `pyhaul` streams to the
sentinel and treats the final byte count as authoritative.

### Compression that interacts with ranges

HTTP responses can come back compressed (gzip, brotli, zstd), and
compression isn't guaranteed to produce the same output bytes for the
same input — two different servers, or even the same server on
different days, can compress the same asset slightly differently. That
means asking for "bytes 1,000,000 onward" inside a compressed stream
isn't a stable question; what you actually want is bytes 1,000,000
onward of the original, *uncompressed* file. Worse, some CDN edges
store **only** a compressed copy of an asset, so a careless resume
request can come back gzipped even when the bytes already saved on
disk are uncompressed — concatenate the two and the file is silently
corrupted from that point forward. `pyhaul` defends against this with
request-level headers that force an uncompressed byte stream end to
end:
[`Accept-Encoding: identity`](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Accept-Encoding)
asks the origin server not to compress; `Cache-Control: no-transform`
([RFC 9111 §5.2.2.6](https://www.rfc-editor.org/rfc/rfc9111#section-5.2.2.6))
forbids every proxy and CDN in the chain from re-compressing on the
way back; and `Range` / `If-Range` are always set by `pyhaul` itself
even if the caller tried to override them.

### Crash safety

The order of writes matters. `pyhaul` flushes the `.part` data with
`fdatasync` *before* updating the checkpoint, writes the checkpoint
atomically (write-to-tmp, `fsync`, rename), and finalizes the download
by atomically renaming `.part` onto the destination path. Sidecar
files are constructed as siblings of the destination, so every rename
is on the same filesystem and truly atomic. Data bytes are never marked
committed until they are on disk; the destination path is only ever
populated by that final rename.

The payoff is a set of guarantees you can verify by inspecting a
directory with no other context:

- **If the destination file exists at the requested path, it is
  complete and uncorrupted.** There is no in-between state where a
  partially-written file sits at the final name.
- **If `.part` and `.part.ctrl` exist instead, the first
  `valid_length` bytes of `.part` are durable and correct.** The rest
  is junk from preallocation or unflushed writes that gets trimmed on
  completion. A subsequent `haul()` call against the same destination
  picks up from byte `valid_length`.

These invariants sound obvious. Enforcing them through process kills,
OS-dependent sparse-allocation semantics, and writes that linger in
kernel buffers is where most home-grown downloaders silently lose
data. `pyhaul` handles the filesystem primitives correctly on Linux,
macOS, and Windows.
