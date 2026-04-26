"""Single source of truth for the package version.

Resolved from the installed package metadata (``pyproject.toml``'s
``[project].version``) so there is only one place to update on release.
Falls back to a sentinel when running directly from a non-installed
source tree.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("pyhaul")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"
