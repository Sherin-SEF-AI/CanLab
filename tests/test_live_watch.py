"""The live watch: a fitted baseline, batches of new frames, events out."""
import numpy as np
import pandas as pd
import pytest

from canlab.core.anomaly_detector import ZScoreBaseline, score_dataframe
from canlab.core.live_watch import KINDS, LiveWatch


def frames(ids=("100", "200"), seconds=10.0, rate_hz=50.0, t0=0.0, seed=0):
    """Steady traffic: byte 0 wanders a little, the rest are quiet."""
    rng = np.random.default_rng(seed)
    rows = []
    for cid in ids:
        n = int(seconds * rate_hz)
        for i in range(n):
            rows.append({"Timestamp": t0 + i / rate_hz, "ID": cid, "Bus": 0, "DLC": 8,
                         "B0": 100 + int(rng.normal(0, 3)), "B1": 7, "B2": int(cid, 16) >> 8,
                         "B3": 0, "B4": 0, "B5": 0, "B6": 0, "B7": i & 0xFF})
    return pd.DataFrame(rows).sort_values("Timestamp", kind="stable").reset_index(drop=True)


def frame(ts, cid, b0=100, b1=7, b2=None, b7=0):
    b2 = int(cid, 16) >> 8 if b2 is None else b2
    return {"Timestamp": ts, "ID": cid, "Bus": 0, "DLC": 8, "B0": b0, "B1": b1,
            "B2": b2, "B3": 0, "B4": 0, "B5": 0, "B6": 0, "B7": b7}


@pytest.fixture
def watch():
    w = LiveWatch(threshold=0.6, cooldown_s=2.0, min_silent_s=0.5)
    info = w.fit(frames())
    assert info["ids"] == 2 and w.is_fitted
    return w


def test_a_byte_leaving_its_band_raises_one_event_per_cooldown():
    watch = LiveWatch(threshold=0.6, cooldown_s=2.0, min_silent_s=100.0)   # silence off
    watch.fit(frames())
    normal = frames(t0=10.0, seconds=1.0)
    assert watch.observe(normal) == []
    bad = pd.DataFrame([frame(11.0 + i * 0.02, "100", b1=200) for i in range(30)])
    events = watch.observe(bad)
    assert [e.kind for e in events] == ["bytes"]
    ev = events[0]
    assert ev.can_id == "100" and ev.ts == 11.0 and ev.score >= 0.6
    assert "B1=200" in ev.detail and "30 frames" in ev.detail
    # still bad 1 s later: inside the cooldown, so nothing more
    assert watch.observe(pd.DataFrame([frame(12.0, "100", b1=200)])) == []
    # 2 s after the first event it reports again
    again = watch.observe(pd.DataFrame([frame(13.1, "100", b1=200)]))
    assert [e.kind for e in again] == ["bytes"]
    assert watch.stats()["by_kind"]["bytes"] == 2


def test_an_unknown_id_is_reported_once(watch):
    first = watch.observe(pd.DataFrame([frame(10.0, "7FF"), frame(10.1, "7FF")]))
    assert [(e.kind, e.can_id) for e in first] == [("new_id", "7FF")]
    assert watch.observe(pd.DataFrame([frame(10.2, "7FF")])) == []
    assert watch.stats()["new_ids"] == ["7FF"]


def test_silence_is_reported_once_and_rearmed_on_return(watch):
    watch.observe(frames(t0=10.0, seconds=1.0))            # both seen up to 10.98
    only_200 = frames(ids=("200",), t0=11.0, seconds=1.0)  # 100 goes quiet
    events = watch.observe(only_200)
    assert [(e.kind, e.can_id) for e in events] == [("silent", "100")]
    assert "no frame for" in events[0].detail and events[0].ts == pytest.approx(11.98)
    assert watch.observe(frames(ids=("200",), t0=12.0, seconds=1.0)) == []
    assert watch.stats()["silent_now"] == ["100"]
    back = watch.observe(pd.DataFrame([frame(13.0, "100")]))
    assert back == [] and watch.stats()["silent_now"] == []
    again = watch.observe(frames(ids=("200",), t0=14.0, seconds=1.0))
    assert [(e.kind, e.can_id) for e in again] == [("silent", "100")]


def test_a_burst_is_reported(watch):
    watch.observe(frames(t0=10.0, seconds=0.5))
    burst = pd.DataFrame([frame(10.5 + i * 0.002, "100", b7=i & 0xFF) for i in range(100)])
    events = watch.observe(burst)
    assert [e.kind for e in events] == ["burst"]
    assert "100 frames" in events[0].detail and events[0].can_id == "100"


def test_events_carry_the_frame_clock_not_the_wall_clock(watch):
    batch = pd.DataFrame([frame(5000.25, "100", b1=250)])
    (ev,) = watch.observe(batch)
    assert ev.ts == 5000.25 and ev.as_dict()["ts"] == 5000.25
    assert watch.events(since_ts=5000.0)[0] is ev and watch.events(since_ts=6000.0) == []


def test_observe_before_fit_is_a_no_op():
    w = LiveWatch()
    assert w.observe(frames()) == [] and w.stats()["fitted"] is False


def test_nan_bytes_are_safe(watch):
    short = pd.DataFrame([{"Timestamp": 10.0, "ID": "100", "Bus": 0, "DLC": 2,
                           "B0": 100, "B1": 7, **{f"B{i}": np.nan for i in range(2, 8)}}])
    assert watch.observe(short) == []


def test_vectorised_scores_match_the_row_scorer():
    df = frames()
    b = ZScoreBaseline()
    b.fit(df)
    rows = df.head(200)
    per_row = [b.score(str(r["ID"]), dict(r)) for _, r in rows.iterrows()]
    scored = score_dataframe(rows, b)
    assert np.allclose(scored["anomaly_score"].to_numpy(), per_row)
    assert list(scored.columns) == list(rows.columns) + ["anomaly_score"]
    assert b.period_stats("100")["median_dt"] == pytest.approx(0.02)
    assert b.period_stats("nope") is None


def test_every_kind_is_countable(watch):
    assert set(watch.stats()["by_kind"]) == set(KINDS)
