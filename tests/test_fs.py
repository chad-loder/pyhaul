"""Tests for pyhaul.fs -- path-length validation, safe paths, and sidecars."""

from __future__ import annotations

import os
import sys
import unicodedata
from pathlib import Path
from unittest.mock import patch

import pytest

from pyhaul.fs import (
    _FALLBACK_NAME_MAX,
    _FALLBACK_PATH_MAX,
    _LONGEST_SIDECAR_SUFFIX,
    _WIN32_LONG_PATH_MAX,
    _WIN32_LONG_PREFIX,
    _existing_ancestor,
    _get_name_max,
    _get_path_max,
    _name_byte_len,
    done_path_for,
    path_fits,
    safe_open_path,
)

SUFFIX_LEN = len(_LONGEST_SIDECAR_SUFFIX)


def _real_name_max(tmp_path: Path) -> int:
    """Query the actual NAME_MAX for the test filesystem."""
    if sys.platform == "win32":
        return 255
    try:
        return os.pathconf(str(tmp_path), "PC_NAME_MAX")
    except (OSError, ValueError):
        return _FALLBACK_NAME_MAX


# ---------------------------------------------------------------------------
# Filename component (NAME_MAX) boundary tests
# ---------------------------------------------------------------------------


class TestNameMaxBoundary:
    """Test path_fits against the filename component limit."""

    def test_well_under_limit(self, tmp_path: Path) -> None:
        dest = tmp_path / "short.bin"
        assert path_fits(dest) is True

    def test_just_barely_under_limit(self, tmp_path: Path) -> None:
        name_max = _real_name_max(tmp_path)
        stem_len = name_max - SUFFIX_LEN - 1
        dest = tmp_path / ("x" * stem_len)
        assert path_fits(dest) is True

    def test_exactly_at_limit(self, tmp_path: Path) -> None:
        name_max = _real_name_max(tmp_path)
        stem_len = name_max - SUFFIX_LEN
        dest = tmp_path / ("x" * stem_len)
        assert path_fits(dest) is True

    def test_barely_over_limit(self, tmp_path: Path) -> None:
        name_max = _real_name_max(tmp_path)
        stem_len = name_max - SUFFIX_LEN + 1
        dest = tmp_path / ("x" * stem_len)
        assert path_fits(dest) is False

    def test_way_over_limit(self, tmp_path: Path) -> None:
        name_max = _real_name_max(tmp_path)
        stem_len = name_max + 100
        dest = tmp_path / ("x" * stem_len)
        assert path_fits(dest) is False


# ---------------------------------------------------------------------------
# Full path string length boundary tests
# ---------------------------------------------------------------------------


class TestPathMaxBoundary:
    """Test path_fits against the full path string length limit."""

    FAKE_LIMIT = 500

    def _make_dest(self, tmp_path: Path, fill: int) -> str:
        """Build a dest string whose total length + suffix == FAKE_LIMIT + fill."""
        prefix = str(tmp_path) + "/"
        name_len = self.FAKE_LIMIT - len(prefix) - SUFFIX_LEN + fill
        if name_len <= 0:
            pytest.skip("tmp_path too long for synthetic limit")
        return prefix + "a" * name_len

    def test_well_under_path_limit(self) -> None:
        dest = "/tmp/small.bin"
        assert path_fits(dest) is True

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows: synthetic ~500 char dest crosses long-path threshold; effective length no longer matches patched path_max",
    )
    def test_just_barely_under_path_limit(self, tmp_path: Path) -> None:
        dest = self._make_dest(tmp_path, fill=-1)
        with (
            patch("pyhaul.fs._get_path_max", return_value=self.FAKE_LIMIT),
            patch("pyhaul.fs._get_name_max", return_value=32768),
        ):
            assert path_fits(dest) is True

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows: synthetic ~500 char dest crosses long-path threshold; effective length no longer matches patched path_max",
    )
    def test_exactly_at_path_limit(self, tmp_path: Path) -> None:
        dest = self._make_dest(tmp_path, fill=0)
        with (
            patch("pyhaul.fs._get_path_max", return_value=self.FAKE_LIMIT),
            patch("pyhaul.fs._get_name_max", return_value=32768),
        ):
            assert path_fits(dest) is True

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows: synthetic ~500 char dest crosses long-path threshold; effective length no longer matches patched path_max",
    )
    def test_barely_over_path_limit(self, tmp_path: Path) -> None:
        dest = self._make_dest(tmp_path, fill=1)
        with (
            patch("pyhaul.fs._get_path_max", return_value=self.FAKE_LIMIT),
            patch("pyhaul.fs._get_name_max", return_value=32768),
        ):
            assert path_fits(dest) is False

    def test_way_over_path_limit(self) -> None:
        with (
            patch("pyhaul.fs._get_path_max", return_value=50),
            patch("pyhaul.fs._get_name_max", return_value=32768),
        ):
            dest = "/tmp/" + "z" * 100
            assert path_fits(dest) is False


class TestNoResolve:
    """Verify that path_fits checks the string as-is on POSIX."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX no-resolve test")
    def test_relative_path_not_expanded(self) -> None:
        """A short relative path should not be penalised by a long cwd."""
        short_relative = "data/foo.bin"
        with patch("pyhaul.fs._get_name_max", return_value=255):
            assert path_fits(short_relative) is True

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX no-resolve test")
    def test_relative_path_string_is_what_gets_measured(self) -> None:
        """Path length check uses len(str) not len(resolved)."""
        rel = "../" * 50 + "file.bin"
        expected_len = len(rel + _LONGEST_SIDECAR_SUFFIX)
        tight_limit = expected_len
        with (
            patch("pyhaul.fs._get_name_max", return_value=255),
            patch("pyhaul.fs._get_path_max", return_value=tight_limit),
        ):
            assert path_fits(rel) is True
        with (
            patch("pyhaul.fs._get_name_max", return_value=255),
            patch("pyhaul.fs._get_path_max", return_value=tight_limit - 1),
        ):
            assert path_fits(rel) is False


class TestPlatformDefaults:
    """Verify the correct platform limit is used by default."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX test")
    def test_posix_queries_pathconf(self, tmp_path: Path) -> None:
        dest = tmp_path / "file.bin"
        extended = str(dest) + _LONGEST_SIDECAR_SUFFIX
        path_max = _get_path_max(str(tmp_path))
        assert isinstance(path_max, int)
        assert path_max > 0
        assert len(extended) < path_max
        assert path_fits(dest) is True

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows test")
    def test_win32_effective_limit_is_32767(self) -> None:
        dest = "C:\\Users\\test\\file.bin"
        assert len(dest + _LONGEST_SIDECAR_SUFFIX) < _WIN32_LONG_PATH_MAX
        assert path_fits(dest) is True


# ---------------------------------------------------------------------------
# safe_open_path
# ---------------------------------------------------------------------------


class TestSafeOpenPath:
    """Verify safe_open_path behavior across platforms."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX passthrough test")
    def test_posix_passthrough_absolute(self) -> None:
        p = "/home/user/downloads/file.bin"
        assert safe_open_path(p) == p

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX passthrough test")
    def test_posix_passthrough_relative(self) -> None:
        p = "../data/file.bin"
        assert safe_open_path(p) == p

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX passthrough test")
    def test_posix_accepts_pathlike(self, tmp_path: Path) -> None:
        dest = tmp_path / "file.bin"
        assert safe_open_path(dest) == str(dest)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows prefix test")
    def test_win32_short_path_unchanged(self) -> None:
        p = "C:\\short\\file.bin"
        result = safe_open_path(p)
        assert not result.startswith(_WIN32_LONG_PREFIX)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows prefix test")
    def test_win32_long_path_gets_prefix(self) -> None:
        p = "C:\\Users\\" + "d" * 300 + "\\file.bin"
        result = safe_open_path(p)
        assert result.startswith(_WIN32_LONG_PREFIX)

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows prefix test")
    def test_win32_already_prefixed_unchanged(self) -> None:
        p = _WIN32_LONG_PREFIX + "C:\\long\\path\\file.bin"
        assert safe_open_path(p) == p

    def test_win32_logic_via_direct_call(self) -> None:
        """Test the \\\\?\\ prefix logic directly on any platform."""
        from pyhaul.fs import _win32_safe_open_path

        short = "/tmp/short/file.bin"
        long_path = "/tmp/" + "d" * 300 + "/file.bin"
        already = _WIN32_LONG_PREFIX + "C:\\data\\file.bin"

        result_short = _win32_safe_open_path(short)
        assert not result_short.startswith(_WIN32_LONG_PREFIX)

        result_long = _win32_safe_open_path(long_path)
        assert result_long.startswith(_WIN32_LONG_PREFIX)

        assert _win32_safe_open_path(already) == already

    def test_win32_short_path_preserves_relative(self) -> None:
        """Short relative paths stay relative -- just slash-normalised."""
        from pyhaul.fs import _win32_safe_open_path

        rel = "data/downloads/file.bin"
        result = _win32_safe_open_path(rel)
        assert result == "data\\downloads\\file.bin"
        assert not result.startswith(_WIN32_LONG_PREFIX)

    def test_win32_long_path_resolves_dotdot(self) -> None:
        """Long paths must be canonical (no ..) for the \\\\?\\ API."""
        from pyhaul.fs import _win32_safe_open_path

        long_rel = "../" * 100 + "file.bin"
        result = _win32_safe_open_path(long_rel)
        assert result.startswith(_WIN32_LONG_PREFIX)
        assert ".." not in result


class TestPathFitsWithSafeOpenPath:
    """Verify path_fits accounts for what safe_open_path will actually do."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX test")
    def test_posix_path_measured_as_provided(self) -> None:
        dest = "/tmp/file.bin"
        extended = dest + _LONGEST_SIDECAR_SUFFIX
        tight = len(extended)
        with (
            patch("pyhaul.fs._get_name_max", return_value=255),
            patch("pyhaul.fs._get_path_max", return_value=tight),
        ):
            assert path_fits(dest) is True
        with (
            patch("pyhaul.fs._get_name_max", return_value=255),
            patch("pyhaul.fs._get_path_max", return_value=tight - 1),
        ):
            assert path_fits(dest) is False

    def test_win32_long_prefix_adds_to_length(self) -> None:
        """The \\\\?\\ prefix (4 chars) is included in the length check."""
        with (
            patch("pyhaul.fs.sys") as mock_sys,
            patch("pyhaul.fs._get_name_max", return_value=32768),
        ):
            mock_sys.platform = "win32"
            base = "C:\\Users\\" + "x" * 300 + "\\file.bin"
            extended = base + _LONGEST_SIDECAR_SUFFIX
            abspath = extended.replace("/", "\\")
            prefixed = _WIN32_LONG_PREFIX + abspath
            tight = len(prefixed)
            with (
                patch("pyhaul.fs._get_path_max", return_value=tight),
                patch("pyhaul.fs.safe_open_path", return_value=prefixed),
            ):
                assert path_fits(base) is True
            with (
                patch("pyhaul.fs._get_path_max", return_value=tight - 1),
                patch("pyhaul.fs.safe_open_path", return_value=prefixed),
            ):
                assert path_fits(base) is False


# ---------------------------------------------------------------------------
# Unicode filename length (bytes vs chars)
# ---------------------------------------------------------------------------


class TestUnicodeNames:
    """POSIX NAME_MAX is in bytes; NTFS NAME_MAX is in UTF-16 code units."""

    def test_ascii_one_byte_per_char(self) -> None:
        assert _name_byte_len("hello.bin") == 9

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX UTF-8 test")
    def test_posix_multibyte_utf8(self) -> None:
        name = "\u00fc" * 10  # \u00fc = 2 bytes NFC, 3 bytes NFD (u + combining umlaut)
        if sys.platform == "darwin":
            assert _name_byte_len(name) == 30  # APFS stores NFD
        else:
            assert _name_byte_len(name) == 20

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX UTF-8 test")
    def test_posix_emoji_four_bytes(self) -> None:
        name = "\U0001f600" * 5  # U+1F600 = 4 bytes UTF-8, no NFD expansion
        assert _name_byte_len(name) == 20

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows UTF-16 test")
    def test_win32_bmp_one_code_unit(self) -> None:
        name = "\u00fc" * 10  # BMP char = 1 UTF-16 code unit each
        assert _name_byte_len(name) == 10

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows UTF-16 test")
    def test_win32_astral_plane_two_code_units(self) -> None:
        name = "\U0001f600" * 5  # astral = 2 UTF-16 code units (surrogate pair)
        assert _name_byte_len(name) == 10

    def test_astral_plane_surrogate_pairs_cross_platform(self) -> None:
        """Astral-plane chars use 2 UTF-16 code units; verify via utf-16-le."""
        name = "\U0001f600" * 5
        utf16_units = len(name.encode("utf-16-le")) // 2
        assert utf16_units == 10
        if sys.platform == "win32":
            assert _name_byte_len(name) == utf16_units

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX byte-length test")
    def test_multibyte_pushes_past_limit(self, tmp_path: Path) -> None:
        name_max = _real_name_max(tmp_path)
        char_budget = name_max - SUFFIX_LEN
        name = "\u00fc" * char_budget  # 2 bytes each -> double the budget
        dest = tmp_path / name
        assert path_fits(dest) is False


# ---------------------------------------------------------------------------
# Regression: macOS NFD decomposition in filename length
# ---------------------------------------------------------------------------


class TestMacOSNfdNameLength:
    r"""On macOS (APFS), filenames are stored in NFD (decomposed) form.

    Characters like \u00fc (U+00FC) decompose into u + combining diaeresis,
    growing from 2 UTF-8 bytes (NFC) to 3 (NFD).  Without NFD
    normalization, _name_byte_len would undercount and path_fits would
    return a false positive -- "fits!" when the filesystem would reject it.
    """

    @pytest.mark.skipif(sys.platform != "darwin", reason="macOS NFD test")
    def test_nfd_expansion_counted_in_name_limit(self, tmp_path: Path) -> None:
        """A name that fits in NFC bytes but not NFD bytes must fail."""
        name_max = _real_name_max(tmp_path)

        nfc_char = "\u00e9"  # e-acute: 2 bytes NFC, 3 bytes NFD (e + combining accent)
        nfc_bytes = len(nfc_char.encode("utf-8"))
        nfd_bytes = len(unicodedata.normalize("NFD", nfc_char).encode("utf-8"))
        assert nfc_bytes == 2
        assert nfd_bytes == 3

        budget = name_max - SUFFIX_LEN
        repeat = budget // nfc_bytes  # fits if measured in NFC bytes
        stem = nfc_char * repeat
        nfd_stem_bytes = len(unicodedata.normalize("NFD", stem + _LONGEST_SIDECAR_SUFFIX).encode("utf-8"))

        if nfd_stem_bytes <= name_max:
            pytest.skip("need a repeat count that exceeds NAME_MAX in NFD")

        dest = tmp_path / stem
        assert path_fits(dest) is False, (
            f"NFD-expanded name is {nfd_stem_bytes} bytes but NAME_MAX is {name_max}; path_fits should have rejected it"
        )

    @pytest.mark.skipif(sys.platform != "darwin", reason="macOS NFD test")
    def test_nfd_stable_chars_unaffected(self, tmp_path: Path) -> None:
        """ASCII names have no NFD expansion -- same result on all platforms."""
        assert _name_byte_len("hello.bin") == 9

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX test")
    def test_nfd_expansion_not_applied_on_linux(self) -> None:
        """Linux ext4/XFS store names as-is (NFC); no NFD penalty."""
        if sys.platform == "darwin":
            pytest.skip("this test is for non-macOS POSIX")
        name = "\u00fc" * 10  # 2 bytes each in NFC
        assert _name_byte_len(name) == 20


# ---------------------------------------------------------------------------
# Regression: POSIX path length measured in bytes not characters
# ---------------------------------------------------------------------------


class TestPosixPathLengthInBytes:
    """PATH_MAX on POSIX is a byte limit on the UTF-8 encoded string.

    Before the fix, path_fits measured len(str) (character count), which
    undercounts for multi-byte characters.  A path with many 3-byte
    chars could slip past a character-based check but exceed PATH_MAX
    in bytes.
    """

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX byte-length test")
    def test_multibyte_path_exceeds_byte_limit(self, tmp_path: Path) -> None:
        """A path of N chars can be >N bytes; byte-based limit must catch it."""
        mb_char = "\u4e16"  # U+4E16 = 3 UTF-8 bytes, 1 Python char
        assert len(mb_char) == 1
        assert len(mb_char.encode("utf-8")) == 3

        prefix = str(tmp_path) + "/"
        prefix_bytes = len(prefix.encode("utf-8"))
        suffix_bytes = len(_LONGEST_SIDECAR_SUFFIX.encode("utf-8"))

        fake_limit = prefix_bytes + 90 + suffix_bytes
        repeat = 40  # 40 chars x 3 bytes = 120 bytes for the stem
        stem = mb_char * repeat
        dest = prefix + stem

        char_len = len(dest + _LONGEST_SIDECAR_SUFFIX)
        byte_len = len((dest + _LONGEST_SIDECAR_SUFFIX).encode("utf-8"))
        assert char_len < fake_limit, "sanity: char count should be under limit"
        assert byte_len > fake_limit, "sanity: byte count should exceed limit"

        with (
            patch("pyhaul.fs._get_name_max", return_value=32768),
            patch("pyhaul.fs._get_path_max", return_value=fake_limit),
        ):
            assert path_fits(dest) is False, (
                f"path is {byte_len} bytes but limit is {fake_limit}; byte-based check should reject"
            )

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX byte-length test")
    def test_ascii_path_bytes_equal_chars(self, tmp_path: Path) -> None:
        """For pure ASCII, byte length == char length -- no difference."""
        dest = str(tmp_path / "plain_ascii_file.bin")
        extended = dest + _LONGEST_SIDECAR_SUFFIX
        assert len(extended) == len(extended.encode("utf-8"))

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX byte-length test")
    def test_at_byte_boundary_fits(self, tmp_path: Path) -> None:
        """A path exactly at the byte limit should pass."""
        mb_char = "\u00e9"  # U+00E9 = 2 UTF-8 bytes
        prefix = str(tmp_path) + "/"
        prefix_bytes = len(prefix.encode("utf-8"))
        suffix_bytes = len(_LONGEST_SIDECAR_SUFFIX.encode("utf-8"))

        repeat = 20  # 20 x 2 bytes = 40 bytes
        stem = mb_char * repeat
        dest = prefix + stem

        byte_len = len((dest + _LONGEST_SIDECAR_SUFFIX).encode("utf-8"))
        fake_limit = byte_len  # exactly at limit

        assert byte_len == prefix_bytes + (repeat * 2) + suffix_bytes

        with (
            patch("pyhaul.fs._get_name_max", return_value=32768),
            patch("pyhaul.fs._get_path_max", return_value=fake_limit),
        ):
            assert path_fits(dest) is True

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX byte-length test")
    def test_one_byte_over_limit_fails(self, tmp_path: Path) -> None:
        """One byte over the limit should fail."""
        mb_char = "\u00e9"  # U+00E9 = 2 UTF-8 bytes
        prefix = str(tmp_path) + "/"
        prefix_bytes = len(prefix.encode("utf-8"))
        suffix_bytes = len(_LONGEST_SIDECAR_SUFFIX.encode("utf-8"))

        repeat = 20
        stem = mb_char * repeat
        dest = prefix + stem

        byte_len = len((dest + _LONGEST_SIDECAR_SUFFIX).encode("utf-8"))
        fake_limit = byte_len - 1

        assert byte_len == prefix_bytes + (repeat * 2) + suffix_bytes

        with (
            patch("pyhaul.fs._get_name_max", return_value=32768),
            patch("pyhaul.fs._get_path_max", return_value=fake_limit),
        ):
            assert path_fits(dest) is False


# ---------------------------------------------------------------------------
# Custom suffix
# ---------------------------------------------------------------------------


class TestCustomSuffix:
    def test_shorter_suffix_fits(self, tmp_path: Path) -> None:
        name_max = _real_name_max(tmp_path)
        stem_len = name_max - 5  # ".done" is 5 chars
        dest = tmp_path / ("s" * stem_len)
        assert path_fits(dest, suffix=".done") is True

    def test_longer_suffix_fails(self, tmp_path: Path) -> None:
        name_max = _real_name_max(tmp_path)
        stem_len = name_max - 5 + 1
        dest = tmp_path / ("s" * stem_len)
        assert path_fits(dest, suffix=".done") is False


# ---------------------------------------------------------------------------
# _existing_ancestor
# ---------------------------------------------------------------------------


class TestExistingAncestor:
    def test_existing_dir(self, tmp_path: Path) -> None:
        assert _existing_ancestor(str(tmp_path)) == str(tmp_path.resolve())

    def test_nonexistent_child(self, tmp_path: Path) -> None:
        deep = str(tmp_path / "a" / "b" / "c")
        result = _existing_ancestor(deep)
        assert Path(result).exists()
        assert str(tmp_path.resolve()) in result

    def test_root_fallback(self) -> None:
        impossible = "/nonexistent_root_abc123/deep/path"
        result = _existing_ancestor(impossible)
        assert Path(result).exists()


# ---------------------------------------------------------------------------
# _get_name_max / _get_path_max fallbacks
# ---------------------------------------------------------------------------


class TestFallbacks:
    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX pathconf test")
    def test_name_max_returns_positive_int(self, tmp_path: Path) -> None:
        result = _get_name_max(str(tmp_path))
        assert isinstance(result, int)
        assert result > 0

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX pathconf test")
    def test_path_max_returns_positive_int(self, tmp_path: Path) -> None:
        result = _get_path_max(str(tmp_path))
        assert isinstance(result, int)
        assert result > 0

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX pathconf test")
    def test_pathconf_oserror_falls_back_name(self, tmp_path: Path) -> None:
        with patch("pyhaul.fs.os.pathconf", side_effect=OSError("mocked")):
            assert _get_name_max(str(tmp_path)) == _FALLBACK_NAME_MAX

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX pathconf test")
    def test_pathconf_oserror_falls_back_path(self, tmp_path: Path) -> None:
        with patch("pyhaul.fs.os.pathconf", side_effect=OSError("mocked")):
            assert _get_path_max(str(tmp_path)) == _FALLBACK_PATH_MAX


# ---------------------------------------------------------------------------
# done_path_for
# ---------------------------------------------------------------------------


class TestDonePathFor:
    def test_simple(self) -> None:
        assert done_path_for(Path("/tmp/file.bin")) == Path("/tmp/file.bin.done")

    def test_no_extension(self) -> None:
        assert done_path_for(Path("/tmp/archive")) == Path("/tmp/archive.done")

    def test_double_extension(self) -> None:
        assert done_path_for(Path("/tmp/data.tar.gz")) == Path("/tmp/data.tar.gz.done")
