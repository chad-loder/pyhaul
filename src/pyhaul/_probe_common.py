"""Shared logic for :func:`~pyhaul.probe.probe` / :func:`~pyhaul.async_probe.probe_async`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from typing import cast

from pyhaul._types import EMPTY_ETAG, ContentRangeError, ETag, ProbeResult, UnexpectedStatusError, parse_etag, parse_url
from pyhaul.content_range import parse_content_range
from pyhaul.headers import DEFAULT_HEADERS, merge_headers
from pyhaul.transport._headers import TransportHeaders
from pyhaul.transport.protocols import AsyncTransportResponse, TransportResponse
from pyhaul.transport.types import TransportRequestOptions

_DEFAULT_CHUNK = 1 << 16


def _parse_content_length(raw: str | None) -> int | None:
    """Parse ``Content-Length`` (same rules as :mod:`pyhaul._engine_common`)."""
    if raw is None:
        return None
    s = raw.strip()
    tokens = [t.strip() for t in s.split(",") if t.strip()]
    if not tokens or any(not t.isdigit() for t in tokens):
        return None
    if len(set(tokens)) > 1:
        return None
    return int(tokens[0])


def _accept_ranges_bytes(headers: TransportHeaders) -> bool:
    """Return True when ``Accept-Ranges`` is ``bytes``."""
    raw = headers.get("Accept-Ranges") or ""
    return raw.strip().lower() == "bytes"


def _length_discovery_present(headers: TransportHeaders) -> bool:
    """True when ``Content-Range`` or parseable ``Content-Length`` hints at size."""
    cr = headers.get("Content-Range") or ""
    if cr.strip():
        return True
    return _parse_content_length(headers.get("Content-Length")) is not None


def _pypdl_style_metadata_complete(headers: TransportHeaders, *, head_was_2xx: bool) -> bool:
    """Whether HEAD returned enough metadata that pypdl would skip a ranged GET."""
    if not head_was_2xx:
        return False
    etag = (headers.get("ETag") or "").strip()
    cd = (headers.get("Content-Disposition") or "").strip()
    return bool(
        _accept_ranges_bytes(headers) and etag and cd and _length_discovery_present(headers),
    )


def _total_length_from_status(status: int, headers: TransportHeaders) -> int | None:
    """Infer full entity length from *status* and *headers* when possible."""
    if status == HTTPStatus.OK:
        return _parse_content_length(headers.get("Content-Length"))
    if status not in (HTTPStatus.PARTIAL_CONTENT, HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE):
        return None
    cr_raw = headers.get("Content-Range")
    if not cr_raw:
        return None
    try:
        cr = parse_content_range(cr_raw)
    except ContentRangeError:
        return None
    if status == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE:
        return cr.instance_length if cr.is_unsatisfied else None
    return cr.instance_length


def _unexpected(status: int, headers: TransportHeaders) -> UnexpectedStatusError:
    """Build :class:`UnexpectedStatusError` for *status* and *headers*."""
    return UnexpectedStatusError(status_code=status, headers=headers)


def _finalize_allowed_status(status: int, headers: TransportHeaders) -> None:
    """Raise if *status* is not an acceptable terminal probe response."""
    if status in (HTTPStatus.OK, HTTPStatus.PARTIAL_CONTENT):
        return
    if status == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE:
        cr_raw = headers.get("Content-Range") or ""
        if cr_raw.strip():
            try:
                cr = parse_content_range(cr_raw)
            except ContentRangeError:
                raise _unexpected(status, headers) from None
            if cr.is_unsatisfied and cr.instance_length is not None:
                return
        raise _unexpected(status, headers)
    if status >= HTTPStatus.BAD_REQUEST:
        raise _unexpected(status, headers)


@dataclass(frozen=True, slots=True)
class ProbeSnapshot:
    """Fields extracted from one HEAD or GET response."""

    status_code: int
    total_length: int | None
    etag: ETag
    last_modified: str
    content_type: str
    content_disposition: str
    accept_ranges_bytes: bool


def snapshot_from_response(status: int, headers: TransportHeaders) -> ProbeSnapshot:
    """Build a :class:`ProbeSnapshot` from response *status* and *headers*."""
    return ProbeSnapshot(
        status_code=status,
        total_length=_total_length_from_status(status, headers),
        etag=parse_etag(headers.get("ETag", "")),
        last_modified=headers.get("Last-Modified") or "",
        content_type=headers.get("Content-Type") or "",
        content_disposition=headers.get("Content-Disposition") or "",
        accept_ranges_bytes=_accept_ranges_bytes(headers),
    )


def merge_snapshots(primary: ProbeSnapshot, secondary: ProbeSnapshot) -> ProbeSnapshot:
    """Prefer non-empty / more informative values from *secondary* (ranged GET)."""
    lm = secondary.last_modified if secondary.last_modified.strip() else primary.last_modified
    ct = secondary.content_type if secondary.content_type.strip() else primary.content_type
    cd = secondary.content_disposition if secondary.content_disposition.strip() else primary.content_disposition
    etag = secondary.etag if secondary.etag != EMPTY_ETAG else primary.etag
    tot = secondary.total_length if secondary.total_length is not None else primary.total_length
    return ProbeSnapshot(
        status_code=secondary.status_code,
        total_length=tot,
        etag=etag,
        last_modified=lm,
        content_type=ct,
        content_disposition=cd,
        accept_ranges_bytes=secondary.accept_ranges_bytes or primary.accept_ranges_bytes,
    )


def probe_base_headers(user_headers: Mapping[str, str] | None) -> TransportHeaders:
    """Merge *user_headers* with pyhaul defaults (no ``Range``)."""
    merged = merge_headers(dict(user_headers or {}), dict(DEFAULT_HEADERS))
    return TransportHeaders.from_mapping(merged)


def drain_sync(resp: TransportResponse, *, chunk_size: int = _DEFAULT_CHUNK) -> None:
    """Consume the full raw body of *resp* (sync)."""
    for _ in resp.iter_raw_bytes(chunk_size=chunk_size):
        pass


async def drain_async(resp: AsyncTransportResponse, *, chunk_size: int = _DEFAULT_CHUNK) -> None:
    """Consume the full raw body of *resp* (async)."""
    async for _ in resp.aiter_raw_bytes(chunk_size=chunk_size):
        pass


def run_probe_sync(
    transport: object,
    url: str,
    *,
    headers: Mapping[str, str] | None,
    options: TransportRequestOptions | None,
) -> ProbeResult:
    """Execute the probe sequence synchronously (HEAD then optional ranged GET)."""
    from pyhaul.transport.protocols import TransportSession

    if not isinstance(transport, TransportSession):
        msg = "client must coerce to TransportSession (same adapters as haul)"
        raise TypeError(msg)

    parsed = parse_url(url)
    prepared_head = transport.prepare_headers(probe_base_headers(headers))

    snap: ProbeSnapshot | None = None
    ranged_get_used = False
    head_attempted = False
    head_status_code: int | None = None
    head_headers_saved: TransportHeaders | None = None
    skip_get = False

    with transport.stream_head(parsed, headers=dict(prepared_head.items()), options=options) as head_resp:
        head_attempted = True
        head_status_code = head_resp.status_code
        hh = head_resp.headers
        if HTTPStatus.OK <= head_resp.status_code < HTTPStatus.MULTIPLE_CHOICES:
            head_headers_saved = hh
            snap = snapshot_from_response(head_resp.status_code, hh)
            skip_get = _pypdl_style_metadata_complete(hh, head_was_2xx=True)

    if not skip_get:
        ranged_get_used = True
        probe_req = merge_headers(
            dict(headers or {}),
            {**DEFAULT_HEADERS, "Range": "bytes=0-0"},
        )
        th = transport.prepare_headers(TransportHeaders.from_mapping(probe_req))
        with transport.stream_get(parsed, headers=dict(th.items()), options=options) as resp:
            snap_get = snapshot_from_response(resp.status_code, resp.headers)
            drain_sync(resp)
            _finalize_allowed_status(resp.status_code, resp.headers)
            snap_final = merge_snapshots(snap, snap_get) if snap is not None else snap_get
    else:
        _finalize_allowed_status(cast("ProbeSnapshot", snap).status_code, cast("TransportHeaders", head_headers_saved))
        snap_final = cast("ProbeSnapshot", snap)

    return ProbeResult(
        url=parsed,
        status_code=snap_final.status_code,
        total_length=snap_final.total_length,
        etag=snap_final.etag,
        last_modified=snap_final.last_modified,
        content_type=snap_final.content_type,
        content_disposition=snap_final.content_disposition,
        accept_ranges_bytes=snap_final.accept_ranges_bytes,
        head_attempted=head_attempted,
        head_status_code=head_status_code,
        ranged_get_used=ranged_get_used,
    )


async def run_probe_async(
    transport: object,
    url: str,
    *,
    headers: Mapping[str, str] | None,
    options: TransportRequestOptions | None,
) -> ProbeResult:
    """Execute the probe sequence asynchronously."""
    from pyhaul.transport.protocols import AsyncTransportSession

    if not isinstance(transport, AsyncTransportSession):
        msg = "client must coerce to AsyncTransportSession (same adapters as haul_async)"
        raise TypeError(msg)

    parsed = parse_url(url)
    prepared_head = transport.prepare_headers(probe_base_headers(headers))

    snap: ProbeSnapshot | None = None
    ranged_get_used = False
    head_attempted = False
    head_status_code: int | None = None
    head_headers_saved: TransportHeaders | None = None
    skip_get = False

    async with transport.stream_head(parsed, headers=dict(prepared_head.items()), options=options) as head_resp:
        head_attempted = True
        head_status_code = head_resp.status_code
        hh = head_resp.headers
        if HTTPStatus.OK <= head_resp.status_code < HTTPStatus.MULTIPLE_CHOICES:
            head_headers_saved = hh
            snap = snapshot_from_response(head_resp.status_code, hh)
            skip_get = _pypdl_style_metadata_complete(hh, head_was_2xx=True)

    if not skip_get:
        ranged_get_used = True
        probe_req = merge_headers(
            dict(headers or {}),
            {**DEFAULT_HEADERS, "Range": "bytes=0-0"},
        )
        th = transport.prepare_headers(TransportHeaders.from_mapping(probe_req))
        async with transport.stream_get(parsed, headers=dict(th.items()), options=options) as resp:
            snap_get = snapshot_from_response(resp.status_code, resp.headers)
            await drain_async(resp)
            _finalize_allowed_status(resp.status_code, resp.headers)
            snap_final = merge_snapshots(snap, snap_get) if snap is not None else snap_get
    else:
        _finalize_allowed_status(cast("ProbeSnapshot", snap).status_code, cast("TransportHeaders", head_headers_saved))
        snap_final = cast("ProbeSnapshot", snap)

    return ProbeResult(
        url=parsed,
        status_code=snap_final.status_code,
        total_length=snap_final.total_length,
        etag=snap_final.etag,
        last_modified=snap_final.last_modified,
        content_type=snap_final.content_type,
        content_disposition=snap_final.content_disposition,
        accept_ranges_bytes=snap_final.accept_ranges_bytes,
        head_attempted=head_attempted,
        head_status_code=head_status_code,
        ranged_get_used=ranged_get_used,
    )
