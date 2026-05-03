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

::: pyhaul.etag.EntityTag
    options:
      heading_level: 3

::: pyhaul.etag.EMPTY_ETAG
    options:
      heading_level: 3

### TransportHeaders {#transport-headers-type}

The immutable header type used for normalized responses and for merged outbound
requests inside adapters. See [TransportHeaders](headers.md) for constructors and methods.

## Utility functions

::: pyhaul._types.parse_url
    options:
      heading_level: 3

::: pyhaul.etag.parse_etag
    options:
      heading_level: 3

::: pyhaul.etag.format_entity_tag_for_http_header
    options:
      heading_level: 3

::: pyhaul.etag.is_weak_validator
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
