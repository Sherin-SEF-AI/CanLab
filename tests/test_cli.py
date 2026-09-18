"""The headless command line.

Runs in a subprocess with no display, because that is the point of it.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SAMPLE = Path(__file__).resolve().parent.parent / "canlab" / "sample_data" / "sample_kona_drive.csv"


def cli(*args, cwd=None):
    env = dict(os.environ)
    env.pop("QT_QPA_PLATFORM", None)          # must not need one
    env.pop("DISPLAY", None)
    return subprocess.run([sys.executable, "-m", "canlab.cli", *args],
                          capture_output=True, text=True, timeout=300,
                          cwd=cwd, env=env, stdin=subprocess.DEVNULL)


def test_ids_lists_every_id_with_rate():
    r = cli("ids", str(SAMPLE))
    assert r.returncode == 0, r.stderr
    assert "6610 frames, 10 IDs" in r.stdout
    assert "0A6" in r.stdout and "Hz" in r.stdout


def test_detect_reports_the_generator_checksums(tmp_path):
    r = cli("detect", str(SAMPLE), "--quiet", "--json", str(tmp_path / "r.json"))
    assert r.returncode == 0, r.stderr
    assert "checksums 7" in r.stdout, r.stdout
    report = json.loads((tmp_path / "r.json").read_text())
    assert report["capture"]["frames"] == 6610
    assert set(report) >= {"counters_checksums", "boundaries", "flags", "enums",
                           "multiplexers"}


def test_detect_drafts_a_dbc_that_cantools_accepts(tmp_path):
    """Several detectors claim the same bits; the draft must not overlap."""
    import cantools

    out = tmp_path / "draft.dbc"
    r = cli("detect", str(SAMPLE), "--quiet", "--dbc", str(out))
    assert r.returncode == 0, r.stderr
    db = cantools.database.load_file(str(out))
    assert sum(len(m.signals) for m in db.messages) > 0


def test_decode_writes_one_row_per_frame(tmp_path):
    dbc = tmp_path / "draft.dbc"
    cli("detect", str(SAMPLE), "--quiet", "--dbc", str(dbc))
    out = tmp_path / "decoded.csv"
    r = cli("decode", str(SAMPLE), "--dbc", str(dbc), "--out", str(out))
    assert r.returncode == 0, r.stderr
    assert "decoded 6610 rows" in r.stdout
    assert out.exists()


@pytest.mark.parametrize("ext", ["csv", "blf", "asc", "log"])
def test_convert_round_trips_every_format(tmp_path, ext):
    from canlab.core.log_parser import parse_log_file

    out = tmp_path / f"conv.{ext}"
    r = cli("convert", str(SAMPLE), str(out))
    assert r.returncode == 0, r.stderr
    back = parse_log_file(str(out))
    assert len(back) == 6610, f".{ext} came back with {len(back)} frames"
    assert back["ID"].nunique() == 10


def test_convert_csv_is_savvycan_layout(tmp_path):
    """So the file opens in SavvyCAN itself, not only in CanLab."""
    out = tmp_path / "conv.csv"
    cli("convert", str(SAMPLE), str(out))
    head = out.read_text().splitlines()[:2]
    assert head[0].startswith("Time Stamp,ID,Extended,Dir,Bus,LEN,D1")
    assert head[1].endswith(",")                # SavvyCAN's trailing comma


def test_runs_without_qt_or_a_display():
    r = cli("ids", str(SAMPLE))
    assert r.returncode == 0
    probe = subprocess.run([sys.executable, "-c",
                            "import sys, canlab.cli; "
                            "print(any(m.startswith('PyQt6') for m in sys.modules))"],
                           capture_output=True, text=True)
    assert probe.stdout.strip() == "False", "the CLI imported Qt"


def test_missing_file_is_a_clean_error():
    r = cli("ids", "/nonexistent/capture.csv")
    assert r.returncode != 0
    assert "no such file" in (r.stdout + r.stderr)


def test_capture_help_needs_no_qt():
    r = cli("capture", "--help")
    assert r.returncode == 0, r.stderr
    assert "--gpio" in r.stdout and "--http" in r.stdout and "never asks" in r.stdout


def test_capture_on_the_virtual_backend_exits_clean_with_marks(tmp_path):
    out = tmp_path / "kit"
    r = cli("capture", "--interface", "virtual", "--channel", "vbus-cli-test",
            "--duration", "1", "--out", str(out), "--no-project")
    assert r.returncode == 0, r.stderr
    assert "0 frames" in r.stdout and (out / "marks.json").is_file()
    assert not list(out.glob("*.csv"))            # nothing arrived, nothing written


def test_capture_refuses_a_bad_pin_map(tmp_path):
    r = cli("capture", "--interface", "virtual", "--channel", "vbus-x", "--duration", "1",
            "--out", str(tmp_path / "k"), "--gpio", "17=brake,17=horn")
    assert r.returncode != 0 and "given twice" in r.stderr


def test_capture_names_a_missing_saved_adapter(tmp_path):
    r = cli("capture", "--adapter", "nothere", "--adapters-json", str(tmp_path / "none.json"),
            "--duration", "1", "--out", str(tmp_path / "k"))
    assert r.returncode != 0 and "no adapter named" in r.stderr


def test_a_listing_piped_into_head_is_not_an_error():
    """`canlab-cli ids capture.csv | head -1` used to end in a BrokenPipeError
    traceback and exit 1. Unbuffered stdout, which is what CI and Docker give
    you, made it happen every time."""
    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env.pop("QT_QPA_PLATFORM", None)
    reader = subprocess.Popen(["head", "-1"], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, text=True)
    writer = subprocess.Popen([sys.executable, "-m", "canlab.cli", "ids", str(SAMPLE)],
                              stdout=reader.stdin, stderr=subprocess.PIPE,
                              text=True, env=env)
    reader.stdin.close()
    first = reader.stdout.read()
    reader.wait(timeout=60)
    err = writer.communicate(timeout=60)[1]
    assert "frames" in first
    assert "BrokenPipeError" not in err and "Traceback" not in err, err
    assert writer.returncode == 0, f"exit {writer.returncode}: {err}"
