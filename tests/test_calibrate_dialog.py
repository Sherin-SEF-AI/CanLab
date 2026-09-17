"""The calibration dialog: load a file, run off the GUI thread, add signals."""
import os
import time

import numpy as np
import pytest

from canlab.core.log_parser import parse_log_file

SAMPLE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "canlab", "sample_data", "sample_kona_drive.csv")


def _wait_until(pred, qcore, timeout=30.0):
    end = time.time() + timeout
    while time.time() < end:
        qcore.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def state(qcore):
    from canlab.core.state import AppState
    st = AppState()
    st.frames_df = parse_log_file(SAMPLE)
    return st


@pytest.fixture
def reference(tmp_path):
    t = np.arange(0, 10, 0.1)
    speed = 60 + 20 * np.sin(2 * np.pi * t / 5)
    p = tmp_path / "gps.csv"
    p.write_text("time,speed (km/h)\n" + "".join(f"{a + 1.0:.1f},{b:.3f}\n"
                                                 for a, b in zip(t, speed)))
    return str(p)


def test_dialog_runs_the_sweep_and_adds_signals_as_one_undo_step(qcore, state, reference):
    from canlab.ui.calibrate_dialog import CalibrateDialog
    dlg = CalibrateDialog(state)
    series = dlg.load_file(reference)
    assert [s.name for s in series] == ["speed"]
    assert dlg.series_table.rowCount() == 1 and dlg.series_table.item(0, 2).text() == "km/h"
    assert dlg.btn_run.isEnabled()

    dlg.lag_spin.setValue(2.0)
    dlg._run()
    assert dlg.btn_cancel.isEnabled() and not dlg.btn_run.isEnabled()
    assert _wait_until(lambda: dlg.results_table.rowCount() > 0 and dlg.btn_run.isEnabled(),
                       qcore)
    verdicts = [dlg.results_table.item(r, 10).text() for r in range(dlg.results_table.rowCount())]
    assert verdicts.count("PASS") == 4
    assert dlg.results_table.item(0, 0).text() == "speed"
    assert dlg.results_table.item(0, 9).text() == "1.0"           # the lag it found

    before = len(state.dbc_signals)
    dlg.results_table.selectAll()
    dlg._add_selected()
    assert len(state.dbc_signals) == before + dlg.results_table.rowCount()
    names = [s["signal_name"] for s in state.dbc_signals[before:]]
    assert names[0] == "speed_0A6" and len(set(names)) == len(names)
    assert all(s["unit"] == "km/h" for s in state.dbc_signals[before:before + 4])
    assert state.undo_dbc() and len(state.dbc_signals) == before

    dlg._add_best()
    assert len(state.dbc_signals) == before + 1
    assert state.dbc_signals[-1]["byte_order"] == "big"
    assert state.dbc_signals[-1]["start_bit"] % 8 == 7          # DBC Motorola MSB
    dlg.close()


def test_cancel_stops_a_running_sweep(qcore, state, reference):
    from canlab.ui.calibrate_dialog import CalibrateDialog
    dlg = CalibrateDialog(state)
    dlg.load_file(reference)
    dlg._run()
    dlg._cancel()
    assert _wait_until(lambda: dlg.btn_run.isEnabled(), qcore)
    assert dlg._worker.stopped
    dlg.close()


def test_a_bad_file_is_reported_not_raised(qcore, state, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from canlab.ui.calibrate_dialog import CalibrateDialog
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    bad = tmp_path / "bad.csv"
    bad.write_text("only\n1\n2\n")
    dlg = CalibrateDialog(state)
    assert dlg.load_file(str(bad)) == []
    assert shown and not dlg.btn_run.isEnabled()
    dlg.close()
