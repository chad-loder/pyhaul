"""Console entrypoint and ``python -m pyhaul`` shim.

The CLI needs at least one HTTP client extra: ``niquests``, ``requests``,
``httpx``, ``urllib3``, or ``wreq`` (install ``pyhaul[niquests]``,
``pyhaul[requests]``, ``pyhaul[httpx]``, ``pyhaul[urllib3]``, or ``pyhaul[wreq]``
respectively).
"""

from __future__ import annotations

import importlib.util
import sys

_HTTP_CLIENT_MODULES = ("niquests", "requests", "httpx", "urllib3", "wreq")


def main() -> int:
    """Entry point for ``python -m pyhaul``; checks for an HTTP backend then delegates to the CLI."""
    if not any(importlib.util.find_spec(name) for name in _HTTP_CLIENT_MODULES):
        sys.stderr.write(
            "Error: no HTTP client is installed. Install at least one of:\n"
            "  pip install pyhaul[niquests]   # default CLI backend\n"
            "  pip install pyhaul[requests]\n"
            "  pip install pyhaul[httpx]\n"
            "  pip install pyhaul[urllib3]\n",
        )
        return 1
    from pyhaul.cli import main as cli_main

    return cli_main()


if __name__ == "__main__":
    raise SystemExit(main())
