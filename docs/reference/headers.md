# TransportHeaders

Immutable, case-insensitive, multi-value HTTP headers used throughout the transport
layer. Most callers encounter them as **response** metadata:
[`UnexpectedStatusError`][pyhaul._types.UnexpectedStatusError] carries them, and every
[`TransportResponse`][pyhaul.transport.protocols.TransportResponse] exposes normalized
response headers.

On the **request** path, pyhaul also represents the merged outbound header set as
`TransportHeaders`: user headers plus structural defaults, then the result of
[`prepare_headers()`][pyhaul.transport.protocols.TransportSession.prepare_headers] before
your adapter's HTTP client sees them. You rarely construct request-side instances yourself;
[`haul()`][pyhaul.engine.haul] / [`haul_async()`][pyhaul.async_engine.haul_async] accept an
optional plain mapping for extras.

## Quick usage

```python
from pyhaul import UnexpectedStatusError

try:
    result = haul(url, client, dest="file.bin")
except UnexpectedStatusError as exc:
    h = exc.headers

    h["Content-Type"]              # first value or KeyError
    h.get("Retry-After")           # first value or None
    h.get("Retry-After", "60")     # first value or default
    "etag" in h                    # case-insensitive membership
    len(h)                         # number of unique header names

    h.get_all("Set-Cookie")        # all values in order → tuple[str, ...]

    merged = h | {"X-Extra": "v"}  # merge → new TransportHeaders
    new = h.replace("Accept", "application/json")  # functional update
```

Because headers are immutable and hashable, they are safe to attach to
exceptions, cache, or pass across threads.

## Sensitive header redaction

`repr()` and `to_safe_dict()` automatically replace values for
`Authorization`, `Proxy-Authorization`, `Cookie`, `Set-Cookie`, and
`X-API-Key` with a fixed-length `[redacted]` placeholder:

```python
from pyhaul.transport._headers import TransportHeaders

h = TransportHeaders.from_pairs([
    ("Content-Type", "text/html"),
    ("Authorization", "Bearer sk-secret-token"),
])

repr(h)
# "TransportHeaders({'content-type': 'text/html', 'authorization': '[redacted]'})"

h.to_safe_dict()
# {'content-type': 'text/html', 'authorization': '[redacted]'}
```

This removes a common footgun when headers end up in logs, tracebacks, or
error messages.

## Adapter fidelity

Multi-value and ordering fidelity varies by HTTP client:

| Client | Multi-value headers | Order |
| --- | --- | --- |
| httpx | All preserved | Wire order |
| aiohttp | All preserved | Wire order |
| requests | All preserved (via `resp.raw`) | Grouped by name |
| niquests | All preserved (via `resp.raw`) | Grouped by name |
| urllib3 | All preserved | Grouped by name |

See [HTTP Client Adapters](../guides/adapters.md) for details on each
adapter's behaviour.

## Full API reference

::: pyhaul.transport._headers.TransportHeaders
    options:
      show_source: false
