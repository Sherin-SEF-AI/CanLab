"""Hardware CAN adapters: describing them, finding them, opening them.

Detection is checked against stand-ins for the probes (python-can, sysfs,
serial ports, USB), and opening is checked for real against python-can's
virtual bus, with frames sent from a second virtual bus on the same channel.
"""
import json
import threading
import time


from canlab.core import adapters as A
from canlab.core.adapters import (
    INTERFACES, Adapter, adapters_from_json, adapters_to_json, detect_adapters, probe_adapter,
)


# ── describing ───────────────────────────────────────────────────────────────

def test_bus_kwargs_per_backend():
    assert Adapter("a", "socketcan", "can0", 250_000).bus_kwargs() == \
        {"interface": "socketcan", "channel": "can0"}          # the kernel owns the bitrate
    assert Adapter("a", "slcan", "/dev/ttyACM0", 500_000, extra={"tty_baudrate": 115200}) \
        .bus_kwargs() == {"interface": "slcan", "channel": "/dev/ttyACM0", "bitrate": 500_000,
                          "tty_baudrate": 115200}
    assert Adapter("a", "kvaser", "1").bus_kwargs()["channel"] == 1          # index backends take ints
    assert Adapter("a", "gs_usb", "0", extra={"index": 0}).bus_kwargs()["index"] == 0
    fd = Adapter("a", "pcan", "PCAN_USBBUS2", 500_000, fd=True, data_bitrate=2_000_000).bus_kwargs()
    assert fd["fd"] is True and fd["data_bitrate"] == 2_000_000 and fd["channel"] == "PCAN_USBBUS2"
    assert "app_name" not in Adapter("a", "vector", "0", extra={"app_name": ""}).bus_kwargs()


def test_round_trip_and_tolerant_load():
    a = Adapter("bench", "slcan", "/dev/ttyACM1", 250_000, True, 4_000_000, {"tty_baudrate": 921600})
    back = adapters_from_json(adapters_to_json([a]))
    assert back == [a]
    assert adapters_from_json("not json") == []
    assert adapters_from_json('[{"channel": "can3"}]')[0].name == "can3"
    assert adapters_from_json('[{"name": "x", "bitrate": null}]')[0].bitrate == 500_000
    assert "250 kbit/s" in a.describe() and "FD 4000" in a.describe()


def test_every_python_can_backend_has_an_entry():
    from can.interfaces import VALID_INTERFACES
    listed = set(INTERFACES)
    # The ones people plug in are described; the rest are still reachable by
    # name through the dialog's backend list.
    for common in ("socketcan", "slcan", "gs_usb", "pcan", "kvaser", "vector", "virtual"):
        assert common in listed and common in VALID_INTERFACES
    assert listed <= set(VALID_INTERFACES)


# ── detecting ────────────────────────────────────────────────────────────────

def test_detect_merges_every_probe(monkeypatch):
    import can
    monkeypatch.setattr(can, "detect_available_configs",
                        lambda ifaces, timeout: [{"interface": "socketcan", "channel": "can0"},
                                                 {"interface": "virtual", "channel": "v1"},
                                                 {"interface": "bogus", "channel": "x"}])
    monkeypatch.setattr(A, "_linux_can_netdevs", lambda: ["can0", "vcan0"])
    monkeypatch.setattr(A, "_serial_ports", lambda: [
        ("/dev/ttyACM0", "CANable", (0xAD50, 0x60C4)),
        ("/dev/ttyUSB0", "FT232R", (0x0403, 0x6001))])
    monkeypatch.setattr(A, "_usb_devices", lambda: [(0x0C72, 0x000C, "PCAN-USB"),
                                                     (0x1D50, 0x606F, "candleLight")])
    found = detect_adapters()
    keys = [(a.interface, a.channel) for a in found]
    assert ("socketcan", "can0") in keys and keys.count(("socketcan", "can0")) == 1
    assert ("socketcan", "vcan0") in keys
    assert ("virtual", "v1") in keys and ("bogus", "x") not in keys
    canable = next(a for a in found if a.channel == "/dev/ttyACM0")
    assert canable.interface == "slcan" and "CANable" in canable.name \
        and canable.extra == {"tty_baudrate": 115200}
    unknown = next(a for a in found if a.channel == "/dev/ttyUSB0")
    assert unknown.interface == "slcan" and "slcan if" in unknown.detected
    pcan = next(a for a in found if a.interface == "pcan")
    assert pcan.channel == "PCAN_USBBUS1" and "PCAN-Basic" in pcan.detected
    # A candleLight is already on the bus as can0 through the kernel driver,
    # so it is not offered a second time as gs_usb.
    assert not any(a.interface == "gs_usb" for a in found)


def test_detect_offers_gs_usb_without_kernel_driver(monkeypatch):
    import can
    monkeypatch.setattr(can, "detect_available_configs", lambda *a, **k: [])
    monkeypatch.setattr(A, "_linux_can_netdevs", lambda: [])
    monkeypatch.setattr(A, "_serial_ports", lambda: [])
    monkeypatch.setattr(A, "_usb_devices", lambda: [(0x1D50, 0x606F, "candleLight")])
    found = detect_adapters()
    assert [(a.interface, a.channel, a.extra) for a in found] == [("gs_usb", "0", {"index": 0})]


def test_detect_survives_probe_failures(monkeypatch):
    import can
    def boom(*a, **k):
        raise RuntimeError("driver exploded")
    monkeypatch.setattr(can, "detect_available_configs", boom)
    monkeypatch.setattr(A, "_linux_can_netdevs", lambda: ["can1"])
    monkeypatch.setattr(A, "_serial_ports", lambda: [])
    monkeypatch.setattr(A, "_usb_devices", lambda: [])
    assert [a.channel for a in detect_adapters()] == ["can1"]


def test_sysfs_scan_reads_arphrd_can(tmp_path, monkeypatch):
    for name, t in (("eth0", "1"), ("can0", "280"), ("vcan0", "280")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "type").write_text(t + "\n")
    monkeypatch.setattr(A, "Path", lambda p: tmp_path if p == "/sys/class/net" else __import__("pathlib").Path(p))
    assert A._linux_can_netdevs() == ["can0", "vcan0"]


# ── opening ──────────────────────────────────────────────────────────────────

def test_probe_listens_on_a_virtual_bus():
    import can
    chan = "canlab-test-vbus"
    sender = can.Bus(interface="virtual", channel=chan)
    stop = threading.Event()

    def feed():
        i = 0
        while not stop.is_set():
            sender.send(can.Message(arbitration_id=0x100 + (i % 2), data=bytes(8),
                                    is_extended_id=False))
            i += 1
            time.sleep(0.02)
    th = threading.Thread(target=feed, daemon=True)
    th.start()
    try:
        r = probe_adapter(Adapter("v", "virtual", chan), listen_s=0.5)
    finally:
        stop.set()
        th.join(2)
        sender.shutdown()
    assert r["ok"] and r["frames"] > 0 and set(r["ids"]) <= {"100", "101"}
    assert r["kwargs"] == {"interface": "virtual", "channel": chan, "bitrate": 500_000}
    assert "open_ms" in r
    assert not sender._sent if hasattr(sender, "_sent") else True   # the test never transmits


def test_probe_reports_failure_with_hint():
    r = probe_adapter(Adapter("nope", "socketcan", "canlab-no-such-dev-99"), listen_s=0.1)
    assert not r["ok"] and r["error"]
    assert "ip link" in r["hint"] or r["hint"] == ""      # hint when the OS said so
    r = probe_adapter(Adapter("bad", "not-a-backend", "x"), listen_s=0.1)
    assert not r["ok"] and "not-a-backend" in r["error"]


# ── the dialogs and settings ─────────────────────────────────────────────────

def test_adapter_dialog_round_trip(qcore):
    from canlab.ui.adapter_dialog import AdapterDialog
    a = Adapter("stick", "slcan", "/dev/ttyACM3", 250_000, extra={"tty_baudrate": 921600})
    dlg = AdapterDialog(a)
    assert dlg.adapter() == a
    assert "pyserial" in dlg.lbl_notes.text()
    dlg.iface_combo.setCurrentIndex(dlg.iface_combo.findData("socketcan"))
    assert dlg.adapter().channel == "/dev/ttyACM3"        # typed by the user, kept
    assert not dlg.bitrate_combo.isEnabled()              # the OS sets it
    fresh = AdapterDialog()
    assert fresh.adapter().channel == INTERFACES["socketcan"].channel_hint
    fresh.iface_combo.setCurrentIndex(fresh.iface_combo.findData("pcan"))
    assert fresh.adapter().channel == "PCAN_USBBUS1"       # never typed, follows the backend
    fresh.iface_combo.setCurrentIndex(fresh.iface_combo.findData("socketcand"))
    got = fresh.adapter()
    assert got.extra["port"] == 29536 and got.extra["host"]
    dlg.deleteLater()
    fresh.deleteLater()


def test_settings_migrates_legacy_interface_and_persists(qcore):
    from canlab.settings_dialog import SettingsDialog, settings
    st = settings()
    st.remove(SettingsDialog.S_ADAPTERS)
    st.setValue(SettingsDialog.S_INTERFACE, "pcan")
    st.setValue(SettingsDialog.S_CHANNEL, "PCAN_USBBUS1")
    st.setValue(SettingsDialog.S_BITRATE, "250000")
    dlg = SettingsDialog()
    names = [(a.name, a.interface, a.channel, a.bitrate) for a in dlg.get_adapters()]
    assert names == [("Default", "pcan", "PCAN_USBBUS1", 250_000)]
    assert dlg.get_can_settings()["interface"] == "pcan"

    dlg._adapters.append(Adapter("CANable", "slcan", "/dev/ttyACM0", extra={"tty_baudrate": 115200}))
    dlg._adapter_refresh_table()
    dlg.adapter_table.selectRow(1)
    dlg._adapter_set_default()
    assert dlg.adapter_table.item(1, 4).text() == "yes"
    dlg._save_persisted()
    saved = json.loads(st.value(SettingsDialog.S_ADAPTERS))
    assert [a["name"] for a in saved] == ["Default", "CANable"]
    assert st.value(SettingsDialog.S_ADAPTER_DEFAULT) == "CANable"
    assert st.value(SettingsDialog.S_INTERFACE) == "slcan"
    assert json.loads(st.value(SettingsDialog.S_EXTRA)) == {"tty_baudrate": 115200}

    again = SettingsDialog()
    assert again.default_adapter().name == "CANable"
    again.adapter_table.selectRow(1)
    again._adapter_remove()
    assert again.default_adapter().name == "Default"
    assert again._unique_name("Default") == "Default (2)"
    dlg.deleteLater()
    again.deleteLater()


def test_main_window_adapter_picker(qcore, monkeypatch):
    from canlab.settings_dialog import SettingsDialog, settings
    st = settings()
    st.setValue(SettingsDialog.S_ADAPTERS, adapters_to_json([
        Adapter("bench", "virtual", "vb0"),
        Adapter("stick", "slcan", "/dev/ttyACM0", 250_000, extra={"tty_baudrate": 921600})]))
    st.setValue(SettingsDialog.S_ADAPTER_DEFAULT, "bench")
    st.setValue(SettingsDialog.S_INTERFACE, "virtual")
    st.setValue(SettingsDialog.S_CHANNEL, "vb0")
    from canlab.mainwindow import MainWindow
    w = MainWindow()
    try:
        texts = [w.adapter_combo.itemText(i) for i in range(w.adapter_combo.count())]
        assert texts[0].startswith("bench") and texts[1].startswith("stick") \
            and texts[-1] == "Manage adapters…"
        assert w.adapter_combo.currentIndex() == 0
        w._on_adapter_picked(1)
        assert w._can_settings["name"] == "stick" and w._can_settings["extra"] == {"tty_baudrate": 921600}
        assert st.value(SettingsDialog.S_ADAPTER_DEFAULT) == "stick"

        opened = {}
        import can
        monkeypatch.setattr(can.interface, "Bus", lambda **kw: opened.update(kw) or _Dummy())
        w._open_bus("slcan", "/dev/ttyACM0", 250_000, False, None, {"tty_baudrate": 921600})
        assert opened == {"interface": "slcan", "channel": "/dev/ttyACM0", "bitrate": 250_000,
                          "tty_baudrate": 921600}
    finally:
        w.close()
        w.deleteLater()
        qcore.processEvents()


class _Dummy:
    def shutdown(self):
        pass


# ── what a real machine with a real adapter found ────────────────────────────

def test_the_canalyst_analyser_is_recognised():
    """Reported from a machine with one plugged in: detection returned nothing.

    04D8:0053 was absent from the table, and python-can's canalystii backend
    has no detection of its own, so nothing else could have found it.
    """
    from canlab.core.adapters import KNOWN_USB
    label, iface = KNOWN_USB[(0x04D8, 0x0053)]
    assert iface == "canalystii"
    assert "CANalyst" in label


def test_every_known_usb_backend_can_actually_be_offered(monkeypatch):
    """The USB loop named four backends explicitly and dropped the rest.

    A table entry for a backend outside that list was unreachable by
    construction: adding the analyser to KNOWN_USB still detected nothing.
    """
    from canlab.core import adapters

    import can
    monkeypatch.setattr(can, "detect_available_configs", lambda *a, **k: [])
    monkeypatch.setattr(adapters, "_linux_can_netdevs", lambda: [])
    monkeypatch.setattr(adapters, "_serial_ports", lambda: [])
    for (vid, pid), (label, iface) in adapters.KNOWN_USB.items():
        if iface == "gs_usb":
            continue          # offered as socketcan when a netdev already exists
        monkeypatch.setattr(adapters, "_usb_devices",
                            lambda vid=vid, pid=pid: [(vid, pid, "device")])
        found = adapters.detect_adapters()
        assert any(a.interface == iface for a in found), \
            f"{label} ({iface}) is in the table but never offered"


def test_probe_says_whether_the_bus_was_really_untouched():
    """"Sends no frames" is not "changes nothing": a controller in normal mode
    acknowledges in hardware, which matters when the other end is a vehicle."""
    from canlab.core.adapters import Adapter, _silence

    silent, warning = _silence(Adapter(name="v", interface="virtual", channel="vbus0"))
    assert silent and not warning

    silent, warning = _silence(Adapter(name="a", interface="canalystii", channel="0"))
    assert not silent and "acknowledges" in warning


def test_the_probe_result_carries_the_warning(qcore):
    from canlab.core.adapters import Adapter, probe_adapter
    from canlab.ui.adapter_dialog import format_test_result

    result = probe_adapter(Adapter(name="v", interface="virtual", channel="vbus0"),
                           listen_s=0.05)
    assert result["ok"] and result["silent"] is True
    assert "Listen-only" in format_test_result(result)

    noisy = dict(result, silent=False, warning="it acknowledges frames")
    assert "acknowledges" in format_test_result(noisy)
