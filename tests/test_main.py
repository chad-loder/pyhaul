"""Tests for pyhaul.__main__ — entry-point smoke tests."""

from __future__ import annotations

import subprocess
import sys

import pytest


class TestModuleEntrypoint:
    def test_help_exits_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pyhaul", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0
        assert "pyhaul" in result.stdout.lower()

    def test_version_exits_zero(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pyhaul", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0
        assert "pyhaul" in result.stdout

    def test_no_args_returns_usage_error(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "pyhaul"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 2


class TestMainNoBackend:
    def test_no_http_client_prints_guidance(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import pyhaul.__main__ as entry

        monkeypatch.setattr("pyhaul.__main__.importlib.util.find_spec", lambda _name: None)
        code = entry.main()
        assert code == 1


class TestMainDelegatesToCli:
    def test_happy_path_delegates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When a backend IS installed, __main__.main() delegates to cli.main()."""
        import pyhaul.__main__ as entry

        monkeypatch.setattr("pyhaul.cli.main", lambda argv=None: 0)
        code = entry.main()
        assert code == 0
