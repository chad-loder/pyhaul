from __future__ import annotations

from pyhaul.headers import merge_headers


def test_merge_headers_preserves_metadata() -> None:
    user = {"User-Agent": "my-bot/1.0", "Authorization": "Bearer token"}
    pyhaul = {"Accept-Encoding": "identity"}
    merged = merge_headers(user, pyhaul)
    assert merged["User-Agent"] == "my-bot/1.0"
    assert merged["Authorization"] == "Bearer token"
    assert merged["Accept-Encoding"] == "identity"


def test_merge_headers_structural_wins() -> None:
    # pyhaul's identity MUST win over user's gzip
    user = {"Accept-Encoding": "gzip", "Range": "bytes=0-9"}
    pyhaul = {"Accept-Encoding": "identity", "Range": "bytes=10-19"}
    merged = merge_headers(user, pyhaul)
    assert merged["Accept-Encoding"] == "identity"
    assert merged["Range"] == "bytes=10-19"


def test_merge_headers_safety_can_be_overridden() -> None:
    # User-provided Cache-Control should win over pyhaul's safety default
    user = {"Cache-Control": "max-age=3600"}
    pyhaul = {"Cache-Control": "no-store, no-transform"}
    merged = merge_headers(user, pyhaul)
    assert merged["Cache-Control"] == "max-age=3600"


def test_merge_headers_safety_default_applied() -> None:
    # If user omits Cache-Control, pyhaul's safety default is applied
    user = {"User-Agent": "bot"}
    pyhaul = {"Cache-Control": "no-store, no-transform"}
    merged = merge_headers(user, pyhaul)
    assert merged["Cache-Control"] == "no-store, no-transform"


def test_merge_headers_case_insensitivity() -> None:
    # User-cased keys are removed if pyhaul replaces them
    user = {"accept-encoding": "gzip"}
    pyhaul = {"Accept-Encoding": "identity"}
    merged = merge_headers(user, pyhaul)
    assert "accept-encoding" not in merged
    assert merged["Accept-Encoding"] == "identity"


def test_merge_headers_casing_preserved_for_metadata() -> None:
    user = {"X-Custom-Header": "value"}
    merged = merge_headers(user, {})
    assert "X-Custom-Header" in merged
    assert merged["X-Custom-Header"] == "value"
