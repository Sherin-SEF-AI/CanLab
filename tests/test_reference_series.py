"""Reference files: CSV columns and GPX tracks become series on a clock."""
import numpy as np
import pytest

from canlab.core.reference_series import (
    ReferenceSeries, haversine_m, load_csv, load_gpx, load_reference_file,
    split_name_unit,
)

GPX = """<?xml version="1.0"?>
<gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">
<trk><name>lap</name><trkseg>
<trkpt lat="42.0000" lon="-81.0000"><ele>170</ele><time>2021-03-25T15:19:08Z</time></trkpt>
<trkpt lat="42.0000" lon="-81.0000"><ele>170</ele></trkpt>
<trkpt lat="42.0001" lon="-81.0000"><ele>171</ele><time>2021-03-25T15:19:09Z</time></trkpt>
<trkpt lat="42.0002" lon="-81.0000"><ele>172</ele><time>2021-03-25T15:19:10Z</time></trkpt>
<trkpt lat="42.0003" lon="-81.0000"><ele>173</ele><time>2021-03-25T15:19:11Z</time></trkpt>
</trkseg></trk></gpx>
"""


def test_named_csv_columns_become_series_with_units(tmp_path):
    p = tmp_path / "ref.csv"
    p.write_text("Time,speed (km/h),rpm [1/min],note\n0.0,10,800,a\n0.5,12,900,b\n1.0,14,1000,c\n")
    series = {s.name: s for s in load_csv(p)}
    assert set(series) == {"speed", "rpm"}          # the text column is skipped
    assert series["speed"].unit == "km/h" and series["rpm"].unit == "1/min"
    assert series["speed"].values.tolist() == [10.0, 12.0, 14.0]
    assert series["speed"].span_s == 1.0


def test_a_timestamp_value_csv_gives_one_series_called_value(tmp_path):
    p = tmp_path / "ref.csv"
    p.write_text("timestamp,value\n0,1\n1,2\n2,3\n")
    (s,) = load_reference_file(p)
    assert s.name == "value" and s.unit == "" and s.samples == 3


def test_iso_timestamps_become_epoch_seconds(tmp_path):
    p = tmp_path / "ref.csv"
    p.write_text("time,speed\n2021-03-25T15:19:08Z,1\n2021-03-25T15:19:09Z,2\n2021-03-25T15:19:10.5Z,3\n")
    (s,) = load_csv(p)
    assert s.ts[0] == pytest.approx(1616685548.0)
    assert np.diff(s.ts).tolist() == [1.0, 1.5]


def test_series_drop_nan_and_sort_by_time():
    s = ReferenceSeries("x", [2.0, 0.0, 1.0, np.nan], [20.0, 0.0, np.nan, 30.0])
    assert s.ts.tolist() == [0.0, 2.0] and s.values.tolist() == [0.0, 20.0]


def test_gpx_gives_speed_from_haversine_and_altitude(tmp_path):
    p = tmp_path / "lap.gpx"
    p.write_text(GPX)
    series = {s.name: s for s in load_gpx(p)}
    assert set(series) == {"speed", "altitude", "latitude", "longitude"}
    expected = haversine_m(42.0, -81.0, 42.0001, -81.0) * 3.6     # one segment per second
    assert series["speed"].samples == 4                          # the untimed point is skipped
    assert series["speed"].values == pytest.approx(np.full(4, expected), rel=0.02)
    assert series["speed"].unit == "km/h"
    assert series["altitude"].values.tolist() == [170.0, 171.0, 172.0, 173.0]
    assert series["latitude"].values[-1] == 42.0003


def test_a_stationary_track_has_zero_speed(tmp_path):
    p = tmp_path / "still.gpx"
    still = GPX.replace('lat="42.0001"', 'lat="42.0000"').replace(
        'lat="42.0002"', 'lat="42.0000"').replace('lat="42.0003"', 'lat="42.0000"')
    p.write_text(still)
    series = {s.name: s for s in load_gpx(p)}
    assert series["speed"].values.tolist() == [0.0, 0.0, 0.0, 0.0]


def test_gpx_without_timed_points_is_refused(tmp_path):
    p = tmp_path / "bare.gpx"
    p.write_text('<gpx><trk><trkseg><trkpt lat="1" lon="2"/><trkpt lat="1" lon="3"/>'
                 '</trkseg></trk></gpx>')
    with pytest.raises(ValueError):
        load_gpx(p)


def test_split_name_unit_handles_both_bracket_styles():
    assert split_name_unit("speed (km/h)") == ("speed", "km/h")
    assert split_name_unit("alt [m]") == ("alt", "m")
    assert split_name_unit("rpm") == ("rpm", "")


def test_missing_file_is_a_file_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_reference_file(tmp_path / "nope.csv")
