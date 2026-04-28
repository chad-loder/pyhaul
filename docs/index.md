# pyhaul

**Resumable HTTP downloads for Python. Bring your own client.**

pyhaul borrows your existing HTTP session and handles byte-range negotiation,
crash-safe checkpointing, and validation. One call to [`haul()`][pyhaul.engine.haul] = one request.
It either succeeds, or it saves progress so the next call resumes.

=== "httpx"

    ```bash
    pip install pyhaul[httpx]
    ```

    ```python
    import httpx
    from pathlib import Path
    from pyhaul import haul, PartialHaulError

    dest = Path("big.zip")
    with httpx.Client() as client:
        for _ in range(10):
            try:
                result = haul("https://example.com/big.zip", client, dest=dest)
                break
            except PartialHaulError:
                pass  # only retryable error; others propagate

    print(f"done: {dest.stat().st_size:,} bytes")
    ```

=== "aiohttp"

    ```bash
    pip install pyhaul[aiohttp]
    ```

    ```python
    import asyncio
    import aiohttp
    from pathlib import Path
    from pyhaul import haul_async, PartialHaulError

    async def main():
        dest = Path("big.zip")
        async with aiohttp.ClientSession() as session:
            for _ in range(10):
                try:
                    result = await haul_async("https://example.com/big.zip", session, dest=dest)
                    break
                except PartialHaulError:
                    pass
        print(f"done: {dest.stat().st_size:,} bytes")

    asyncio.run(main())
    ```

=== "requests"

    ```bash
    pip install pyhaul[requests]
    ```

    ```python
    import requests
    from pathlib import Path
    from pyhaul import haul, PartialHaulError

    dest = Path("big.zip")
    with requests.Session() as session:
        for _ in range(10):
            try:
                result = haul("https://example.com/big.zip", session, dest=dest)
                break
            except PartialHaulError:
                pass

    print(f"done: {dest.stat().st_size:,} bytes")
    ```

=== "niquests"

    ```bash
    pip install pyhaul[niquests]
    ```

    ```python
    import niquests
    from pathlib import Path
    from pyhaul import haul, PartialHaulError

    dest = Path("big.zip")
    with niquests.Session() as session:
        for _ in range(10):
            try:
                result = haul("https://example.com/big.zip", session, dest=dest)
                break
            except PartialHaulError:
                pass

    print(f"done: {dest.stat().st_size:,} bytes")
    ```

=== "urllib3"

    ```bash
    pip install pyhaul[urllib3]
    ```

    ```python
    import urllib3
    from pathlib import Path
    from pyhaul import haul, PartialHaulError

    dest = Path("big.zip")
    pool = urllib3.PoolManager()
    for _ in range(10):
        try:
            result = haul("https://example.com/big.zip", pool, dest=dest)
            break
        except PartialHaulError:
            pass

    print(f"done: {dest.stat().st_size:,} bytes")
    pool.clear()
    ```

## Guarantees

- **The destination file will not exist until download is complete.** Incomplete
  data lives in a `.part` file; on completion it is atomically moved into place.
- **Interrupted downloads resume when possible.** Kill the process, lose the
  network — the next `haul()` picks up from the last durable byte.
- **If the remote resource changes, retry will not corrupt.** ETag-based
  validation detects changes between attempts.
- **Your HTTP client is borrowed, not owned.** pyhaul never creates, configures,
  or closes sessions.
- **Transport errors pass through unwrapped.** `httpx.ReadTimeout` stays
  `httpx.ReadTimeout`.

## Where to go next

<div class="grid cards" markdown>

-   **[Quick Start](quickstart.md)**

    Install pyhaul, download a file, resume after failure — in under 3 minutes.

-   **[Bulk Downloads](guides/bulk-downloads.md)**

    Parallel downloads, interruption handling, and when you can safely access
    the destination file.

-   **[HTTP Client Adapters](guides/adapters.md)**

    Use pyhaul with httpx, requests, aiohttp, niquests, or urllib3 — including
    auth and session integration.

-   **[API Reference](reference/api.md)**

    Complete reference for `haul()`, `haul_async()`, types, and state.

</div>
