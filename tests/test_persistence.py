"""Settings, plugins and project files: what survives a restart, and what runs.

Interface, bitrate, REST port and vehicle profile used to live only in memory,
so every launch started from hard-coded defaults. Every plugin in the plugin
directory was executed at startup whether or not the user had enabled it.
"""
import json

import pytest

pytest.importorskip("PyQt6")

from PyQt6.QtCore import QSettings

from canlab.core import plugin_loader


@pytest.fixture
def isolated_settings(tmp_path, monkeypatch):
    """Point the app's settings at a temp file so tests never touch real ones."""
    store = QSettings(str(tmp_path / "canlab.ini"), QSettings.Format.IniFormat)
    from canlab import settings_dialog
    monkeypatch.setattr(settings_dialog, "settings", lambda: store)
    yield store
    store.clear()
    store.sync()


@pytest.fixture
def make_dialog(qcore):
    """Build SettingsDialogs and destroy them deterministically.

    Widgets left to the garbage collector are torn down at unpredictable
    points, which crashes Qt when another test is mid-teardown.
    """
    created = []

    def factory():
        from canlab.settings_dialog import SettingsDialog
        dlg = SettingsDialog()
        created.append(dlg)
        return dlg

    yield factory
    for dlg in created:
        dlg.close()
        dlg.deleteLater()
    qcore.processEvents()


def test_settings_round_trip(isolated_settings, make_dialog):
    dlg = make_dialog()
    dlg.iface_combo.setCurrentText("pcan")
    dlg.channel_edit.setText("can3")
    dlg.bitrate_combo.setCurrentText("250000")
    dlg.chk_canfd.setChecked(True)
    dlg.rest_port_spin.setValue(9100)
    idx = dlg.profile_combo.findData("toyota")
    dlg.profile_combo.setCurrentIndex(idx)
    dlg._save_persisted()

    fresh = make_dialog()
    fresh._load_persisted()
    assert fresh.iface_combo.currentText() == "pcan"
    assert fresh.channel_edit.text() == "can3"
    assert fresh.bitrate_combo.currentText() == "250000"
    assert fresh.chk_canfd.isChecked() is True
    assert fresh.rest_port_spin.value() == 9100
    assert fresh.profile_combo.currentData() == "toyota"


def test_multibus_config_round_trip(isolated_settings, make_dialog):
    from PyQt6.QtWidgets import QTableWidgetItem

    from canlab.settings_dialog import SettingsDialog
    dlg = make_dialog()
    dlg.multibus_table.insertRow(0)
    for ci, value in enumerate(["powertrain", "socketcan", "can1", "500000"]):
        dlg.multibus_table.setItem(0, ci, QTableWidgetItem(value))
    dlg._save_persisted()

    stored = json.loads(isolated_settings.value(SettingsDialog.S_MULTIBUS, "[]", str))
    assert stored == [{"name": "powertrain", "interface": "socketcan",
                       "channel": "can1", "bitrate": 500000}]
    fresh = make_dialog()
    fresh._load_persisted()
    assert fresh.get_multibus_config() == stored


def test_keyring_failure_does_not_break_saving(monkeypatch):
    import keyring

    from canlab import settings_dialog
    def boom(*_a, **_k):
        raise RuntimeError("no keyring backend")
    monkeypatch.setattr(keyring, "set_password", boom)
    settings_dialog.save_api_key("sk-test")      # must not raise
    assert settings_dialog.load_api_key() in ("", "sk-test")


def test_model_lists_are_current():
    from canlab.settings_dialog import AI_MODELS
    assert "claude-sonnet-5" in AI_MODELS["Anthropic"]
    assert "claude-haiku-4-5-20251001" not in AI_MODELS["Anthropic"]
    for retired in ("llama-3.1-70b-versatile", "mixtral-8x7b-32768", "gemma2-9b-it"):
        assert retired not in AI_MODELS["Groq"], f"{retired} was decommissioned"


def test_the_default_model_is_one_the_settings_offer():
    """The AI tab used to default to a model the settings list did not contain."""
    from canlab.core.ai_client import ANTHROPIC_DEFAULT_MODEL
    from canlab.settings_dialog import AI_MODELS
    assert ANTHROPIC_DEFAULT_MODEL in AI_MODELS["Anthropic"]


# ── plugins ──────────────────────────────────────────────────────────────────

@pytest.fixture
def plugin_dir(tmp_path, monkeypatch, isolated_settings):
    monkeypatch.setattr(plugin_loader, "PLUGIN_DIR", tmp_path)
    (tmp_path / "demo.py").write_text(
        'PLUGIN_NAME = "Demo"\nPLUGIN_VERSION = "1.0"\n'
        'import pathlib; pathlib.Path(r"%s").write_text("ran")\n'
        'def register(app): pass\n' % (tmp_path / "ran.txt"))
    return tmp_path


def test_plugins_are_disabled_until_enabled(plugin_dir):
    found = plugin_loader.discover_plugins()
    assert len(found) == 1 and found[0]["name"] == "Demo"
    assert found[0]["enabled"] is False, "a plugin must not run just by being present"
    assert not (plugin_dir / "ran.txt").exists()


def test_discovery_does_not_execute_plugin_code(plugin_dir):
    plugin_loader.discover_plugins()
    assert not (plugin_dir / "ran.txt").exists(), "metadata must be read statically"


def test_enabling_a_plugin_is_remembered(plugin_dir):
    path = str(plugin_dir / "demo.py")
    plugin_loader.set_enabled(path, True)
    assert path in plugin_loader.enabled_paths()
    assert plugin_loader.discover_plugins()[0]["enabled"] is True
    plugin_loader.set_enabled(path, False)
    assert plugin_loader.discover_plugins()[0]["enabled"] is False


def test_only_enabled_plugins_are_activated(plugin_dir):
    plugins = plugin_loader.discover_plugins()
    assert plugin_loader.activate_plugins(plugins, object()) == []
    assert not (plugin_dir / "ran.txt").exists()

    plugin_loader.set_enabled(str(plugin_dir / "demo.py"), True)
    activated = plugin_loader.activate_plugins(plugin_loader.discover_plugins(), object())
    assert activated == ["Demo"]
    assert (plugin_dir / "ran.txt").exists()


# ── project files ────────────────────────────────────────────────────────────

def _state_with_data():
    from canlab.core.log_parser import make_row
    from canlab.core.state import AppState
    st = AppState()
    st.append_rows([make_row(i * 0.01, 0x1A0, False, 0, bytes(range(8)))
                    for i in range(10)])
    st.dbc_signals.append({"message_id": "1A0", "signal_name": "S",
                           "start_bit": 0, "length": 8})
    st.vehicle_profile = "toyota"
    return st


def test_project_is_versioned_and_round_trips(tmp_path):
    import zipfile

    from canlab.core.project import PROJECT_FORMAT_VERSION, load_project, save_project
    st = _state_with_data()
    path = str(tmp_path / "p.canlab")
    save_project(st, path)
    with zipfile.ZipFile(path) as zf:
        meta = json.loads(zf.read("meta.json"))
    assert meta["format_version"] == PROJECT_FORMAT_VERSION
    assert meta["vehicle_profile"] == "toyota"

    from canlab.core.state import AppState
    fresh = AppState()
    load_project(fresh, path)
    assert len(fresh.frames_df) == 10
    assert fresh.vehicle_profile == "toyota"
    assert fresh.dbc_signals[0]["signal_name"] == "S"


def test_an_older_project_without_a_version_still_loads(tmp_path):
    import io
    import zipfile

    from canlab.core.project import load_project
    from canlab.core.state import AppState
    path = tmp_path / "old.canlab"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("signals.json", json.dumps([{"message_id": "200",
                                                 "signal_name": "Old"}]))
        # v1 meta: no format_version, and keys from since-removed features.
        zf.writestr("meta.json", json.dumps({"repo_url": "x", "fingerprint": {},
                                             "periodicities": {"200": 10.0}}))
        buf = io.StringIO("Timestamp,ID,Bus,DLC,B0\n0.0,200,0,1,5\n")
        zf.writestr("frames.csv", buf.getvalue())
    st = AppState()
    load_project(st, str(path))
    assert st.dbc_signals[0]["signal_name"] == "Old"
    assert st.periodicities == {"200": 10.0}


def test_loading_a_project_does_not_discard_session_memory(tmp_path):
    from canlab.core.project import load_project, save_project
    from canlab.core.state import AppState
    st = _state_with_data()
    st.ai_memory = [{"id": "1A0", "conclusion": "from the project"}]
    path = str(tmp_path / "p.canlab")
    save_project(st, path)

    other = AppState()
    other.ai_memory = [{"id": "2B0", "conclusion": "from this session"}]
    load_project(other, path)
    conclusions = {e["conclusion"] for e in other.ai_memory}
    assert conclusions == {"from this session", "from the project"}
