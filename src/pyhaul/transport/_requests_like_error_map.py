"""Map requests-compatible exception modules to :mod:`pyhaul.transport.errors`."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import ModuleType
from typing import Any

from pyhaul.transport.errors import (
    TransportConnectionError,
    TransportError,
    TransportHTTPError,
    TransportTLSError,
)

try:
    import urllib3.exceptions as _urllib3_exc
except ImportError:  # pragma: no cover
    _urllib3_exc = None  # type: ignore[assignment]  # ty: ignore[invalid-assignment]


@contextmanager
def map_requests_like_transport_errors(exc: ModuleType) -> Iterator[None]:
    """Translate *exc* (``niquests.exceptions`` or ``requests.exceptions``) to transport errors."""
    try:
        yield
    except TransportError:
        raise
    except exc.HTTPError as e:
        status = _response_status_code(e)
        raise TransportHTTPError(str(e), status_code=status) from e
    except exc.SSLError as e:
        raise TransportTLSError(str(e)) from e
    except exc.RequestException as e:
        raise TransportConnectionError(str(e)) from e
    except Exception as e:
        if _urllib3_exc is not None and isinstance(e, _urllib3_exc.HTTPError):
            raise TransportConnectionError(str(e)) from e
        raise


def _response_status_code(exc: Any) -> int | None:
    resp = getattr(exc, "response", None)
    if resp is None:
        return None
    code = getattr(resp, "status_code", None)
    return int(code) if code is not None else None
