# pyhaul

[![CI](https://github.com/chad-loder/pyhaul/actions/workflows/ci.yml/badge.svg)](https://github.com/chad-loder/pyhaul/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/chad-loder/pyhaul/graph/badge.svg)](https://codecov.io/gh/chad-loder/pyhaul)
[![PyPI](https://img.shields.io/pypi/v/pyhaul.svg)](https://pypi.org/project/pyhaul/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Resumable HTTP downloads for Python.

```bash
pip install pyhaul[requests]   # or: pyhaul[httpx], pyhaul[niquests], pyhaul[urllib3]
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
requests, httpx, niquests, and urllib3.

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
[DESIGN.md](https://github.com/chad-loder/pyhaul/blob/main/DESIGN.md) for the exception hierarchy, transport
adapters, and download lifecycle.
