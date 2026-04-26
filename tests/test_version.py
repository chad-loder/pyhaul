from __future__ import annotations

import re

import pyhaul


def test_version_is_semver_or_sentinel() -> None:
    assert re.match(r"^\d+\.\d+\.\d+([.+-].+)?$", pyhaul.__version__)
