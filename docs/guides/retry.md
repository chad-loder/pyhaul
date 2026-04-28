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
library. It works well with pyhaul because `PartialHaulError` is a normal
exception you can filter on:

=== "httpx"

    ```python
    from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter
    import httpx
    from pyhaul import haul, PartialHaulError

    @retry(
        retry=retry_if_exception_type((PartialHaulError, httpx.TransportError)),
        wait=wait_exponential_jitter(initial=2, max=30),
        stop=stop_after_attempt(10),
    )
    def download(client, url, dest):
        return haul(url, client, dest=dest)
    ```

=== "aiohttp"

    ```python
    from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter
    import aiohttp
    from pyhaul import haul_async, PartialHaulError

    @retry(
        retry=retry_if_exception_type((PartialHaulError, aiohttp.ClientError)),
        wait=wait_exponential_jitter(initial=2, max=30),
        stop=stop_after_attempt(10),
    )
    async def download(session, url, dest):
        return await haul_async(url, session, dest=dest)
    ```

=== "requests"

    ```python
    from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter
    import requests
    from pyhaul import haul, PartialHaulError

    @retry(
        retry=retry_if_exception_type((PartialHaulError, requests.ConnectionError, requests.Timeout)),
        wait=wait_exponential_jitter(initial=2, max=30),
        stop=stop_after_attempt(10),
    )
    def download(session, url, dest):
        return haul(url, session, dest=dest)
    ```

=== "niquests"

    ```python
    from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter
    import niquests
    from pyhaul import haul, PartialHaulError

    @retry(
        retry=retry_if_exception_type((PartialHaulError, niquests.ConnectionError, niquests.Timeout)),
        wait=wait_exponential_jitter(initial=2, max=30),
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

pyhaul raises [`ServerMisconfiguredError`][pyhaul._types.ServerMisconfiguredError] for any HTTP status that isn't
200, 206, or 416. This includes both permanent errors (404, 403) and transient
ones (429, 503). If you need to retry on transient status codes — for example,
to honor a `Retry-After` header — catch `ServerMisconfiguredError` alongside
your client's status exception:

=== "httpx"

    ```python
    import time
    import httpx
    from pyhaul import haul, PartialHaulError, ServerMisconfiguredError

    for attempt in range(1, 11):
        try:
            result = haul(url, client, dest="file.bin")
            break
        except PartialHaulError:
            time.sleep(min(2**attempt, 30))
        except ServerMisconfiguredError as exc:
            if "429" in str(exc) or "503" in str(exc):
                time.sleep(min(2**attempt, 30))
            else:
                raise
    ```

=== "aiohttp"

    ```python
    import asyncio
    import aiohttp
    from pyhaul import haul_async, PartialHaulError, ServerMisconfiguredError

    for attempt in range(1, 11):
        try:
            result = await haul_async(url, session, dest="file.bin")
            break
        except PartialHaulError:
            await asyncio.sleep(min(2**attempt, 30))
        except ServerMisconfiguredError as exc:
            if "429" in str(exc) or "503" in str(exc):
                await asyncio.sleep(min(2**attempt, 30))
            else:
                raise
    ```

!!! tip
    If your HTTP client is configured to raise on non-2xx status codes
    (e.g., `httpx.Client(event_hooks=...)` or `response.raise_for_status()`),
    the client's own exception will surface *before* pyhaul sees the status.
    In that case, catch the client's exception type directly instead of
    `ServerMisconfiguredError`.

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
