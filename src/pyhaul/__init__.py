"""pyhaul: resumable, cursor-based, CDN-safe HTTP downloads."""

from pyhaul._session_dispatch import register_async_adapter, register_sync_adapter
from pyhaul._types import (
    AsyncProgressCallback,
    CompleteHaul,
    ContentRangeError,
    ControlFileError,
    DestinationError,
    HashBuilder,
    HaulError,
    HaulState,
    PartialHaulError,
    ServerMeta,
    ServerMisconfiguredError,
    UnexpectedStatusError,
    Url,
    parse_url,
)
from pyhaul._version import __version__
from pyhaul.async_engine import haul_async
from pyhaul.engine import haul
from pyhaul.etag import (
    EMPTY_ETAG,
    EntityTag,
    ETag,
    format_entity_tag_for_http_header,
    is_weak_validator,
    parse_etag,
)

__all__ = [
    "EMPTY_ETAG",
    "AsyncProgressCallback",
    "CompleteHaul",
    "ContentRangeError",
    "ControlFileError",
    "DestinationError",
    "ETag",
    "EntityTag",
    "HashBuilder",
    "HaulError",
    "HaulState",
    "PartialHaulError",
    "ServerMeta",
    "ServerMisconfiguredError",
    "UnexpectedStatusError",
    "Url",
    "__version__",
    "format_entity_tag_for_http_header",
    "haul",
    "haul_async",
    "is_weak_validator",
    "parse_etag",
    "parse_url",
    "register_async_adapter",
    "register_sync_adapter",
]
