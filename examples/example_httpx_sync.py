import time
from pathlib import Path

import httpx

from pyhaul import HaulState, PartialHaulError, haul

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
