"""Offline, deterministic tests for core.opendbc_matcher.match_capture.

These build a tiny fake index directly (no network, no cache) and assert that
ID-set overlap scoring ranks the right vehicle first.
"""
import pytest

from core import opendbc_matcher


# A small hand-built index standing in for the parsed opendbc DBCs.
FAKE_INDEX = {
    "hyundai_kona.dbc": {
        "frame_ids": ["018", "260", "316", "544"],
        "message_names": ["MDPS12", "SAS11", "TCS13", "CLU11"],
    },
    "toyota_corolla.dbc": {
        "frame_ids": ["0AA", "0B4", "224", "2C1"],
        "message_names": ["STEER_ANGLE", "SPEED", "BRAKE", "GAS"],
    },
    "honda_civic.dbc": {
        "frame_ids": ["158", "1D0", "255", "324"],
        "message_names": ["ENGINE_DATA", "POWERTRAIN", "STEERING", "WHEELS"],
    },
}


def test_exact_match_ranks_first():
    # Observe exactly the Kona's IDs -> Jaccard 1.0 -> ranked first.
    observed = {"018", "260", "316", "544"}
    ranked = opendbc_matcher.match_capture(observed, index=FAKE_INDEX)

    assert ranked[0]["dbc"] == "hyundai_kona.dbc"
    assert ranked[0]["score"] == pytest.approx(1.0)
    assert ranked[0]["coverage"] == pytest.approx(1.0)
    assert ranked[0]["matched_ids"] == ["018", "260", "316", "544"]
    assert ranked[0]["message_count"] == 4


def test_partial_match_ranks_by_overlap():
    # 3 of Kona's 4 IDs plus one unrelated ID; nothing else overlaps.
    observed = {"018", "260", "316", "7FF"}
    ranked = opendbc_matcher.match_capture(observed, index=FAKE_INDEX)

    assert ranked[0]["dbc"] == "hyundai_kona.dbc"
    # intersection 3, union 5 -> 0.6
    assert ranked[0]["score"] == pytest.approx(3 / 5)
    assert ranked[0]["matched_ids"] == ["018", "260", "316"]
    # DBCs with zero overlap must not appear.
    assert all(r["dbc"] == "hyundai_kona.dbc" for r in ranked)


def test_normalization_makes_widths_line_up():
    # Observed IDs given with 0x prefix / lowercase / short width still match.
    observed = {"0x18", "0X260", "316", "544"}
    ranked = opendbc_matcher.match_capture(observed, index=FAKE_INDEX)

    assert ranked[0]["dbc"] == "hyundai_kona.dbc"
    assert ranked[0]["score"] == pytest.approx(1.0)


def test_top_k_limits_results():
    observed = {"018", "260", "0AA", "0B4", "158", "1D0"}  # overlaps all three
    ranked = opendbc_matcher.match_capture(observed, top_k=2, index=FAKE_INDEX)
    assert len(ranked) == 2


def test_empty_inputs_return_empty():
    assert opendbc_matcher.match_capture(set(), index=FAKE_INDEX) == []
    assert opendbc_matcher.match_capture({"018"}, index={}) == []


def test_offline_no_cache_returns_empty(monkeypatch):
    # With no explicit index and no cached index, must not crash.
    monkeypatch.setattr(opendbc_matcher, "load_index", lambda: {})
    assert opendbc_matcher.match_capture({"018", "260"}) == []
