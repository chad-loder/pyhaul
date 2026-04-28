# Quick Start

This walkthrough takes you from installation through a complete
download-resume-retry cycle. It takes about 3 minutes.

## Install

pyhaul has zero required dependencies. Pick an HTTP client extra that matches
what you already use:

=== "httpx"

    ```bash
    pip install pyhaul[httpx]
    ```

=== "requests"

    ```bash
    pip install pyhaul[requests]
    ```

=== "niquests"

    ```bash
    pip install pyhaul[niquests]
    ```

=== "aiohttp"

    ```bash
    pip install pyhaul[aiohttp]
    ```

=== "urllib3"

    ```bash
    pip install pyhaul[urllib3]
    ```

## Download a file

The entire API surface fits in one function: [`haul()`][pyhaul.engine.haul] (or [`haul_async()`][pyhaul.async_engine.haul_async] for
async code). Pass a URL, your HTTP client, and a destination path:

```python
import httpx
from pyhaul import haul

with httpx.Client() as client:
    result = haul("https://example.com/big.zip", client, dest="big.zip")
    print(f"done: sha256={result.sha256[:16]}…")
```

`haul()` returns a [`CompleteHaul`][pyhaul._types.CompleteHaul] on success, which carries the SHA-256 tree
hash, ETag, and content type.

## What happens on interruption

If the download is interrupted — network drop, process kill, `Ctrl-C` — two
sidecar files remain on disk:

- `big.zip.part` — the bytes downloaded so far
- `big.zip.part.ctrl` — a binary checkpoint with the cursor position, ETag,
  and block-level hashes

The destination file (`big.zip`) **does not exist** at this point. There is no
state where a partially-written file sits at the final path.

## Resume

To resume, call `haul()` again with the same destination. pyhaul reads the
checkpoint, sends a `Range` request with `If-Range: <etag>`, and appends from
where it left off:

```python
# Just call haul() again — it resumes automatically
result = haul("https://example.com/big.zip", client, dest="big.zip")
```

If the remote file changed between attempts, pyhaul detects the ETag mismatch
and restarts from byte 0 — no silent corruption.

## Add retry logic

One `haul()` = one HTTP request. When the stream ends early, pyhaul raises
[`PartialHaulError`][pyhaul._types.PartialHaulError] and saves progress. Wrap it in a retry loop:

```python
import time
from pyhaul import haul, PartialHaulError, HaulState

state = HaulState()

with httpx.Client() as client:
    for attempt in range(1, 11):
        try:
            result = haul(
                "https://example.com/big.zip",
                client,
                dest="big.zip",
                state=state,
            )
            print(f"done: {state.valid_length:,} bytes")
            break
        except PartialHaulError as exc:
            print(f"attempt {attempt}: {exc.reason} "
                  f"({state.valid_length:,} bytes so far)")
            time.sleep(min(2**attempt, 30))
```

[`HaulState`][pyhaul._types.HaulState] is an optional mutable bag updated in-place throughout the
download — useful for progress reporting or deciding whether to retry.

## Track progress

Pass `on_progress` to get called after each chunk lands on disk:

```python
def show_progress(state: HaulState) -> None:
    if state.reported_length:
        pct = state.valid_length / state.reported_length * 100
        print(f"\r{pct:.1f}%", end="", flush=True)

result = haul(url, client, dest="big.zip", state=state, on_progress=show_progress)
```

## Use the CLI

pyhaul also works as a command-line tool for quick smoke tests:

```bash
python -m pyhaul -o big.zip https://example.com/big.zip
```

The CLI handles retries automatically (up to 20 attempts with exponential
backoff). See [CLI Reference](reference/cli.md) for all options. Note that
the CLI is not a stable interface — for scripting and automation, use the
Python API directly.

## Next steps

- **[Bulk Downloads](guides/bulk-downloads.md)** — parallel downloads,
  interruption handling, and destination file safety
- **[HTTP Client Adapters](guides/adapters.md)** — integrate with your existing
  auth, session pooling, and proxy configuration
- **[Retry Patterns](guides/retry.md)** — advanced retry strategies with
  tenacity and backoff
- **[Async Usage](guides/async.md)** — `haul_async()` with TaskGroup and
  semaphore-based concurrency
