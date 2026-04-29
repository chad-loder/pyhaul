# Retry Patterns

One [`haul()`][pyhaul.engine.haul] = one HTTP request. When the stream ends early, pyhaul raises
[`PartialHaulError`][pyhaul._types.PartialHaulError] and saves progress to disk. Retries are your responsibility
— a deliberate design choice that lets you use whatever retry strategy fits
your application.

## Simple for-loop

The most straightforward approach — no dependencies:

=== "httpx"

    ```python
    import time
    import httpx
    from pyhaul import haul, PartialHaulError, HaulState

    state = HaulState()

    with httpx.Client() as client:
        for attempt in range(1, 11):
            try:
                result = haul(url, client, dest="file.bin", state=state)
                print(f"done: {state.valid_length:,} bytes")
                break
            except PartialHaulError as exc:
                print(f"attempt {attempt}: {exc.reason}")
                time.sleep(min(2**attempt, 30))
        else:
            raise RuntimeError("download failed after 10 attempts")
    ```

=== "aiohttp"

    ```python
    import asyncio
    import aiohttp
    from pyhaul import haul_async, PartialHaulError, HaulState

    async def main():
        state = HaulState()

        async with aiohttp.ClientSession() as session:
            for attempt in range(1, 11):
                try:
                    result = await haul_async(url, session, dest="file.bin", state=state)
                    print(f"done: {state.valid_length:,} bytes")
                    return
                except PartialHaulError as exc:
                    print(f"attempt {attempt}: {exc.reason}")
                    await asyncio.sleep(min(2**attempt, 30))
            raise RuntimeError("download failed after 10 attempts")

    asyncio.run(main())
    ```

=== "requests"

    ```python
    import time
    import requests
    from pyhaul import haul, PartialHaulError, HaulState

    state = HaulState()

    with requests.Session() as session:
        for attempt in range(1, 11):
            try:
                result = haul(url, session, dest="file.bin", state=state)
                print(f"done: {state.valid_length:,} bytes")
                break
            except PartialHaulError as exc:
                print(f"attempt {attempt}: {exc.reason}")
                time.sleep(min(2**attempt, 30))
        else:
            raise RuntimeError("download failed after 10 attempts")
    ```

=== "niquests"

    ```python
    import time
    import niquests
    from pyhaul import haul, PartialHaulError, HaulState

    state = HaulState()

    with niquests.Session() as session:
        for attempt in range(1, 11):
            try:
                result = haul(url, session, dest="file.bin", state=state)
                print(f"done: {state.valid_length:,} bytes")
                break
            except PartialHaulError as exc:
                print(f"attempt {attempt}: {exc.reason}")
                time.sleep(min(2**attempt, 30))
        else:
            raise RuntimeError("download failed after 10 attempts")
    ```

## tenacity

[tenacity](https://tenacity.readthedocs.io/) is the standard Python retry
library. A custom predicate lets you retry on partial downloads *and*
transient HTTP status errors in one decorator:

=== "httpx"

    ```python
    from tenacity import (
        retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter,
    )
    import httpx
    from pyhaul import haul, PartialHaulError, UnexpectedStatusError

    def _retryable(exc: BaseException) -> bool:
        if isinstance(exc, (PartialHaulError, httpx.TransportError)):
            return True
        return isinstance(exc, UnexpectedStatusError) and exc.is_transient

    @retry(
        retry=retry_if_exception(_retryable),
        wait=wait_exponential_jitter(initial=2, max=60),
        stop=stop_after_attempt(10),
    )
    def download(client, url, dest):
        return haul(url, client, dest=dest)
    ```

=== "aiohttp"

    ```python
    from tenacity import (
        retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter,
    )
    import aiohttp
    from pyhaul import haul_async, PartialHaulError, UnexpectedStatusError

    def _retryable(exc: BaseException) -> bool:
        if isinstance(exc, (PartialHaulError, aiohttp.ClientError)):
            return True
        return isinstance(exc, UnexpectedStatusError) and exc.is_transient

    @retry(
        retry=retry_if_exception(_retryable),
        wait=wait_exponential_jitter(initial=2, max=60),
        stop=stop_after_attempt(10),
    )
    async def download(session, url, dest):
        return await haul_async(url, session, dest=dest)
    ```

=== "requests"

    ```python
    from tenacity import (
        retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter,
    )
    import requests
    from pyhaul import haul, PartialHaulError, UnexpectedStatusError

    def _retryable(exc: BaseException) -> bool:
        if isinstance(exc, (PartialHaulError, requests.ConnectionError, requests.Timeout)):
            return True
        return isinstance(exc, UnexpectedStatusError) and exc.is_transient

    @retry(
        retry=retry_if_exception(_retryable),
        wait=wait_exponential_jitter(initial=2, max=60),
        stop=stop_after_attempt(10),
    )
    def download(session, url, dest):
        return haul(url, session, dest=dest)
    ```

=== "niquests"

    ```python
    from tenacity import (
        retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter,
    )
    import niquests
    from pyhaul import haul, PartialHaulError, UnexpectedStatusError

    def _retryable(exc: BaseException) -> bool:
        if isinstance(exc, (PartialHaulError, niquests.ConnectionError, niquests.Timeout)):
            return True
        return isinstance(exc, UnexpectedStatusError) and exc.is_transient

    @retry(
        retry=retry_if_exception(_retryable),
        wait=wait_exponential_jitter(initial=2, max=60),
        stop=stop_after_attempt(10),
    )
    def download(session, url, dest):
        return haul(url, session, dest=dest)
    ```

tenacity's async support works transparently with `haul_async`.

## What to retry and what not to retry

| Exception | Retry? | Why |
| --- | --- | --- |
| [`PartialHaulError`][pyhaul._types.PartialHaulError] | Yes | Stream ended early; progress saved to checkpoint |
| Transport errors (timeout, connection reset) | Yes | Transient network issue — see table below |
| [`UnexpectedStatusError`][pyhaul._types.UnexpectedStatusError] | Caller decides | `exc.is_transient` is True for common temporary statuses (408, 425, 429, many 5xx, CDN codes); `exc.is_server_error` is True for any HTTP 5xx |
| [`ServerMisconfiguredError`][pyhaul._types.ServerMisconfiguredError] | No | Server violates HTTP in a way that prevents safe resume |
| [`DestinationError`][pyhaul._types.DestinationError] | No | Path problem — retrying won't fix it |
| [`ControlFileError`][pyhaul._types.ControlFileError] | Auto-recovers | Corrupt checkpoint is discarded; next attempt starts fresh |

Transport errors from your HTTP library **pass through unwrapped** — pyhaul
never wraps them. The retryable base class varies by library:

| Library | Retryable base exception |
| --- | --- |
| httpx | `httpx.TransportError` |
| aiohttp | `aiohttp.ClientError` |
| requests | `requests.ConnectionError`, `requests.Timeout` |
| niquests | `niquests.ConnectionError`, `niquests.Timeout` |
| urllib3 | `urllib3.exceptions.HTTPError` |

## Transient HTTP status errors

When the server returns a non-download status (anything other than 200, 206, or
416), pyhaul raises [`UnexpectedStatusError`][pyhaul._types.UnexpectedStatusError]
with structured metadata — `status_code`, `headers`,
`is_transient` / `is_server_error`, and parsed `retry_after_seconds` — so you can branch on status codes and honour
`Retry-After` (delay seconds or HTTP-date) without manual parsing:

=== "httpx"

    ```python
    import time
    import httpx
    from pyhaul import haul, PartialHaulError, UnexpectedStatusError

    with httpx.Client() as client:
        for attempt in range(1, 11):
            try:
                result = haul(url, client, dest="file.bin")
                break
            except PartialHaulError:
                time.sleep(min(2**attempt, 30))
            except UnexpectedStatusError as exc:
                if exc.is_transient:
                    wait = exc.retry_after_seconds if exc.retry_after_seconds is not None else min(2**attempt, 60)
                    time.sleep(wait)
                else:
                    raise  # 404, 403, etc. — not retryable
    ```

=== "aiohttp"

    ```python
    import asyncio
    import aiohttp
    from pyhaul import haul_async, PartialHaulError, UnexpectedStatusError

    async def main():
        async with aiohttp.ClientSession() as session:
            for attempt in range(1, 11):
                try:
                    result = await haul_async(url, session, dest="file.bin")
                    break
                except PartialHaulError:
                    await asyncio.sleep(min(2**attempt, 30))
                except UnexpectedStatusError as exc:
                    if exc.is_transient:
                        wait = exc.retry_after_seconds if exc.retry_after_seconds is not None else min(2**attempt, 60)
                        await asyncio.sleep(wait)
                    else:
                        raise  # 404, 403, etc. — not retryable

    asyncio.run(main())
    ```

=== "requests"

    ```python
    import time
    import requests
    from pyhaul import haul, PartialHaulError, UnexpectedStatusError

    with requests.Session() as session:
        for attempt in range(1, 11):
            try:
                result = haul(url, session, dest="file.bin")
                break
            except PartialHaulError:
                time.sleep(min(2**attempt, 30))
            except UnexpectedStatusError as exc:
                if exc.is_transient:
                    wait = exc.retry_after_seconds if exc.retry_after_seconds is not None else min(2**attempt, 60)
                    time.sleep(wait)
                else:
                    raise  # 404, 403, etc. — not retryable
    ```

=== "niquests"

    ```python
    import time
    import niquests
    from pyhaul import haul, PartialHaulError, UnexpectedStatusError

    with niquests.Session() as session:
        for attempt in range(1, 11):
            try:
                result = haul(url, session, dest="file.bin")
                break
            except PartialHaulError:
                time.sleep(min(2**attempt, 30))
            except UnexpectedStatusError as exc:
                if exc.is_transient:
                    wait = exc.retry_after_seconds if exc.retry_after_seconds is not None else min(2**attempt, 60)
                    time.sleep(wait)
                else:
                    raise  # 404, 403, etc. — not retryable
    ```

!!! note
    pyhaul reads the HTTP status code directly from the streaming response
    before your client has a chance to raise its own status exception.
    A 429 or 503 always surfaces as `UnexpectedStatusError` with
    `is_transient == True`, regardless of your client's `raise_for_status`
    configuration.

`exc.headers` is a [`TransportHeaders`](../reference/headers.md) — an
immutable, case-insensitive mapping you can query for `Retry-After` and other
response metadata. Sensitive headers like `Authorization` are automatically
redacted in logs and tracebacks.

## Backoff strategies

### Exponential backoff with jitter

Best for most cases — prevents thundering herd when multiple downloads
retry simultaneously:

```python
wait=wait_exponential_jitter(initial=2, max=60)
```

### Skip backoff when progress was made

If `haul()` transferred bytes before failing, the connection is working but
unstable. You can retry immediately in that case:

```python
state = HaulState()

for attempt in range(1, 11):
    state.bytes_read = 0
    try:
        result = haul(url, client, dest="file.bin", state=state)
        break
    except PartialHaulError:
        if state.bytes_read == 0:
            time.sleep(min(2**attempt, 30))
        # else: retry immediately — connection works, just dropped
```

The CLI uses this strategy internally.

## Progress-aware retry

Pass a `HaulState` to track cumulative progress across retries:

```python
state = HaulState()

for attempt in range(1, 11):
    try:
        result = haul(url, client, dest="file.bin", state=state)
        break
    except PartialHaulError:
        if state.reported_length:
            pct = state.valid_length / state.reported_length * 100
            print(f"attempt {attempt}: {pct:.1f}% complete, retrying…")
```

`state.valid_length` reflects the total bytes saved to disk (not just this
attempt). `state.reported_length` is the server-claimed total size (may be
`None` for chunked responses).
