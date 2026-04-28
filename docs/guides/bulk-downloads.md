# Bulk Downloads

This guide covers the most common real-world use case: downloading many files,
possibly in parallel, from a script or service.

## The core mental model

Each [`haul()`][pyhaul.engine.haul] call downloads one file with one HTTP request. pyhaul does not
manage parallelism, retry policy, or queueing — those are yours. What pyhaul
gives you:

- **Atomic completion.** The destination file does not exist until the download
  is fully complete. There is no window where a partial file sits at the final
  path.
- **Crash-safe resume.** If the process is killed mid-download, sidecar files
  (`.part` and `.part.ctrl`) persist on disk. The next `haul()` call to the same
  destination picks up from the last durable byte.
- **Independent checkpoints.** Each destination has its own checkpoint. Ten
  downloads in flight means ten independent resume states. A crash partway
  through leaves each file individually resumable.

## When can you safely access the destination file?

**Only after `haul()` returns [`CompleteHaul`][pyhaul._types.CompleteHaul].**

Until that point, the file at the destination path does not exist. In-progress
data lives at `<dest>.part`. On completion, pyhaul atomically renames `.part`
to the final path. If `haul()` raises — for any reason — the destination file
is not created.

```python
result = haul(url, client, dest="data.bin")
# At this point — and only at this point — data.bin exists and is complete.
# result.sha256 provides the integrity hash.
```

!!! warning
    Never read `<dest>.part` directly. Its contents may include unflushed data
    or pre-allocated junk past the valid cursor. The `.part.ctrl` checkpoint
    knows the true valid length; the engine handles this on resume.

## Parallel downloads with threads (sync)

For sync clients (`requests`, `httpx.Client`, `niquests.Session`, `urllib3`),
use a thread pool. Each thread gets its own session or shares a thread-safe
session:

```python
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import httpx
from pyhaul import haul, PartialHaulError

FILES = [
    ("https://data.example.edu/census/2024-vol01.csv.gz", Path("data/2024-vol01.csv.gz")),
    ("https://data.example.edu/census/2024-vol02.csv.gz", Path("data/2024-vol02.csv.gz")),
    ("https://data.example.edu/census/2024-vol03.csv.gz", Path("data/2024-vol03.csv.gz")),
]
MAX_RETRIES = 10


def download_with_retry(client: httpx.Client, url: str, dest: Path) -> Path:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            haul(url, client, dest=dest)
            return dest
        except PartialHaulError:
            if attempt == MAX_RETRIES:
                raise
    return dest  # unreachable, but satisfies type checkers


Path("data").mkdir(exist_ok=True)

with httpx.Client() as client:
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {
            pool.submit(download_with_retry, client, url, dest): url
            for url, dest in FILES
        }
        for future in as_completed(futures):
            url = futures[future]
            try:
                path = future.result()
                print(f"done: {path}")
            except Exception as exc:
                print(f"failed: {url}: {exc}")
```

!!! note
    `requests.Session` is the only sync client that is *not* thread-safe.
    Create one session per thread, or switch to `niquests.Session`, `httpx.Client`,
    or `urllib3.PoolManager` — all of which are thread-safe.

## Parallel downloads with asyncio

For async clients (`httpx.AsyncClient`, `aiohttp.ClientSession`,
`niquests.AsyncSession`), use `asyncio.TaskGroup` or `asyncio.gather`:

```python
import asyncio
from pathlib import Path

import httpx
from pyhaul import haul_async, PartialHaulError

FILES = [
    ("https://data.example.edu/census/2024-vol01.csv.gz", Path("data/2024-vol01.csv.gz")),
    ("https://data.example.edu/census/2024-vol02.csv.gz", Path("data/2024-vol02.csv.gz")),
    ("https://data.example.edu/census/2024-vol03.csv.gz", Path("data/2024-vol03.csv.gz")),
]


async def download_one(
    client: httpx.AsyncClient, url: str, dest: Path
) -> Path:
    for attempt in range(1, 11):
        try:
            await haul_async(url, client, dest=dest)
            return dest
        except PartialHaulError:
            if attempt == 10:
                raise
            await asyncio.sleep(min(2**attempt, 30))
    return dest


async def main() -> None:
    Path("data").mkdir(exist_ok=True)
    async with httpx.AsyncClient() as client:
        async with asyncio.TaskGroup() as tg:
            tasks = [
                tg.create_task(download_one(client, url, dest))
                for url, dest in FILES
            ]
        for task in tasks:
            print(f"done: {task.result()}")


asyncio.run(main())
```

### Limiting concurrency

If you're downloading hundreds of files, limit concurrency with a semaphore:

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
    return dest
```

## What happens on interruption

When your script is killed (SIGINT, SIGTERM, OOM, power loss):

1. Any in-flight `haul()` calls leave their `.part` and `.part.ctrl` files on
   disk.
2. Destination files that were not yet complete **do not exist** at their final
   paths.
3. Destination files from previously completed downloads are unaffected.
4. Re-running the same script resumes each incomplete download from its
   checkpoint.

You do not need to track which files completed. Just iterate your download list
again — `haul()` will skip already-complete files (if the destination exists)
or resume from the checkpoint.

```python
for url, dest in FILES:
    if dest.exists():
        continue  # already complete from a previous run
    download_with_retry(client, url, dest)
```

## The `.part` and `.part.ctrl` relationship

Resume only works when **both** sidecar files are present. The `.part.ctrl`
file is the checkpoint — it records the cursor position, ETag, and block-level
hashes that pyhaul needs to verify the `.part` data before appending. Without
it, pyhaul has no way to trust the bytes already on disk.

**If the `.part` file exists without its `.part.ctrl`:**

pyhaul treats this as a fresh download. It overwrites the orphaned `.part` from
byte 0 — no resume, no error. The data in the old `.part` is discarded.

This can happen if the `.part.ctrl` file is accidentally deleted, or if a
`.part` file was left behind by a different tool or a previous version of
your script.

**If a `.part` file is left behind by something other than pyhaul:**

pyhaul will overwrite it from the beginning (since there's no matching
checkpoint). This is safe — the `.part` file is a scratch workspace, not a
final artifact. The no-clobber guarantee applies only to the **destination
file**, which is never written to until the atomic rename at completion.

!!! warning
    If you are managing downloads across process restarts, do not delete
    `.part.ctrl` files without also deleting their `.part` companions. An
    orphaned `.part` wastes disk space until pyhaul (or your cleanup code)
    overwrites or removes it.

## Cleaning up failed downloads

To discard a partial download and force a restart, delete both sidecar files:

```python
from pathlib import Path

dest = Path("data.bin")
dest.with_suffix(dest.suffix + ".part.ctrl").unlink(missing_ok=True)  # checkpoint first
dest.with_suffix(dest.suffix + ".part").unlink(missing_ok=True)
```

## Summary

| Question | Answer |
| --- | --- |
| When does the destination file exist? | Only after `haul()` returns `CompleteHaul` |
| What files are on disk during download? | `<dest>.part` (data) and `<dest>.part.ctrl` (checkpoint) |
| What happens on crash/kill? | Sidecar files persist; next `haul()` resumes |
| `.part` exists without `.part.ctrl`? | pyhaul starts over from byte 0 (no resume possible) |
| Is it safe to read `.part` directly? | No — use the completed destination file only |
| How do I force a fresh download? | Delete both `.part` and `.part.ctrl` |
