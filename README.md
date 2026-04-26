# pyhaul

[![CI](https://github.com/chad-loder/pyhaul/actions/workflows/ci.yml/badge.svg)](https://github.com/chad-loder/pyhaul/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/chad-loder/pyhaul/graph/badge.svg)](https://codecov.io/gh/chad-loder/pyhaul)
[![PyPI](https://img.shields.io/pypi/v/pyhaul.svg)](https://pypi.org/project/pyhaul/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Resumable HTTP downloads for Python.

```bash
pip install pyhaul[requests]   # or: httpx, niquests, urllib3, aiohttp (see below)
```

```python
import requests
from pyhaul import haul

with requests.Session() as session:
    result = haul("https://example.com/big.zip", session, dest="big.zip")
    print(f"done: sha256={result.sha256[:16]}…")
```

---

## What is it?

A small, pure-Python library that makes HTTP downloads **resumable**.
Call `haul()` with your existing HTTP client, a URL, and a destination
path — it handles byte-range negotiation, ETag validation, crash-safe
checkpointing, and atomic file completion. Sync and async; works with
requests, httpx, niquests, urllib3, and **aiohttp** (async).

Each call to `haul()` upholds these guarantees:

- **The destination file is either complete or absent.** There is no
  state where a partially-written file sits at the final path.
  Incomplete data lives in a temporary `.part` file; on completion
  it is atomically moved into place.
- **Interrupted downloads resume, not restart.** Checkpoint state
  lives on disk, not in memory. Kill the process, lose the network,
  get a 503 — the next `haul()` picks up from the last durable
  byte. Zero re-downloaded data if the resource hasn't changed.
- **Changed resources are detected, not silently corrupted.** If
  the remote file changes between attempts, `pyhaul` detects the
  mismatch via ETag (a server-side fingerprint) and starts over
  cleanly instead of gluing mismatched halves together.
- **Your HTTP client is borrowed, not owned.** `pyhaul` sets
  per-request headers and returns the session untouched. It never
  creates, configures, or closes sessions.
- **Transport errors pass through unwrapped.** `httpx.ReadTimeout`
  stays `httpx.ReadTimeout`. You catch the types you already know.

## How it fits into your code

One `haul()` = one HTTP request. It either succeeds and returns
`CompleteHaul`, or it throws — possibly after saving progress
to a `.part` file that allows the next call to resume. `pyhaul` never
creates sessions, connections, or clients. Your HTTP library's native
exceptions propagate through unwrapped, so you can drop `haul()`
into existing code without changing your error handling. Retries are
your call — a for-loop, `tenacity`, or nothing. Concurrency limiting
(e.g. `asyncio.Semaphore`) is also yours — `pyhaul` downloads one
file per call and doesn't manage parallelism.

```python
def haul(url, client, *, dest, state=None) -> CompleteHaul: ...
async def haul_async(url, client, *, dest, state=None) -> CompleteHaul: ...
```

`state` is an optional `HaulState` bag, updated in-place as bytes
land on disk — works identically in sync and async. See
[DESIGN.md](DESIGN.md) for the exception hierarchy, transport
adapters, and download lifecycle.

<!-- pypi-end -->

## Examples

<!-- source: examples/example_httpx_sync.py -->
<details>
<summary><strong>Sync with retries (httpx)</strong></summary>

```python
import time
from pathlib import Path

import httpx

from pyhaul import PartialHaulError, HaulState, haul

url = "https://example.com/big.iso"
dest = Path("big.iso")
state = HaulState()  # optional — tracks byte-level progress

with httpx.Client() as client:
    for attempt in range(1, 11):
        try:
            result = haul(url, client, dest=dest, state=state)
            print(f"done: {state.valid_length:,} bytes, sha256={result.sha256[:16]}…")
            break
        except PartialHaulError as exc:
            print(f"attempt {attempt}: {exc.reason} ({state.valid_length:,} bytes so far)")
            time.sleep(min(2**attempt, 30))
```

</details>

<!-- source: examples/example_httpx_async.py -->
<details>
<summary><strong>Async concurrent downloads (httpx + tenacity)</strong></summary>

```python
import asyncio
from pathlib import Path

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from pyhaul import PartialHaulError, haul_async

URLS = [
    ("https://example.com/data/shard-001.bin", Path("downloads/shard-001.bin")),
    ("https://example.com/data/shard-002.bin", Path("downloads/shard-002.bin")),
    ("https://example.com/data/shard-003.bin", Path("downloads/shard-003.bin")),
]


@retry(
    retry=retry_if_exception_type(PartialHaulError),
    wait=wait_exponential_jitter(initial=2, max=30),
    stop=stop_after_attempt(10),
)
async def download_one(client: httpx.AsyncClient, url: str, dest: Path) -> None:
    await haul_async(url, client, dest=dest)


async def main() -> None:
    Path("downloads").mkdir(exist_ok=True)
    async with httpx.AsyncClient() as client, asyncio.TaskGroup() as tg:
        for url, dest in URLS:
            tg.create_task(download_one(client, url, dest))


asyncio.run(main())
```

Each `haul_async` call manages its own checkpoint independently.
A crash partway through leaves each file in a separately resumable
state.

</details>

<!-- See doc_todo.md for future README section ideas. -->

## Why this exists

You probably already know that resuming an HTTP download isn't just
`Range: bytes=N-`. The full list of silent failure modes is longer
than most people expect — servers that return 200 instead of 206,
resources that change between retries (`curl -C -` and `aria2c` both
miss this), compression that corrupts resumed streams, and ordering
guarantees needed for crash safety. See [WHY.md](WHY.md) for the
deep-dive and a comparison with `curl`, `wget`, and `aria2c`.

## Install

```bash
pip install pyhaul[requests]   # if you already use requests
pip install pyhaul[httpx]      # httpx (sync + async)
pip install pyhaul[niquests]   # niquests (HTTP/2+3, async)
pip install pyhaul[urllib3]    # raw urllib3
pip install pyhaul[aiohttp]    # aiohttp (async)
```

No hard dependency on any HTTP library. Pick one (or several) as extras.

---

## Development

```bash
git clone https://github.com/chad-loder/pyhaul.git && cd pyhaul
uv sync --all-groups
uv run pytest
just lint        # ruff + mypy + pyright + rumdl
```

## License

MIT. See the `LICENSE` file for details.
