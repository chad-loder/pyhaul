"""Pluggable HTTP transport: protocols, types, and errors."""

from pyhaul.transport.errors import (
    TransportConnectionError,
    TransportError,
    TransportHTTPError,
    TransportTLSError,
    TransportUnsupportedError,
)
from pyhaul.transport.protocols import (
    AsyncTransportResponse,
    AsyncTransportSession,
    TransportResponse,
    TransportSession,
)
from pyhaul.transport.proxy_transport_session import (
    AsyncTransportSessionProxyPlanning,
    AsyncTransportSessionProxyRecipe,
    TransportSessionProxyPlanning,
    TransportSessionProxyRecipe,
    async_transport_session_proxy,
    transport_session_proxy,
)
from pyhaul.transport.types import TransportHeaders, TransportRequestOptions

__all__ = [
    "AsyncTransportResponse",
    "AsyncTransportSession",
    "AsyncTransportSessionProxyPlanning",
    "AsyncTransportSessionProxyRecipe",
    "TransportConnectionError",
    "TransportError",
    "TransportHTTPError",
    "TransportHeaders",
    "TransportRequestOptions",
    "TransportResponse",
    "TransportSession",
    "TransportSessionProxyPlanning",
    "TransportSessionProxyRecipe",
    "TransportTLSError",
    "TransportUnsupportedError",
    "async_transport_session_proxy",
    "transport_session_proxy",
]
