# Writing a Custom Adapter

If your application uses an HTTP library that pyhaul doesn't ship an adapter
for, you can write your own. The adapter protocol is intentionally minimal: one
context manager, one iterator.

## Why the protocol is structured this way

pyhaul needs exactly one thing from an HTTP client: a streaming GET request
that yields raw bytes. No connection management, no cookie handling, no retry
logic — just "open a stream, give me bytes, close the stream."

This is why the protocol is a single `stream_get()` context manager rather than
a full-featured HTTP client interface. pyhaul delegates everything else
(auth, proxies, TLS, pooling) to your session.

## The TransportSession protocol

A sync adapter implements [`TransportSession`][pyhaul.transport.protocols.TransportSession]:

```python
from contextlib import AbstractContextManager
from collections.abc import Iterator, Mapping
from pyhaul.transport.protocols import TransportSession, TransportResponse
from pyhaul._types import Url

class TransportSession:
    def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
    ) -> AbstractContextManager[TransportResponse]:
        ...
```

The returned `TransportResponse` needs three things:

```python
class TransportResponse:
    @property
    def status_code(self) -> int: ...

    @property
    def headers(self) -> TransportHeaders: ...

    def iter_raw_bytes(self, *, chunk_size: int) -> Iterator[bytes]: ...
```

!!! important
    `iter_raw_bytes` must yield **raw bytes** — post-transfer-encoding,
    pre-content-encoding. This means the bytes as the server framed them,
    without decompression. If your library auto-decompresses, you need to
    bypass that layer (e.g. `decode_content=False` in requests/urllib3,
    `iter_raw()` instead of `iter_bytes()` in httpx).

## Minimal working example

Here's a complete sync adapter for the `urllib3` library, simplified for
clarity:

```python
from collections.abc import Iterator, Mapping
from contextlib import contextmanager

import urllib3

from pyhaul._types import Url
from pyhaul.transport.protocols import TransportResponse, TransportSession
from pyhaul.transport.types import TransportHeaders


class MyResponse(TransportResponse):
    def __init__(self, resp: urllib3.HTTPResponse) -> None:
        self._resp = resp
        self._headers: TransportHeaders | None = None

    @property
    def status_code(self) -> int:
        return self._resp.status

    @property
    def headers(self) -> TransportHeaders:
        if self._headers is None:
            self._headers = TransportHeaders.from_pairs(
                list(self._resp.headers.items())
            )
        return self._headers

    def raise_for_status(self) -> None:
        if self._resp.status >= 400:
            raise RuntimeError(f"HTTP {self._resp.status}")

    def iter_raw_bytes(self, *, chunk_size: int) -> Iterator[bytes]:
        yield from self._resp.stream(chunk_size, decode_content=False)


class MyAdapter:
    def __init__(self, pool: urllib3.PoolManager) -> None:
        self._pool = pool

    @contextmanager
    def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options=None,
    ) -> Iterator[TransportResponse]:
        resp = self._pool.request(
            "GET", str(url), headers=dict(headers), preload_content=False
        )
        try:
            yield MyResponse(resp)
        finally:
            resp.release_conn()
```

## Registering your adapter

Once you have an adapter class, register it so `haul()` can auto-detect your
client type with [`register_sync_adapter()`][pyhaul._session_dispatch.register_sync_adapter]:

```python
from pyhaul import register_sync_adapter

def my_factory(obj):
    if isinstance(obj, urllib3.PoolManager):
        return MyAdapter(obj)
    return None

register_sync_adapter(my_factory)
```

Now `haul(url, my_pool_manager, dest=...)` works without the caller needing
to wrap manually.

### Async adapters

The async protocol mirrors the sync one:

- [`AsyncTransportSession`][pyhaul.transport.protocols.AsyncTransportSession]`.stream_get()` returns an
  `AbstractAsyncContextManager[AsyncTransportResponse]`
- `AsyncTransportResponse.aiter_raw_bytes()` returns an `AsyncIterator[bytes]`

Register with [`register_async_adapter()`][pyhaul._session_dispatch.register_async_adapter].

## TransportHeaders

The `TransportHeaders` class normalizes response headers for pyhaul's
internal use. Build one from the response's header pairs:

```python
from pyhaul.transport.types import TransportHeaders

headers = TransportHeaders.from_pairs([
    ("Content-Type", "application/octet-stream"),
    ("Content-Length", "1048576"),
    ("ETag", '"abc123"'),
])
```

This handles case-insensitive lookups and multi-value headers.

## Error mapping (optional but recommended)

pyhaul's built-in adapters map library-specific exceptions to a common
`TransportError` hierarchy. This enables the engine to distinguish
connection errors from HTTP errors from TLS errors. If you want the same
behavior, catch your library's exceptions and re-raise as:

- `TransportConnectionError` — network-level failures (timeouts, DNS, connection refused)
- `TransportHTTPError` — HTTP-level errors (4xx, 5xx)
- `TransportTLSError` — certificate or TLS handshake failures
- `TransportUnsupportedError` — unsupported protocol/scheme

This is optional. If you don't map errors, your library's native exceptions
propagate through to the caller (which is fine — pyhaul's "transport errors
pass through unwrapped" guarantee still holds).

## Testing your adapter

The simplest test: download a small file and verify the hash:

```python
from pyhaul import haul

pool = urllib3.PoolManager()
result = haul("https://httpbin.org/bytes/1024", pool, dest="test.bin")
assert len(result.sha256) > 0
```

For more thorough testing, verify resume behavior: start a download, interrupt
it (e.g. by mocking a network error after N bytes), then call `haul()` again
and confirm it resumes from the checkpoint.
