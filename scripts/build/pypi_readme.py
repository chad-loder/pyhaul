"""Generate docs/PYPI_README.md from README.md.

Parses README.md with markdown-it-py to find a <!-- pypi-end --> HTML comment
marker, slices the document at that boundary (respecting code fences), rewrites
repo-relative links to absolute GitHub URLs, and writes the result.

Usage:
    uv run scripts/build/pypi_readme.py            # generate / update
    uv run scripts/build/pypi_readme.py --check    # exit 1 if out of date
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from markdown_it import MarkdownIt

PYPROJECT = Path("pyproject.toml")
MARKER = "pypi-end"
SRC = Path("README.md")
DEST = Path("docs/PYPI_README.md")

RELATIVE_LINK_RE = re.compile(r"\[([^\]]+)\]\((?!https?://)([^)]+)\)")


def rewrite_relative_links(text: str, base_url: str) -> str:
    """Convert repo-relative markdown links to absolute GitHub URLs."""
    text = RELATIVE_LINK_RE.sub(rf"[\1]({base_url}/\2)", text)
    # Badges use nested image+link, e.g. [![License](img.svg)](LICENSE) — the
    # single-pass regex can miss the outer (LICENSE) target; fix the common case.
    return text.replace("](LICENSE)", f"]({base_url}/LICENSE)")


def slice_at_marker(text: str, marker: str) -> str:
    """Truncate markdown at an HTML comment containing *marker*.

    Uses markdown-it-py so that markers inside fenced code blocks or inline
    code are correctly ignored.
    """
    md = MarkdownIt("commonmark", {"html": True})
    for token in md.parse(text):
        if token.type == "html_block" and marker in token.content and token.map is not None:
            return "\n".join(text.split("\n")[: token.map[0]]).rstrip() + "\n"
    return text


def repo_blob_url() -> str:
    """Read project.urls.Source from pyproject.toml and derive the blob URL."""
    cfg = tomllib.loads(PYPROJECT.read_text())
    source = cfg["project"]["urls"]["Source"]
    parsed = urlparse(source)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        _die(f"project.urls.Source is not a valid HTTP(S) URL: {source!r}")
    normalized = parsed._replace(path=str(PurePosixPath(parsed.path) / "blob" / "main"))
    return str(normalized.geturl())


def generate() -> str:
    """Build the PyPI README text from README.md."""
    text = SRC.read_text()
    text = slice_at_marker(text, MARKER)
    return rewrite_relative_links(text, repo_blob_url())


def _die(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def main() -> None:
    for required in (SRC, PYPROJECT):
        if not required.exists():
            _die(f"{required} not found (run from repo root)")

    check = "--check" in sys.argv[1:]
    expected = generate()

    if check:
        current = DEST.read_text() if DEST.exists() else None
        if current == expected:
            sys.exit(0)
        print(
            f"{DEST} is {'missing' if current is None else 'out of date'}.\n"
            f"{DEST} is generated from {SRC} by slicing at the <!-- {MARKER} --> comment\n"
            f"and rewriting relative links. To fix, run:\n"
            f"\n"
            f"    uv run scripts/build/pypi_readme.py\n"
            f"\n"
            f"Then stage the updated {DEST} alongside your {SRC} changes.",
            file=sys.stderr,
        )
        sys.exit(1)
    else:
        DEST.write_text(expected)
        src_size = SRC.stat().st_size
        pct = f"{100 * len(expected) // src_size}%" if src_size else "n/a"
        print(f"{DEST}: {len(expected):,} bytes (from {src_size:,}, {pct})")


if __name__ == "__main__":
    main()
