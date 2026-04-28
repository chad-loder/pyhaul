# Async Usage

pyhaul has a real async engine — not a sync wrapper running in a thread pool.
[`haul_async()`][pyhaul.async_engine.haul_async] uses `async with` and `async for` natively, sharing all
non-I/O logic with the sync path.

## Basic async download

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

## Supported async clients

| Library | Client type | Install |
| --- | --- | --- |
| httpx | `httpx.AsyncClient` | `pip install pyhaul[httpx]` |
| niquests | `niquests.AsyncSession` | `pip install pyhaul[niquests]` |
| aiohttp | `aiohttp.ClientSession` | `pip install pyhaul[aiohttp]` |

## Concurrent downloads with TaskGroup

`asyncio.TaskGroup` (Python 3.11+) is the cleanest way to download multiple
files concurrently:

```python
import asyncio
from pathlib import Path
import httpx
from pyhaul import haul_async, PartialHaulError

URLS = [
    ("https://data.example.edu/census/2024-vol01.csv.gz", Path("data/2024-vol01.csv.gz")),
    ("https://data.example.edu/census/2024-vol02.csv.gz", Path("data/2024-vol02.csv.gz")),
    ("https://data.example.edu/census/2024-vol03.csv.gz", Path("data/2024-vol03.csv.gz")),
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

Each `haul_async()` call manages its own checkpoint independently. A crash
partway through leaves each file in a separately resumable state.

## Limiting concurrency with Semaphore

When downloading many files, limit concurrency to avoid overwhelming the
server or exhausting file descriptors:

```python
sem = asyncio.Semaphore(8)

async def download_one(client, url, dest):
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

## Async with tenacity

[tenacity](https://tenacity.readthedocs.io/) supports async natively. Decorate
an `async def` and tenacity handles the await:

```python
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)
from pyhaul import haul_async, PartialHaulError

@retry(
    retry=retry_if_exception_type(PartialHaulError),
    wait=wait_exponential_jitter(initial=2, max=30),
    stop=stop_after_attempt(10),
)
async def download(client, url, dest):
    return await haul_async(url, client, dest=dest)
```

## Async with aiohttp

aiohttp has a different session model from httpx. The adapter handles the
differences:

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

asyncio.run(main())
```

!!! note
    pyhaul sets `auto_decompress=False` on aiohttp requests internally to
    ensure raw bytes for accurate resume. Your session's other settings
    (auth, proxies, timeouts) pass through unchanged.

## Progress reporting in async

The `on_progress` callback is synchronous even in async mode — pass a
[`HaulState`][pyhaul._types.HaulState] to track progress. Keep the callback fast:

```python
from pyhaul import haul_async, HaulState

state = HaulState()

def show_progress(state: HaulState):
    if state.reported_length:
        pct = state.valid_length / state.reported_length * 100
        print(f"\r{pct:.1f}%", end="", flush=True)

async def main():
    async with httpx.AsyncClient() as client:
        result = await haul_async(
            url, client, dest="file.bin",
            state=state, on_progress=show_progress,
        )

asyncio.run(main())
```
