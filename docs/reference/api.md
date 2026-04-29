# API Reference

## Download functions

::: pyhaul.engine.haul
    options:
      heading_level: 3

::: pyhaul.async_engine.haul_async
    options:
      heading_level: 3

## Types

::: pyhaul._types.CompleteHaul
    options:
      heading_level: 3

::: pyhaul._types.HaulState
    options:
      heading_level: 3

::: pyhaul._types.ServerMeta
    options:
      heading_level: 3

::: pyhaul._types.Url
    options:
      heading_level: 3

::: pyhaul._types.ETag
    options:
      heading_level: 3

### TransportHeaders {#transport-headers-type}

The immutable header type used for normalized responses and for merged outbound
requests inside adapters. Full constructors and methods are documented on the
dedicated [TransportHeaders](headers.md) page — duplicated here it would collide with that reference.

## Utility functions

::: pyhaul._types.parse_url
    options:
      heading_level: 3

::: pyhaul._types.parse_etag
    options:
      heading_level: 3

::: pyhaul._types.HashBuilder
    options:
      heading_level: 3

## Adapter registration

::: pyhaul._session_dispatch.register_sync_adapter
    options:
      heading_level: 3

::: pyhaul._session_dispatch.register_async_adapter
    options:
      heading_level: 3

## Transport protocols

::: pyhaul.transport.protocols.TransportSession
    options:
      heading_level: 3

::: pyhaul.transport.protocols.TransportResponse
    options:
      heading_level: 3

::: pyhaul.transport.protocols.AsyncTransportSession
    options:
      heading_level: 3

::: pyhaul.transport.protocols.AsyncTransportResponse
    options:
      heading_level: 3

## Transport session proxy

Layer header policy around an existing adapter without subclassing each backend:

::: pyhaul.transport.proxy_transport_session.transport_session_proxy
    options:
      heading_level: 3

::: pyhaul.transport.proxy_transport_session.async_transport_session_proxy
    options:
      heading_level: 3
