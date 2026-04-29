# HTTP Client Adapters

pyhaul works with the HTTP client you already use. Pass your session object
directly to [`haul()`][pyhaul.engine.haul] or [`haul_async()`][pyhaul.async_engine.haul_async] — pyhaul auto-detects the library and
wraps it internally.

## Supported clients

| Extra | Client type | Async | Install |
| --- | --- | --- | --- |
| `httpx` | `httpx.Client` / `httpx.AsyncClient` | Yes | `pip install pyhaul[httpx]` |
| `niquests` | `niquests.Session` / `niquests.AsyncSession` | Yes | `pip install pyhaul[niquests]` |
| `aiohttp` | `aiohttp.ClientSession` | Async only | `pip install pyhaul[aiohttp]` |
| `requests` | `requests.Session` | Sync only | `pip install pyhaul[requests]` |
| `urllib3` | `urllib3.PoolManager` | Sync only | `pip install pyhaul[urllib3]` |

## Your session, your config

pyhaul **borrows** your session. It never creates, configures, or closes it.
This means all your existing configuration — auth headers, proxies, connection
pools, TLS settings, timeouts — passes through unchanged.

```python
import httpx
from pyhaul import haul

client = httpx.Client(
    headers={"Authorization": "Bearer sk-..."},
    proxy="http://proxy.corp:3128",
    timeout=30.0,
    limits=httpx.Limits(max_connections=10),
)
with client:
    result = haul("https://internal.cdn/asset.bin", client, dest="asset.bin")
```

pyhaul adds per-request headers for range negotiation (`Range`, `If-Range`,
`Accept-Encoding: identity`, `Cache-Control: no-transform`) but does not
modify your session. After `haul()` returns, the session is exactly as you
left it.

You can also supply **extra headers for that download** via the optional
`headers=` keyword on [`haul()`][pyhaul.engine.haul] / [`haul_async()`][pyhaul.async_engine.haul_async].
Those merge before structural headers; structural headers still win where they
overlap. That is separate from session defaults (which many clients merge in on
their own). To adjust headers inside the adapter layer — for example tests or
telemetry — implement or wrap [`prepare_headers()`][pyhaul.transport.protocols.TransportSession.prepare_headers];
see [Writing a Custom Adapter](custom-transport.md).

### Redirects

pyhaul does not override your HTTP client's redirect policy. Library defaults differ:
**`httpx.Client`** / **`httpx.AsyncClient`** use **`follow_redirects=False`**
(httpx treats opt-in redirect following as conservative with respect to credentials on
cross-host redirects); **`requests.Session`**, **`niquests.Session`**,
**`aiohttp.ClientSession`**, and urllib3 (via pyhaul's adapter) follow redirects in the
usual configurations unless you turn that off. CDN and mirror URLs often redirect — configure
the session or client you pass to pyhaul accordingly (for example
`httpx.Client(follow_redirects=True)`).

## Per-client examples

### httpx (sync)

[![Built with HTTPX](https://img.shields.io/badge/built%20with-HTTPX-blue)](https://www.python-httpx.org/)

```python
import httpx
from pyhaul import haul

with httpx.Client() as client:
    result = haul("https://example.com/file.bin", client, dest="file.bin")
```

### httpx (async)

```python
import asyncio
import httpx
from pyhaul import haul_async

async def main():
    async with httpx.AsyncClient() as client:
        result = await haul_async(
            "https://example.com/file.bin", client, dest="file.bin"
        )

asyncio.run(main())
```

### requests

```python
import requests
from pyhaul import haul

with requests.Session() as session:
    result = haul("https://example.com/file.bin", session, dest="file.bin")
```

### niquests (sync)

```python
import niquests
from pyhaul import haul

with niquests.Session() as session:
    result = haul("https://example.com/file.bin", session, dest="file.bin")
```

### niquests (async)

```python
import asyncio
import niquests
from pyhaul import haul_async

async def main():
    async with niquests.AsyncSession() as session:
        result = await haul_async(
            "https://example.com/file.bin", session, dest="file.bin"
        )

asyncio.run(main())
```

### aiohttp

[![Built with aiohttp](https://img.shields.io/badge/built%20with-aiohttp-blue)](https://github.com/aio-libs/aiohttp)

```python
import asyncio
import aiohttp
from pyhaul import haul_async

async def main():
    async with aiohttp.ClientSession() as session:
        result = await haul_async(
            "https://example.com/file.bin", session, dest="file.bin"
        )

asyncio.run(main())
```

### urllib3

```python
import urllib3
from pyhaul import haul

http = urllib3.PoolManager()
result = haul("https://example.com/file.bin", http, dest="file.bin")
```

## Auth and session integration

Since pyhaul uses your session as-is, authentication works exactly as it does
in your application today.

### Bearer tokens

```python
client = httpx.Client(headers={"Authorization": "Bearer sk-..."})
```

### Basic auth

=== "httpx"

    ```python
    client = httpx.Client(auth=("user", "password"))
    ```

=== "requests"

    ```python
    session = requests.Session()
    session.auth = ("user", "password")
    ```

### Custom auth flows

If your application uses a custom auth handler (e.g. OAuth token refresh),
attach it to the session before passing it to pyhaul:

```python
from httpx import Auth, Request, Response

class TokenRefreshAuth(Auth):
    def auth_flow(self, request: Request):
        request.headers["Authorization"] = f"Bearer {self.get_token()}"
        yield request

    def get_token(self) -> str:
        ...  # your token refresh logic

client = httpx.Client(auth=TokenRefreshAuth())
result = haul(url, client, dest="file.bin")
```

### Proxies

=== "httpx"

    ```python
    client = httpx.Client(proxy="http://proxy.corp:3128")
    ```

=== "requests / niquests"

    ```python
    session = requests.Session()
    session.proxies = {
        "http": "http://proxy.corp:3128",
        "https": "http://proxy.corp:3128",
    }
    ```

=== "urllib3"

    ```python
    http = urllib3.ProxyManager("http://proxy.corp:3128")
    ```

### Connection pooling

Your session's connection pool is shared across all `haul()` calls. pyhaul
does not create its own connections.

```python
client = httpx.Client(
    limits=httpx.Limits(
        max_connections=20,
        max_keepalive_connections=10,
    )
)
```

## Adapter-specific notes

### httpx

- Both sync (`httpx.Client`) and async (`httpx.AsyncClient`) are supported.
- pyhaul uses `response.iter_raw()` / `response.aiter_raw()` to bypass
  content decoding, ensuring byte-accurate resume.
- Per-request **`follow_redirects`** can be forwarded via **`TransportRequestOptions`**
  when an adapter receives it (see [Writing a Custom Adapter](custom-transport.md)); otherwise
  httpx uses your client's default (constructor default is `follow_redirects=False` — see
  [Redirects](#redirects) above).

### requests

- **Not thread-safe.** If you need concurrent sync downloads, create one
  `requests.Session` per thread or use `niquests.Session` instead.
- pyhaul reads raw bytes via `response.raw.stream(chunk_size, decode_content=False)`
  to avoid decompression interference with range requests.

### niquests

- Thread-safe and supports HTTP/2 and HTTP/3.
- Both sync and async sessions are supported.
- Uses the same raw streaming approach as the requests adapter.

### aiohttp

- Async only — there is no sync aiohttp adapter.
- pyhaul sets `auto_decompress=False` on the request to ensure raw bytes for
  accurate resume.
- TLS certificate errors map to `TransportTLSError`.

### urllib3

- Sync only. Pass a `urllib3.PoolManager` (or `urllib3.ProxyManager` for proxied
  requests).
- The adapter streams raw bytes via the response's `stream()` method.

## Error handling

Transport errors from your HTTP library **pass through unwrapped**. pyhaul
does not wrap `httpx.ReadTimeout` in a pyhaul exception — you catch the types
you already know:

```python
import httpx
from pyhaul import haul, PartialHaulError

try:
    result = haul(url, client, dest="file.bin")
except PartialHaulError:
    # stream ended early — progress saved, safe to retry
    ...
except httpx.ReadTimeout:
    # your library's native timeout — handle as you normally would
    ...
except httpx.ConnectError:
    # connection failed — no progress to save
    ...
```

See [Exceptions Reference](../reference/exceptions.md) for the full pyhaul
exception hierarchy, including [`PartialHaulError`][pyhaul._types.PartialHaulError].
