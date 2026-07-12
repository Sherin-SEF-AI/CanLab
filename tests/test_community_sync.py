"""Tests for the community-profile filename sanitizer (path-traversal, C4)."""
import pytest

pytest.importorskip("PyQt6")   # community_sync imports QThread at module load

from core.community_sync import _safe_profile_id


@pytest.mark.parametrize("bad", [
    "../../.canlab/plugins/evil",
    "a/b/c",
    "..",
    ".hidden",
    "",
    None,
    "x" * 300,
    "..\\..\\windows",
])
def test_sanitizer_has_no_separators_or_traversal(bad):
    s = _safe_profile_id(bad)
    assert "/" not in s
    assert "\\" not in s
    assert ".." not in s
    assert not s.startswith(".")
    assert len(s) <= 128
    assert s  # never empty


def test_normal_id_preserved():
    assert _safe_profile_id("hyundai_kona_2019") == "hyundai_kona_2019"
