"""The PyPI long description must match the generator (``uv run scripts/build/pypi_readme.py``)."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def test_pypi_readme_is_up_to_date() -> None:
    root = Path(__file__).resolve().parent.parent
    path = root / "scripts" / "build" / "pypi_readme.py"
    spec = importlib.util.spec_from_file_location("_pypi_readme", path)
    assert spec is not None
    assert spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    expected = mod.generate()
    assert (root / "docs" / "PYPI_README.md").read_text() == expected
