"""Tests for pyhaul.cli — helpers and main() integration."""

from __future__ import annotations

import argparse
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pyhaul._types import (
    CompleteHaul,
    ETag,
    HaulError,
    HaulState,
    PartialHaulError,
    ServerMisconfiguredError,
)
from pyhaul.cli import (
    ByteStandard,
    Printer,
    build_parser,
    default_output,
    format_bytes,
    format_duration,
    main,
    parse_header,
    parse_size,
    resolve_destination,
    resolve_timeout,
)


def _make_complete_haul() -> CompleteHaul:
    return CompleteHaul(elapsed=1.0, sha256="a" * 64, etag=ETag('"test"'), content_type="application/octet-stream")


# ---------------------------------------------------------------------------
# parse_size
# ---------------------------------------------------------------------------


class TestParseSize:
    def test_plain_bytes(self) -> None:
        assert parse_size("100") == 100

    def test_kibibytes(self) -> None:
        assert parse_size("512K") == 512 * 1024

    def test_mebibytes(self) -> None:
        assert parse_size("1M") == 1024 * 1024

    def test_gibibytes_with_suffix(self) -> None:
        assert parse_size("4 GiB") == 4 * 1024**3

    def test_fractional(self) -> None:
        assert parse_size("1.5M") == int(1.5 * 1024 * 1024)

    def test_empty_raises(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="empty size"):
            parse_size("")

    def test_invalid_format_raises(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="invalid size format"):
            parse_size("abc")

    def test_unknown_suffix_raises(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="unknown size suffix"):
            parse_size("10X")

    def test_zero_raises(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="must be positive"):
            parse_size("0")


# ---------------------------------------------------------------------------
# parse_header
# ---------------------------------------------------------------------------


class TestParseHeader:
    def test_valid(self) -> None:
        assert parse_header("Content-Type: application/json") == ("Content-Type", "application/json")

    def test_strips_whitespace(self) -> None:
        assert parse_header("  X-Key :  val  ") == ("X-Key", "val")

    def test_missing_colon_raises(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="must be"):
            parse_header("no-colon-here")

    def test_empty_name_raises(self) -> None:
        with pytest.raises(argparse.ArgumentTypeError, match="empty header name"):
            parse_header(": value")


# ---------------------------------------------------------------------------
# default_output
# ---------------------------------------------------------------------------


class TestDefaultOutput:
    def test_basename_from_url(self) -> None:
        assert default_output("https://example.com/file.iso") == "file.iso"

    def test_trailing_slash(self) -> None:
        assert default_output("https://example.com/dir/") == "dir"

    def test_no_path_fallback(self) -> None:
        assert default_output("https://example.com") == "index.html"

    def test_empty_path(self) -> None:
        assert default_output("https://example.com/") == "index.html"


# ---------------------------------------------------------------------------
# format_bytes
# ---------------------------------------------------------------------------


class TestFormatBytes:
    def test_bytes(self) -> None:
        assert format_bytes(500) == "500.0 B"

    def test_kibibytes(self) -> None:
        assert format_bytes(1536) == "1.5 KiB"

    def test_mebibytes(self) -> None:
        assert format_bytes(10 * 1024 * 1024) == "10.0 MiB"

    def test_si_standard(self) -> None:
        assert format_bytes(1500, std=ByteStandard.SI) == "1.5 KB"

    def test_negative_raises(self) -> None:
        with pytest.raises(ValueError, match="Negative"):
            format_bytes(-1)


# ---------------------------------------------------------------------------
# format_duration
# ---------------------------------------------------------------------------


class TestFormatDuration:
    def test_seconds(self) -> None:
        assert format_duration(42.3) == "42.3s"

    def test_minutes(self) -> None:
        assert format_duration(125) == "2m05s"

    def test_hours(self) -> None:
        assert format_duration(3723) == "1h02m03s"


# ---------------------------------------------------------------------------
# resolve_timeout
# ---------------------------------------------------------------------------


class TestResolveTimeout:
    def test_both_none(self) -> None:
        ns = argparse.Namespace(connect_timeout=None, read_timeout=None)
        assert resolve_timeout(ns) is None

    def test_connect_only(self) -> None:
        ns = argparse.Namespace(connect_timeout=5.0, read_timeout=None)
        assert resolve_timeout(ns) == (5.0, 20.0)

    def test_read_only(self) -> None:
        ns = argparse.Namespace(connect_timeout=None, read_timeout=60.0)
        assert resolve_timeout(ns) == (30.0, 60.0)

    def test_both_set(self) -> None:
        ns = argparse.Namespace(connect_timeout=3.0, read_timeout=12.0)
        assert resolve_timeout(ns) == (3.0, 12.0)


# ---------------------------------------------------------------------------
# resolve_destination
# ---------------------------------------------------------------------------


class TestResolveDestination:
    def test_explicit_output(self, tmp_path: Path) -> None:
        ns = argparse.Namespace(
            url="https://example.com/file.bin",
            output="out.bin",
            output_dir=str(tmp_path),
            remote_name=False,
        )
        result = resolve_destination(ns)
        assert result == (tmp_path / "out.bin").resolve()

    def test_derived_from_url(self) -> None:
        ns = argparse.Namespace(
            url="https://example.com/data.tar.gz",
            output=None,
            output_dir=None,
            remote_name=False,
        )
        result = resolve_destination(ns)
        assert result.name == "data.tar.gz"

    def test_output_dir_created(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "new" / "deep"
        ns = argparse.Namespace(
            url="https://example.com/f.bin",
            output=None,
            output_dir=str(out_dir),
            remote_name=False,
        )
        resolve_destination(ns)
        assert out_dir.is_dir()


# ---------------------------------------------------------------------------
# Printer
# ---------------------------------------------------------------------------


class TestPrinter:
    def test_quiet_suppresses_info(self, capsys: pytest.CaptureFixture[str]) -> None:
        p = Printer(quiet=True)
        p.info("hidden")
        assert capsys.readouterr().err == ""

    def test_info_prints_to_stderr(self, capsys: pytest.CaptureFixture[str]) -> None:
        p = Printer(quiet=False)
        p.info("visible")
        assert "visible" in capsys.readouterr().err

    def test_err_always_prints(self, capsys: pytest.CaptureFixture[str]) -> None:
        p = Printer(quiet=True)
        p.err("always")
        assert "always" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# build_parser
# ---------------------------------------------------------------------------


class TestBuildParser:
    def test_prog_name(self) -> None:
        parser = build_parser()
        assert parser.prog == "pyhaul"

    def test_all_backends_accepted(self) -> None:
        parser = build_parser()
        for backend in ("niquests", "requests", "httpx", "urllib3"):
            args = parser.parse_args(["--http-backend", backend, "https://x.com/f"])
            assert args.http_backend == backend


# ---------------------------------------------------------------------------
# main() integration — monkeypatch _run_haul / _build_client
# ---------------------------------------------------------------------------


class TestMainHelp:
    def test_help_exits_zero(self) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--help"])
        assert exc.value.code == 0

    def test_version_exits_zero(self) -> None:
        with pytest.raises(SystemExit) as exc:
            main(["--version"])
        assert exc.value.code == 0


class TestMainNoUrl:
    def test_no_url_returns_usage_error(self) -> None:
        assert main([]) == 2


class TestMainInvalidUrl:
    def test_invalid_scheme(self) -> None:
        assert main(["ftp://example.com/f"]) == 2

    def test_no_scheme(self) -> None:
        assert main(["not-a-url"]) == 2


class TestMainMissingBackend:
    def test_missing_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("pyhaul.cli.importlib.util.find_spec", lambda _name: None)
        assert main(["https://example.com/f.bin"]) == 1


class TestMainSuccessfulDownload:
    def test_success_returns_zero(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        def mock_haul(dest: Path, client: object, url: str, state: HaulState) -> CompleteHaul:
            state.valid_length = 1024
            return _make_complete_haul()

        monkeypatch.setattr("pyhaul.cli._run_haul", mock_haul)
        monkeypatch.setattr("pyhaul.cli._build_client", lambda _args: MagicMock())
        result = main(["https://example.com/f.bin", "-o", str(tmp_path / "out.bin"), "-q"])
        assert result == 0


class TestMainPartialHaul:
    def test_partial_retries_then_gives_up(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        call_count = 0

        def mock_haul(dest: Path, client: object, url: str, state: HaulState) -> CompleteHaul:
            nonlocal call_count
            call_count += 1
            raise PartialHaulError("truncated")

        monkeypatch.setattr("pyhaul.cli._run_haul", mock_haul)
        monkeypatch.setattr("pyhaul.cli._build_client", lambda _args: MagicMock())
        monkeypatch.setattr("pyhaul.cli.time.sleep", lambda _s: None)
        monkeypatch.setattr("pyhaul.cli._MAX_RETRIES", 3)
        result = main(["https://example.com/f.bin", "-o", str(tmp_path / "out.bin"), "-q"])
        assert result == 1
        assert call_count == 3


class TestMainHaulError:
    def test_haul_error_returns_one(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        def mock_haul(dest: Path, client: object, url: str, state: HaulState) -> CompleteHaul:
            raise HaulError("bad")

        monkeypatch.setattr("pyhaul.cli._run_haul", mock_haul)
        monkeypatch.setattr("pyhaul.cli._build_client", lambda _args: MagicMock())
        result = main(["https://example.com/f.bin", "-o", str(tmp_path / "out.bin"), "-q"])
        assert result == 1


class TestMainServerMisconfigured:
    def test_server_misconfigured_returns_one(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        def mock_haul(dest: Path, client: object, url: str, state: HaulState) -> CompleteHaul:
            raise ServerMisconfiguredError("no ranges")

        monkeypatch.setattr("pyhaul.cli._run_haul", mock_haul)
        monkeypatch.setattr("pyhaul.cli._build_client", lambda _args: MagicMock())
        result = main(["https://example.com/f.bin", "-o", str(tmp_path / "out.bin"), "-q"])
        assert result == 1


class TestMainNetworkError:
    def test_connection_error_returns_one(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        def mock_haul(dest: Path, client: object, url: str, state: HaulState) -> CompleteHaul:
            raise ConnectionError("refused")

        monkeypatch.setattr("pyhaul.cli._run_haul", mock_haul)
        monkeypatch.setattr("pyhaul.cli._build_client", lambda _args: MagicMock())
        result = main(["https://example.com/f.bin", "-o", str(tmp_path / "out.bin"), "-q"])
        assert result == 1

    def test_os_error_returns_one(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        def mock_haul(dest: Path, client: object, url: str, state: HaulState) -> CompleteHaul:
            raise OSError("disk full")

        monkeypatch.setattr("pyhaul.cli._run_haul", mock_haul)
        monkeypatch.setattr("pyhaul.cli._build_client", lambda _args: MagicMock())
        result = main(["https://example.com/f.bin", "-o", str(tmp_path / "out.bin"), "-q"])
        assert result == 1


class TestMainQuiet:
    def test_quiet_suppresses_progress(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def mock_haul(dest: Path, client: object, url: str, state: HaulState) -> CompleteHaul:
            state.valid_length = 1024
            return _make_complete_haul()

        monkeypatch.setattr("pyhaul.cli._run_haul", mock_haul)
        monkeypatch.setattr("pyhaul.cli._build_client", lambda _args: MagicMock())
        main(["https://example.com/f.bin", "-o", str(tmp_path / "out.bin"), "-q"])
        captured = capsys.readouterr()
        assert "→" not in captured.err
        assert "done:" not in captured.err


# ---------------------------------------------------------------------------
# _build_client — backend constructors
# ---------------------------------------------------------------------------


class TestBuildClient:
    def test_build_niquests(self) -> None:
        niquests = pytest.importorskip("niquests")
        from pyhaul.cli import _build_client

        ns = build_parser().parse_args(["--http-backend", "niquests", "https://x.com/f"])
        client = _build_client(ns)
        assert isinstance(client, niquests.Session)

    def test_build_requests(self) -> None:
        requests = pytest.importorskip("requests")
        from pyhaul.cli import _build_client

        ns = build_parser().parse_args(["--http-backend", "requests", "https://x.com/f"])
        client = _build_client(ns)
        assert isinstance(client, requests.Session)

    def test_build_httpx(self) -> None:
        httpx = pytest.importorskip("httpx")
        from pyhaul.cli import _build_client

        ns = build_parser().parse_args(["--http-backend", "httpx", "https://x.com/f"])
        client = _build_client(ns)
        try:
            assert isinstance(client, httpx.Client)
        finally:
            httpx.Client.close(client)  # narrowed through assert above

    def test_build_urllib3(self) -> None:
        urllib3 = pytest.importorskip("urllib3")
        from pyhaul.cli import _build_client

        ns = build_parser().parse_args(["--http-backend", "urllib3", "https://x.com/f"])
        client = _build_client(ns)
        assert isinstance(client, urllib3.PoolManager)

    def test_build_niquests_with_proxy(self) -> None:
        niquests = pytest.importorskip("niquests")
        from pyhaul.cli import _build_client

        ns = build_parser().parse_args(["--http-backend", "niquests", "-x", "http://proxy:8080", "https://x.com/f"])
        client = _build_client(ns)
        assert isinstance(client, niquests.Session)
        assert client.proxies == {"http": "http://proxy:8080", "https": "http://proxy:8080"}

    def test_build_niquests_insecure(self) -> None:
        niquests = pytest.importorskip("niquests")
        from pyhaul.cli import _build_client

        ns = build_parser().parse_args(["--http-backend", "niquests", "-k", "https://x.com/f"])
        client = _build_client(ns)
        assert isinstance(client, niquests.Session)
        assert client.verify is False

    def test_build_urllib3_with_proxy(self) -> None:
        urllib3 = pytest.importorskip("urllib3")
        from pyhaul.cli import _build_client

        ns = build_parser().parse_args(["--http-backend", "urllib3", "-x", "http://proxy:8080", "https://x.com/f"])
        client = _build_client(ns)
        assert isinstance(client, urllib3.ProxyManager)

    def test_build_httpx_with_timeout(self) -> None:
        httpx = pytest.importorskip("httpx")
        from pyhaul.cli import _build_client

        ns = build_parser().parse_args(["--http-backend", "httpx", "--connect-timeout", "5", "https://x.com/f"])
        client = _build_client(ns)
        try:
            assert isinstance(client, httpx.Client)
        finally:
            httpx.Client.close(client)  # narrowed through assert above

    def test_build_urllib3_insecure(self) -> None:
        urllib3 = pytest.importorskip("urllib3")
        from pyhaul.cli import _build_client

        ns = build_parser().parse_args(["--http-backend", "urllib3", "-k", "https://x.com/f"])
        client = _build_client(ns)
        assert isinstance(client, urllib3.PoolManager)

    def test_build_requests_with_proxy(self) -> None:
        requests = pytest.importorskip("requests")
        from pyhaul.cli import _build_client

        ns = build_parser().parse_args(["--http-backend", "requests", "-x", "http://proxy:8080", "https://x.com/f"])
        client = _build_client(ns)
        assert isinstance(client, requests.Session)
        assert client.proxies == {"http": "http://proxy:8080", "https": "http://proxy:8080"}
