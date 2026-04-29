# Async Usage

pyhaul has a real async engine — not a sync wrapper running in a thread pool.
[`haul_async()`][pyhaul.async_engine.haul_async] uses `async with` and `async for` natively, sharing all
non-I/O logic with the sync path.

## Supported async clients

| Library | Client type | Install |
| --- | --- | --- |
| httpx | `httpx.AsyncClient` | `pip install pyhaul[httpx]` |
| aiohttp | `aiohttp.ClientSession` | `pip install pyhaul[aiohttp]` |
| niquests | `niquests.AsyncSession` | `pip install pyhaul[niquests]` |

!!! tip "What about urllib3?"
    urllib3 is sync-only. For async downloads with urllib3, use
    [`asyncio.to_thread()`](#mixing-sync-clients-with-asyncio) to run the sync
    `haul()` in a thread pool.

## Basic async download

=== "httpx"

    ```python
    import asyncio
    import httpx
    from pyhaul import haul_async

    async def main():
        async with httpx.AsyncClient() as client:
            result = await haul_async(
                "https://example.com/file.bin",
                client,
                dest="file.bin",
            )
            print(f"done: sha256={result.sha256[:16]}…")

    asyncio.run(main())
    ```

=== "aiohttp"

    ```python
    import asyncio
    import aiohttp
    from pyhaul import haul_async

    async def main():
        async with aiohttp.ClientSession() as session:
            result = await haul_async(
                "https://example.com/file.bin",
                session,
                dest="file.bin",
            )
            print(f"done: sha256={result.sha256[:16]}…")

    asyncio.run(main())
    ```

=== "niquests"

    ```python
    import asyncio
    import niquests
    from pyhaul import haul_async

    async def main():
        async with niquests.AsyncSession() as session:
            result = await haul_async(
                "https://example.com/file.bin",
                session,
                dest="file.bin",
            )
            print(f"done: sha256={result.sha256[:16]}…")

    asyncio.run(main())
    ```

!!! note
    pyhaul sets `auto_decompress=False` on aiohttp requests internally to
    ensure raw bytes for accurate resume. Your session's other settings
    (auth, proxies, timeouts) pass through unchanged.

## Concurrent downloads with TaskGroup

`asyncio.TaskGroup` (Python 3.11+) is the cleanest way to download multiple
files concurrently:

=== "httpx"

    ```python
    import asyncio
    from pathlib import Path
    import httpx
    from pyhaul import haul_async, PartialHaulError

    URLS = [
        ("https://data.example.edu/census/2024-vol01.csv.gz", Path("data/vol01.csv.gz")),
        ("https://data.example.edu/census/2024-vol02.csv.gz", Path("data/vol02.csv.gz")),
        ("https://data.example.edu/census/2024-vol03.csv.gz", Path("data/vol03.csv.gz")),
    ]

    async def download_one(client: httpx.AsyncClient, url: str, dest: Path):
        for attempt in range(1, 11):
            try:
                await haul_async(url, client, dest=dest)
                return dest
            except PartialHaulError:
                if attempt == 10:
                    raise
                await asyncio.sleep(min(2**attempt, 30))

    async def main():
        Path("data").mkdir(exist_ok=True)
        async with httpx.AsyncClient() as client:
            async with asyncio.TaskGroup() as tg:
                tasks = [
                    tg.create_task(download_one(client, url, dest))
                    for url, dest in URLS
                ]
        for task in tasks:
            print(f"done: {task.result()}")

    asyncio.run(main())
    ```

=== "aiohttp"

    ```python
    import asyncio
    from pathlib import Path
    import aiohttp
    from pyhaul import haul_async, PartialHaulError

    URLS = [
        ("https://data.example.edu/census/2024-vol01.csv.gz", Path("data/vol01.csv.gz")),
        ("https://data.example.edu/census/2024-vol02.csv.gz", Path("data/vol02.csv.gz")),
        ("https://data.example.edu/census/2024-vol03.csv.gz", Path("data/vol03.csv.gz")),
    ]

    async def download_one(session: aiohttp.ClientSession, url: str, dest: Path):
        for attempt in range(1, 11):
            try:
                await haul_async(url, session, dest=dest)
                return dest
            except PartialHaulError:
                if attempt == 10:
                    raise
                await asyncio.sleep(min(2**attempt, 30))

    async def main():
        Path("data").mkdir(exist_ok=True)
        async with aiohttp.ClientSession() as session:
            async with asyncio.TaskGroup() as tg:
                tasks = [
                    tg.create_task(download_one(session, url, dest))
                    for url, dest in URLS
                ]
        for task in tasks:
            print(f"done: {task.result()}")

    asyncio.run(main())
    ```

=== "niquests"

    ```python
    import asyncio
    from pathlib import Path
    import niquests
    from pyhaul import haul_async, PartialHaulError

    URLS = [
        ("https://data.example.edu/census/2024-vol01.csv.gz", Path("data/vol01.csv.gz")),
        ("https://data.example.edu/census/2024-vol02.csv.gz", Path("data/vol02.csv.gz")),
        ("https://data.example.edu/census/2024-vol03.csv.gz", Path("data/vol03.csv.gz")),
    ]

    async def download_one(session: niquests.AsyncSession, url: str, dest: Path):
        for attempt in range(1, 11):
            try:
                await haul_async(url, session, dest=dest)
                return dest
            except PartialHaulError:
                if attempt == 10:
                    raise
                await asyncio.sleep(min(2**attempt, 30))

    async def main():
        Path("data").mkdir(exist_ok=True)
        async with niquests.AsyncSession() as session:
            async with asyncio.TaskGroup() as tg:
                tasks = [
                    tg.create_task(download_one(session, url, dest))
                    for url, dest in URLS
                ]
        for task in tasks:
            print(f"done: {task.result()}")

    asyncio.run(main())
    ```

Each `haul_async()` call manages its own checkpoint independently. A crash
partway through leaves each file in a separately resumable state.

## Limiting concurrency with Semaphore

When downloading many files, limit concurrency to avoid overwhelming the
server or exhausting file descriptors:

=== "httpx"

    ```python
    sem = asyncio.Semaphore(8)

    async def download_one(client: httpx.AsyncClient, url: str, dest: str):
        async with sem:
            for attempt in range(1, 11):
                try:
                    await haul_async(url, client, dest=dest)
                    return dest
                except PartialHaulError:
                    if attempt == 10:
                        raise
                    await asyncio.sleep(min(2**attempt, 30))
    ```

=== "aiohttp"

    ```python
    sem = asyncio.Semaphore(8)

    async def download_one(session: aiohttp.ClientSession, url: str, dest: str):
        async with sem:
            for attempt in range(1, 11):
                try:
                    await haul_async(url, session, dest=dest)
                    return dest
                except PartialHaulError:
                    if attempt == 10:
                        raise
                    await asyncio.sleep(min(2**attempt, 30))
    ```

=== "niquests"

    ```python
    sem = asyncio.Semaphore(8)

    async def download_one(session: niquests.AsyncSession, url: str, dest: str):
        async with sem:
            for attempt in range(1, 11):
                try:
                    await haul_async(url, session, dest=dest)
                    return dest
                except PartialHaulError:
                    if attempt == 10:
                        raise
                    await asyncio.sleep(min(2**attempt, 30))
    ```

## Async with tenacity

[tenacity](https://tenacity.readthedocs.io/) supports async natively. Decorate
an `async def` and tenacity handles the await:

=== "httpx"

    ```python
    from tenacity import (
        retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter,
    )
    import httpx
    from pyhaul import haul_async, PartialHaulError, UnexpectedStatusError

    def _retryable(exc: BaseException) -> bool:
        if isinstance(exc, (PartialHaulError, httpx.TransportError)):
            return True
        return isinstance(exc, UnexpectedStatusError) and exc.is_transient

    @retry(
        retry=retry_if_exception(_retryable),
        wait=wait_exponential_jitter(initial=2, max=60),
        stop=stop_after_attempt(10),
    )
    async def download(client: httpx.AsyncClient, url: str, dest: str):
        return await haul_async(url, client, dest=dest)
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
    async def download(session: aiohttp.ClientSession, url: str, dest: str):
        return await haul_async(url, session, dest=dest)
    ```

=== "niquests"

    ```python
    from tenacity import (
        retry, retry_if_exception, stop_after_attempt, wait_exponential_jitter,
    )
    import niquests
    from pyhaul import haul_async, PartialHaulError, UnexpectedStatusError

    def _retryable(exc: BaseException) -> bool:
        if isinstance(exc, (PartialHaulError, niquests.RequestException)):
            return True
        return isinstance(exc, UnexpectedStatusError) and exc.is_transient

    @retry(
        retry=retry_if_exception(_retryable),
        wait=wait_exponential_jitter(initial=2, max=60),
        stop=stop_after_attempt(10),
    )
    async def download(session: niquests.AsyncSession, url: str, dest: str):
        return await haul_async(url, session, dest=dest)
    ```

## Mixing sync clients with asyncio

urllib3 and `requests.Session` are sync-only. If your application is async but
you need one of these clients, use [`asyncio.to_thread()`](https://docs.python.org/3/library/asyncio-task.html#asyncio.to_thread)
to run the sync `haul()` in a thread without blocking the event loop:

=== "urllib3"

    ```python
    import asyncio
    import urllib3
    from pyhaul import haul

    async def main():
        pool = urllib3.PoolManager()
        result = await asyncio.to_thread(
            haul, "https://example.com/file.bin", pool, dest="file.bin",
        )
        print(f"done: sha256={result.sha256[:16]}…")
        pool.clear()

    asyncio.run(main())
    ```

=== "requests"

    ```python
    import asyncio
    import requests
    from pyhaul import haul

    async def main():
        with requests.Session() as session:
            result = await asyncio.to_thread(
                haul, "https://example.com/file.bin", session, dest="file.bin",
            )
            print(f"done: sha256={result.sha256[:16]}…")

    asyncio.run(main())
    ```

!!! warning
    `asyncio.to_thread()` runs the download in a separate OS thread.
    You get non-blocking I/O from the event loop's perspective, but you
    lose the benefits of true async streaming (single-threaded concurrency,
    lower memory, backpressure). Prefer a native async client when possible.

## Progress reporting

`haul_async` accepts an [`AsyncProgressCallback`][pyhaul._types.AsyncProgressCallback]:
either a synchronous callable or one whose return value is awaitable (for example
`async def`). The engine awaits each chunk's hook before reading the next chunk,
so you can push updates over websockets or similar without wrapping each call in
[`asyncio.create_task`][asyncio.create_task]. Keep work bounded — progress runs on the
download's critical path.

[`haul()`][pyhaul.engine.haul] still accepts only synchronous callbacks.

Pass a [`HaulState`][pyhaul._types.HaulState] to track progress. Example with a sync hook:

```python
import asyncio

import httpx

from pyhaul import haul_async, HaulState

state = HaulState()
high_water = 0


def show_progress(state: HaulState):
    global high_water
    # After a retry, valid_length may rewind to the last checkpoint.
    # Use a high-water mark to avoid showing backward progress.
    high_water = max(high_water, state.valid_length)
    if state.reported_length:
        pct = high_water / state.reported_length * 100
        print(f"\r{pct:.1f}%", end="", flush=True)


async def main():
    url = "https://example.com/file.bin"
    async with httpx.AsyncClient() as client:
        await haul_async(
            url,
            client,
            dest="file.bin",
            state=state,
            on_progress=show_progress,
        )


asyncio.run(main())
```

!!! note "Cancellation and outer timeouts"
    pyhaul only maps **HTTP client** failures (timeouts, disconnects, TLS, etc.)
    to [`TransportError`][pyhaul.transport.errors.TransportError] subclasses.
    **Caller-owned** deadlines — `asyncio.wait_for(...)`, `asyncio.Task.cancel()`, or
    cooperative cancellation — surface as `asyncio.TimeoutError` or
    `asyncio.CancelledError`. Those bypass adapter translation and are **not**
    turned into [`PartialHaulError`][pyhaul._types.PartialHaulError]; treat them as
    application policy, not transport failure.
