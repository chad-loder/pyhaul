# pyhaul

**Resumable HTTP downloads for Python. Bring your own client.**

pyhaul borrows your existing HTTP session and handles byte-range negotiation,
crash-safe checkpointing, and validation. One call to [`haul()`][pyhaul.engine.haul] = one request.
It either succeeds, or it saves progress so the next call resumes.

```bash
pip install pyhaul[httpx]   # or: niquests, requests, urllib3, aiohttp
```

```python
import httpx
from pyhaul import haul

with httpx.Client() as client:
    result = haul("https://example.com/big.zip", client, dest="big.zip")
    print(f"done: sha256={result.sha256[:16]}…")
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
