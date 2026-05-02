"""Requests-backed sync transport adapter."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager

import requests
import requests.exceptions as _requests_exceptions

from pyhaul._types import Url
from pyhaul.transport._http_common import request_options_to_requests_like_kwargs, transport_header_pairs
from pyhaul.transport._requests_like_error_map import map_requests_like_transport_errors
from pyhaul.transport.protocols import TransportResponse, TransportSession
from pyhaul.transport.types import TransportHeaders, TransportRequestOptions


def headers_from_requests_response(resp: requests.Response) -> TransportHeaders:
    """Build :class:`TransportHeaders` from a requests response."""
    # resp.headers is CaseInsensitiveDict (collapses multi-value headers).
    # Reach through to the urllib3 raw response for multi-value fidelity;
    # order is grouped-by-name (HTTPHeaderDict limitation).
    raw_headers = getattr(resp.raw, "headers", None)
    if raw_headers is not None and hasattr(raw_headers, "iteritems"):
        return TransportHeaders.from_pairs(transport_header_pairs(raw_headers.iteritems()))
    return TransportHeaders.from_pairs(transport_header_pairs(resp.headers.items()))


class RequestsTransportResponse(TransportResponse):
    """Transport view over a :class:`requests.Response`."""

    __slots__ = ("_headers", "_resp")

    def __init__(self, resp: requests.Response) -> None:
        self._resp = resp
        self._headers: TransportHeaders | None = None

    @property
    def status_code(self) -> int:
        """HTTP status code of the response."""
        return int(self._resp.status_code)

    @property
    def headers(self) -> TransportHeaders:
        """Response headers, lazily parsed on first access."""
        if self._headers is None:
            self._headers = headers_from_requests_response(self._resp)
        return self._headers

    def raise_for_status(self) -> None:
        """Raise :exc:`~pyhaul.transport.errors.TransportHTTPError` for 4xx/5xx responses."""
        with map_requests_like_transport_errors(_requests_exceptions):
            self._resp.raise_for_status()

    def iter_raw_bytes(self, *, chunk_size: int) -> Iterator[bytes]:
        """Yield raw response body chunks without decoding."""
        with map_requests_like_transport_errors(_requests_exceptions):
            yield from self._resp.raw.stream(chunk_size, decode_content=False)


class RequestsAdapter:
    """Wrap a :class:`requests.Session` as a :class:`TransportSession`."""

    __slots__ = ("_session",)

    def __init__(self, session: requests.Session) -> None:
        self._session = session

    def prepare_headers(self, headers: TransportHeaders) -> TransportHeaders:
        """Optionally mutate headers before they are sent (noop)."""
        return headers

    @contextmanager
    def stream_get(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> Iterator[TransportResponse]:
        """Open a streaming GET request and yield the response."""
        kwargs = request_options_to_requests_like_kwargs(options)
        with (
            map_requests_like_transport_errors(_requests_exceptions),
            self._session.get(url, headers=dict(headers), stream=True, **kwargs) as resp,
        ):
            yield RequestsTransportResponse(resp)

    @contextmanager
    def stream_head(
        self,
        url: Url,
        *,
        headers: Mapping[str, str],
        options: TransportRequestOptions | None = None,
    ) -> Iterator[TransportResponse]:
        """Open a HEAD request and yield the response."""
        kwargs = request_options_to_requests_like_kwargs(options)
        with map_requests_like_transport_errors(_requests_exceptions):
            resp = self._session.head(str(url), headers=dict(headers), **kwargs)
        try:
            yield RequestsTransportResponse(resp)
        finally:
            resp.close()


def requests_transport(session: requests.Session) -> TransportSession:
    """Shorthand: ``RequestsAdapter(session)``."""
    return RequestsAdapter(session)
