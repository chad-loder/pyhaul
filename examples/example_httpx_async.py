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
