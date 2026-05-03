# pyhaul

[![CI](https://github.com/chad-loder/pyhaul/actions/workflows/ci.yml/badge.svg?event=push)](https://github.com/chad-loder/pyhaul/actions/workflows/ci.yml)
[![codecov](https://codecov.io/gh/chad-loder/pyhaul/graph/badge.svg)](https://codecov.io/gh/chad-loder/pyhaul)
[![PyPI](https://img.shields.io/pypi/v/pyhaul.svg)](https://pypi.org/project/pyhaul/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
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

---

## What is it?

A small, pure-Python library that makes HTTP downloads **resumable**.
Call `haul()` with a URL, your existing HTTP client, and a destination
path. **pyhaul** handles byte-range negotiation, ETag validation,
crash-safe checkpointing, and atomic file completion. Supports both
sync and async across multiple HTTP client libraries.

Each call to `haul()` upholds these guarantees:

- **One `haul()` makes one request**. You are responsible for
  retry loops, but retry just means call `haul()` again.
- **The destination file will not exist until download is complete.**
  Incomplete data lives in a temporary `.part` file; on completion
  it is atomically moved into place.
- **Interrupted downloads resume when possible.** Checkpoint state
  lives on disk, not in memory. Kill the process, lose the network,
  get a 503 — the next `haul()` picks up from the last durable byte.
- **If the remote resource changes, retry will not corrupt.** ETag
  mismatch detection prevents gluing mismatched halves together.
- **Your HTTP client is borrowed, not owned.** `pyhaul` sets
  per-request headers and returns your session untouched.
- **Transport errors pass through unwrapped.** `httpx.ReadTimeout`
  stays `httpx.ReadTimeout`. You catch the types you already know.

No hard dependency on any HTTP library. Pick one (or several) as extras.

## Documentation

**[Full documentation](https://chad-loder.github.io/pyhaul/)** — Quick start, guides, and API reference.

- [Quick Start](https://chad-loder.github.io/pyhaul/quickstart/) — install, first download, async usage
- [Why pyhaul Exists](https://chad-loder.github.io/pyhaul/explanation/why/) — silent failure modes in HTTP resume, comparison with curl/wget/aria2c
- [Design & Architecture](https://chad-loder.github.io/pyhaul/explanation/design/) — transport adapters, checkpoint state, download lifecycle
- [API Reference](https://chad-loder.github.io/pyhaul/reference/api/) — `haul()`, `haul_async()`, `HaulState`, exceptions
- [Control File Spec](https://chad-loder.github.io/pyhaul/reference/spec/) — checkpoint format for implementers

<!-- pypi-end -->

---

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for branches, commit style, and full tooling.

```bash
git clone https://github.com/chad-loder/pyhaul.git && cd pyhaul
uv sync --all-groups
uv run pytest
just lint        # ruff + mypy + pyright + rumdl
```

## License

MIT. See the `LICENSE` file for details.
