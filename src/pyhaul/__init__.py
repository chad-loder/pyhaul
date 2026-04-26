"""pyhaul: resumable, cursor-based, CDN-safe HTTP downloads."""

from pyhaul._session_dispatch import register_async_adapter, register_sync_adapter
from pyhaul._types import (
    CompleteHaul,
    ContentRangeError,
    ControlFileError,
    DestinationError,
    ETag,
    HashBuilder,
    HaulError,
    HaulState,
    PartialHaulError,
    ServerMeta,
    ServerMisconfiguredError,
    Url,
    parse_etag,
    parse_url,
)
from pyhaul._version import __version__
from pyhaul.async_engine import haul_async
from pyhaul.engine import haul

__all__ = [
    "CompleteHaul",
    "ContentRangeError",
    "ControlFileError",
    "DestinationError",
    "ETag",
    "HashBuilder",
    "HaulError",
    "HaulState",
    "PartialHaulError",
    "ServerMeta",
    "ServerMisconfiguredError",
    "Url",
    "__version__",
    "haul",
    "haul_async",
    "parse_etag",
    "parse_url",
    "register_async_adapter",
    "register_sync_adapter",
]
