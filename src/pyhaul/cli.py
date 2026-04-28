"""Minimal curl-compatible CLI for one-off pyhaul downloads.

Uses one optional HTTP stack at runtime (``--http-backend``: ``niquests``,
``requests``, ``httpx``, or ``urllib3``; default ``niquests``).  Install a
matching extra (e.g. ``pyhaul[niquests]``).

Exit codes follow UNIX convention::

    0   success
    1   generic download / HTTP error
    2   usage error
    130 interrupted (SIGINT / SIGTERM)
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import os
import re
import signal
import sys
import threading
import time
from enum import StrEnum
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING, Any, Final
from urllib.parse import urlparse

from pyhaul._types import (
    CompleteHaul,
    HaulError,
    HaulState,
    PartialHaulError,
    ServerMisconfiguredError,
    UnexpectedStatusError,
    parse_url,
)
from pyhaul._version import __version__

if TYPE_CHECKING:
    from collections.abc import Sequence

log = logging.getLogger("pyhaul.cli")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_INTERRUPTED = 130

_SEC_PER_MIN = 60
_MIN_PER_HOUR = 60
_MAX_RETRIES = 20


class ByteStandard(StrEnum):
    """Unit system for human-readable byte formatting (IEC binary vs SI decimal)."""

    IEC = "IEC"
    SI = "SI"


_BYTE_CONFIG: Final = {
    ByteStandard.IEC: (1024.0, ("B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB", "ZiB", "YiB")),
    ByteStandard.SI: (1000.0, ("B", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB")),
}

_base, _units = _BYTE_CONFIG[ByteStandard.IEC]
# Every IEC name in :data:`_BYTE_CONFIG` (B … YiB) plus the usual shorthands (K, MB, T, …).
_SIZE_SUFFIXES: dict[str, int] = {
    alias: int(_base) ** mag
    for mag, unit in enumerate(_units)
    for alias in (unit.upper(), f"{unit.strip('iB')}B", unit.strip("iB"))
}

_SIZE_RE = re.compile(r"^(\d*\.?\d+)\s*([A-Z]*)$")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def parse_size(value: str) -> int:
    """Parse a human, IEC-1024 size into bytes (e.g. ``1M``, ``512K``, ``4 GiB``).

    ``Content-Length`` and ``Content-Range`` in HTTP are plain decimal byte
    counts (or a structured range), not ``1M``-style values—use :func:`int` or
    a range parser for those, not this helper.
    """
    s = value.strip()
    if not s:
        raise argparse.ArgumentTypeError("empty size")
    match = _SIZE_RE.match(s.upper())
    if not match:
        raise argparse.ArgumentTypeError(f"invalid size format: {value!r}")

    digits, suffix = match.groups()
    multiplier = _SIZE_SUFFIXES.get(suffix)
    if multiplier is None:
        raise argparse.ArgumentTypeError(f"unknown size suffix: {suffix!r}")
    out = int(float(digits) * multiplier)
    if out <= 0:
        raise argparse.ArgumentTypeError(f"size must be positive: {value!r}")
    return out


def parse_header(raw: str) -> tuple[str, str]:
    """Parse a ``Name: Value`` header string (curl-compatible)."""
    if ":" not in raw:
        raise argparse.ArgumentTypeError(f"header must be 'Name: Value', got {raw!r}")
    name, _, value = raw.partition(":")
    name = name.strip()
    value = value.strip()
    if not name:
        raise argparse.ArgumentTypeError(f"empty header name in {raw!r}")
    return name, value


def default_output(url: str) -> str:
    """Derive a local filename from a URL, curl ``-O`` style."""
    path = urlparse(url).path
    name = path.rstrip("/").rsplit("/", 1)[-1] if path else ""
    return name or "index.html"


def format_bytes(size: float, std: ByteStandard = ByteStandard.IEC, prec: int = 1) -> str:
    """Short human-readable byte count (``1.5 MiB``)."""
    if size < 0:
        raise ValueError("Negative size")
    base, units = _BYTE_CONFIG[std]
    mag = 0
    while size >= base and mag < len(units) - 1:
        size /= base
        mag += 1
    return f"{size:.{prec}f} {units[mag]}"


def format_duration(seconds: float) -> str:
    """Format *seconds* as a compact human-readable duration string."""
    if seconds < _SEC_PER_MIN:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), _SEC_PER_MIN)
    if m < _MIN_PER_HOUR:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, _MIN_PER_HOUR)
    return f"{h}h{m:02d}m{s:02d}s"


# ---------------------------------------------------------------------------
# Output / progress
# ---------------------------------------------------------------------------


class Printer:
    """Stderr-only info / error printer.

    curl writes content to stdout and status to stderr; we do the same so
    pipelines stay grep-friendly.
    """

    def __init__(self, *, quiet: bool) -> None:
        self.quiet = quiet

    def info(self, msg: str) -> None:
        """Write an informational message to stderr (suppressed in quiet mode)."""
        if self.quiet:
            return
        sys.stderr.write(msg + "\n")
        sys.stderr.flush()

    def err(self, msg: str) -> None:
        """Write a prefixed error message to stderr (always shown)."""
        sys.stderr.write(f"pyhaul: {msg}\n")
        sys.stderr.flush()


# ---------------------------------------------------------------------------
# Argparse
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    """Construct the ``pyhaul`` CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="pyhaul",
        description=(
            "Resumable, cursor-based HTTP downloader. "
            "Writes <file>.part + <file>.part.ctrl during transfer; "
            "re-running the same command resumes where it stopped."
        ),
        epilog=(
            "Examples:\n"
            "  pyhaul -o file.iso https://example.com/file.iso\n"
            "  pyhaul --http-backend httpx -o out.bin https://example.com/file.bin\n"
            "  pyhaul -x socks5h://127.0.0.1:9050 http://abc.onion/blob.bin\n"
            "  pyhaul -H 'Cookie: x=1' -A 'my-bot/1.0' https://host/f.zip\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", nargs="?", help="URL to download")

    out = parser.add_argument_group("output")
    out.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="write output to FILE (default: derived from URL)",
    )
    out.add_argument(
        "-O",
        "--remote-name",
        action="store_true",
        help="use URL basename as output filename (default when -o is absent)",
    )
    out.add_argument(
        "--output-dir",
        metavar="DIR",
        help="directory to save file in (created if missing)",
    )

    net = parser.add_argument_group("network")
    net.add_argument(
        "-x",
        "--proxy",
        metavar="URL",
        help="proxy URL (e.g. socks5h://127.0.0.1:9050, http://host:3128)",
    )
    net.add_argument(
        "-H",
        "--header",
        action="append",
        default=[],
        metavar="HEADER",
        type=parse_header,
        help="add custom header 'Name: Value' (repeatable)",
    )
    net.add_argument(
        "-A",
        "--user-agent",
        metavar="NAME",
        help="User-Agent string",
    )
    net.add_argument(
        "--http-backend",
        choices=("niquests", "requests", "httpx", "urllib3"),
        default="niquests",
        metavar="NAME",
        help="HTTP client library (default: niquests)",
    )
    net.add_argument(
        "-k",
        "--insecure",
        action="store_true",
        help="skip TLS certificate verification",
    )
    net.add_argument(
        "--connect-timeout",
        type=float,
        metavar="SECS",
        help="maximum seconds to wait for connect",
    )
    net.add_argument(
        "--read-timeout",
        type=float,
        metavar="SECS",
        help="maximum seconds between response chunks (default: 4x connect-timeout)",
    )

    log_g = parser.add_argument_group("logging")
    log_g.add_argument(
        "-q",
        "--quiet",
        "-s",
        "--silent",
        action="store_true",
        help="suppress progress output",
    )
    log_g.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="verbose logging (repeat for debug)",
    )

    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"pyhaul {__version__}",
    )
    return parser


# ---------------------------------------------------------------------------
# Session setup
# ---------------------------------------------------------------------------


def resolve_timeout(args: argparse.Namespace) -> float | tuple[float, float] | None:
    """Derive a timeout value from parsed CLI arguments."""
    ct = args.connect_timeout
    rt = args.read_timeout
    if ct is None and rt is None:
        return None
    if ct is None:
        ct = 30.0
    if rt is None:
        rt = ct * 4
    return (ct, rt)


def resolve_destination(args: argparse.Namespace) -> Path:
    """Determine the output file path from parsed CLI arguments."""
    name = args.output or default_output(args.url)
    dest = Path(name)
    if args.output_dir:
        out_dir = Path(args.output_dir).expanduser()
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / (dest.name if dest.is_absolute() else dest)
    return dest.expanduser().resolve()


def _build_client(args: argparse.Namespace) -> object:
    """Create a raw HTTP client for the selected backend.

    Returns the native client object; ``haul()`` auto-wraps it via
    session dispatch.
    """
    backend = args.http_backend
    match backend:
        case "niquests":
            return _build_niquests(args)
        case "requests":
            return _build_requests(args)
        case "httpx":
            return _build_httpx(args)
        case "urllib3":
            return _build_urllib3(args)
        case _:
            msg = f"unknown http backend: {backend!r}"
            raise ValueError(msg)


def _build_niquests(args: argparse.Namespace) -> object:
    import niquests

    sess = niquests.Session()
    if args.proxy:
        sess.proxies = {"http": args.proxy, "https": args.proxy}
    if args.insecure:
        sess.verify = False
    return sess


def _build_requests(args: argparse.Namespace) -> object:
    import requests

    sess = requests.Session()
    if args.proxy:
        sess.proxies = {"http": args.proxy, "https": args.proxy}
    if args.insecure:
        sess.verify = False
    return sess


def _build_httpx(args: argparse.Namespace) -> object:
    import httpx

    kw: dict[str, object] = {}
    if args.proxy:
        kw["proxy"] = args.proxy
    if args.insecure:
        kw["verify"] = False
    timeout = resolve_timeout(args)
    if timeout is not None:
        kw["timeout"] = timeout
    return httpx.Client(**kw)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


def _build_urllib3(args: argparse.Namespace) -> object:
    import urllib3

    kw: dict[str, object] = {}
    timeout = resolve_timeout(args)
    if timeout is not None:
        kw["timeout"] = urllib3.Timeout(connect=timeout[0], read=timeout[1]) if isinstance(timeout, tuple) else timeout
    if args.insecure:
        kw["cert_reqs"] = "CERT_NONE"
    if args.proxy:
        return urllib3.ProxyManager(args.proxy, **kw)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]
    return urllib3.PoolManager(**kw)  # type: ignore[arg-type]  # ty: ignore[invalid-argument-type]


def _close_client(client: object) -> None:
    obj: Any = client
    if hasattr(obj, "close") and callable(obj.close):
        obj.close()  # ty: ignore[call-top-callable]
    elif hasattr(obj, "clear") and callable(obj.clear):
        obj.clear()  # ty: ignore[call-top-callable]


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------


class _InterruptState:
    """Async-signal-safe interrupt state.

    Signal handlers must only perform async-signal-safe operations.
    We restrict ourselves to: setting a ``threading.Event``, assigning an
    attribute, and calling ``os._exit`` (a raw syscall wrapper that is
    documented as safe from a signal handler). No stdio, no logging, no
    lock acquisition.
    """

    def __init__(self) -> None:
        self.event = threading.Event()
        self.signum: int = 0

    def set(self, signum: int) -> None:
        self.signum = signum
        self.event.set()

    @property
    def is_set(self) -> bool:
        return self.event.is_set()


def _install_signal_handlers(state: _InterruptState) -> None:
    """First signal sets flag; second signal hard-exits."""

    def handle(signum: int, _frame: FrameType | None) -> None:
        if state.is_set:
            os._exit(EXIT_INTERRUPTED)
        state.set(signum)

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, handle)
        except (OSError, ValueError):
            log.debug("could not install handler for %s", sig)


def _configure_logging(verbosity: int) -> None:
    if verbosity >= 2:  # noqa: PLR2004
        level = logging.DEBUG
    elif verbosity == 1:
        level = logging.INFO
    else:
        level = logging.WARNING
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    """Run the ``pyhaul`` CLI, returning a POSIX exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.url:
        parser.print_usage(sys.stderr)
        sys.stderr.write("pyhaul: URL is required\n")
        return EXIT_USAGE

    _configure_logging(args.verbose)

    printer = Printer(quiet=args.quiet)

    try:
        url = parse_url(args.url)
    except ValueError as exc:
        printer.err(f"invalid URL: {exc}")
        return EXIT_USAGE

    try:
        dest = resolve_destination(args)
    except OSError as exc:
        printer.err(f"could not prepare output path: {exc}")
        return EXIT_ERROR

    if importlib.util.find_spec(args.http_backend) is None:
        printer.err(
            f"HTTP backend {args.http_backend!r} is not installed. Try: pip install pyhaul[{args.http_backend}]",
        )
        return EXIT_ERROR

    client = _build_client(args)
    try:
        return _download_loop(url, client, dest, printer)
    finally:
        _close_client(client)


def _download_loop(  # noqa: C901, PLR0911
    url: str,
    client: object,
    dest: Path,
    printer: Printer,
) -> int:
    interrupt = _InterruptState()
    _install_signal_handlers(interrupt)

    printer.info(f"{url} → {dest}")
    t_start = time.monotonic()
    state = HaulState()

    for attempt in range(1, _MAX_RETRIES + 1):
        if interrupt.is_set:
            name = signal.Signals(interrupt.signum).name
            printer.info(f"{name} received; resume state saved")
            return EXIT_INTERRUPTED

        state.bytes_read = 0

        try:
            result = _run_haul(dest, client, url, state)
        except PartialHaulError as exc:
            printer.info(f"partial (attempt {attempt}/{_MAX_RETRIES}): {exc.reason}; resuming…")
            if state.bytes_read == 0:
                time.sleep(min(2**attempt, 30))
            continue
        except KeyboardInterrupt:
            return EXIT_INTERRUPTED
        except UnexpectedStatusError as exc:
            _print_err(f"unexpected HTTP {exc.status_code}: {exc.reason}")
            return EXIT_ERROR
        except ServerMisconfiguredError as exc:
            _print_err(f"server misconfigured: {exc}")
            return EXIT_ERROR
        except HaulError as exc:
            _print_err(str(exc))
            return EXIT_ERROR
        except OSError as exc:
            _print_err(f"i/o error: {exc}")
            return EXIT_ERROR
        except Exception as exc:  # noqa: BLE001
            _print_err(f"network error: {exc}")
            return EXIT_ERROR

        n = state.valid_length
        elapsed = time.monotonic() - t_start
        avg = n / elapsed if elapsed > 0 else 0.0
        printer.info(
            f"done: {format_bytes(n)} in "
            f"{format_duration(elapsed)} ({format_bytes(int(avg))}/s)  "
            f"sha256={result.sha256[:16]}…"
        )
        return EXIT_OK

    printer.err(f"gave up after {_MAX_RETRIES} resume attempts")
    return EXIT_ERROR


def _run_haul(
    dest: Path,
    client: object,
    url: str,
    state: HaulState,
) -> CompleteHaul:
    """Call engine.haul.  Exceptions propagate to the caller."""
    from pyhaul.engine import haul

    return haul(url, client, dest=dest, state=state)


def _print_err(msg: str) -> None:
    sys.stderr.write(f"pyhaul: {msg}\n")
    sys.stderr.flush()
