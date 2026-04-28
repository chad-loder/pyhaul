# Retry Patterns

One [`haul()`][pyhaul.engine.haul] = one HTTP request. When the stream ends early, pyhaul raises
[`PartialHaulError`][pyhaul._types.PartialHaulError] and saves progress to disk. Retries are your responsibility
— a deliberate design choice that lets you use whatever retry strategy fits
your application.

## Simple for-loop

The most straightforward approach — no dependencies:

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

## tenacity

[tenacity](https://tenacity.readthedocs.io/) is the standard Python retry
library. It works well with pyhaul because `PartialHaulError` is a normal
exception you can filter on:

```python
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)
from pyhaul import haul, PartialHaulError

@retry(
    retry=retry_if_exception_type(PartialHaulError),
    wait=wait_exponential_jitter(initial=2, max=30),
    stop=stop_after_attempt(10),
)
def download(client, url, dest):
    return haul(url, client, dest=dest)
```

### tenacity with async

```python
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter
from pyhaul import haul_async, PartialHaulError

@retry(
    retry=retry_if_exception_type(PartialHaulError),
    wait=wait_exponential_jitter(initial=2, max=30),
    stop=stop_after_attempt(10),
)
async def download(client, url, dest):
    return await haul_async(url, client, dest=dest)
```

tenacity's async support works transparently with `haul_async`.

## What to retry and what not to retry

| Exception | Retry? | Why |
| --- | --- | --- |
| [`PartialHaulError`][pyhaul._types.PartialHaulError] | Yes | Stream ended early; progress saved to checkpoint |
| `httpx.ReadTimeout` (or equivalent) | Yes | Transient network issue |
| `httpx.ConnectError` (or equivalent) | Yes | Transient connectivity issue |
| [`ServerMisconfiguredError`][pyhaul._types.ServerMisconfiguredError] | No | Server violates HTTP in a way that prevents safe resume |
| [`DestinationError`][pyhaul._types.DestinationError] | No | Path problem — retrying won't fix it |
| [`ControlFileError`][pyhaul._types.ControlFileError] | Auto-recovers | Corrupt checkpoint is discarded; next attempt starts fresh |

!!! tip
    Transport errors from your HTTP library pass through unwrapped. You can
    include them in your retry filter alongside `PartialHaulError`:

    ```python
    @retry(
        retry=retry_if_exception_type((PartialHaulError, httpx.TransportError)),
        wait=wait_exponential_jitter(initial=2, max=30),
        stop=stop_after_attempt(10),
    )
    def download(client, url, dest):
        return haul(url, client, dest=dest)
    ```

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
