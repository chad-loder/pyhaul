# pyhaul

[![CI](https://github.com/chad-loder/pyhaul/actions/workflows/ci.yml/badge.svg?event=push)](https://github.com/chad-loder/pyhaul/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/chad-loder/pyhaul/graph/badge.svg)](https://codecov.io/gh/chad-loder/pyhaul)
[![PyPI](https://img.shields.io/pypi/v/pyhaul.svg)](https://pypi.org/project/pyhaul/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/chad-loder/pyhaul/blob/main/LICENSE)
[![Docs](https://img.shields.io/badge/docs-properdocs-blue.svg)](https://chad-loder.github.io/pyhaul/)

Resumable HTTP downloads for Python. **Bring your own client:** pyhaul borrows your existing
session and handles byte-range negotiation, crash-safe checkpointing, and validation.

[![httpx](https://img.shields.io/badge/httpx-async%2Bsync-6B46C1.svg)](https://www.python-httpx.org/)
[![niquests](https://img.shields.io/badge/niquests-async%2Bsync-6B46C1.svg)](https://niquests.readthedocs.io/)
[![aiohttp](https://img.shields.io/badge/aiohttp-async-2563EB.svg)](https://docs.aiohttp.org/)
[![requests](https://img.shields.io/badge/requests-sync-059669.svg)](https://requests.readthedocs.io/)
[![urllib3](https://img.shields.io/badge/urllib3-sync-059669.svg)](https://urllib3.readthedocs.io/)

```bash
pip install pyhaul[httpx]   # or: niquests, requests, urllib3, aiohttp
```

```python
import httpx  # or: requests, niquests, urllib3, aiohttp
from pyhaul import haul

with httpx.Client() as client:
    result = haul("https://example.com/big.zip", client, dest="big.zip")
    print(f"done: sha256={result.sha256[:16]}…")
```

---

## What is it?

A small, pure-Python library that makes HTTP downloads **resumable**.
To download a file, call `haul()` with a URL, your existing HTTP
client, and a destination path. **pyhaul** handles byte-range
negotiation for resume, ETag validation, crash-safe
checkpointing, and atomic file completion. Supports both sync and
async across multiple HTTP client libraries.

Each call to `haul()` upholds these guarantees:

- **One `haul()` makes one request**. You are responsible for
  retry loops, but retry just means call `haul()` again.
- **The destination file will not exist until download is complete.**
  There is no state where a partially-written file sits at the final
  path. Incomplete data lives in a temporary `.part` file; on completion
  it is atomically moved into place.
- **Interrupted downloads resume when possible.** Checkpoint state
  lives on disk, not in memory. Kill the process, lose the network,
  get a 503 — the next `haul()` picks up from the last durable
  byte. Zero re-downloaded data if the resource hasn't changed.
- **If the remote resource changes, retry will not corrupt.** If
  the remote file changes between attempts, `pyhaul` detects the
  mismatch via ETag (a server-side fingerprint) and starts over
  cleanly instead of gluing mismatched halves together.
- **Your HTTP client is borrowed, not owned.** `pyhaul` sets
  per-request headers and returns your session untouched. It never
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
[docs/DESIGN.md](https://github.com/chad-loder/pyhaul/blob/main/docs/DESIGN.md) for the exception hierarchy, transport
adapters, and download lifecycle.

## Documentation

**[Full documentation →](https://chad-loder.github.io/pyhaul/)**

- **[docs/DESIGN.md](https://github.com/chad-loder/pyhaul/blob/main/docs/DESIGN.md)** — Transport adapters, checkpoint state, and the download lifecycle.
- **[docs/WHY.md](https://github.com/chad-loder/pyhaul/blob/main/docs/WHY.md)** — Silent failure modes in HTTP range/resume, and how pyhaul compares
  to `curl`, `wget`, and `aria2c`.
- **[docs/SPEC.md](https://github.com/chad-loder/pyhaul/blob/main/docs/SPEC.md)** — Control file and checkpoint format (implementers / compatible tools).
