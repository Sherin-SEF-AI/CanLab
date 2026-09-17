"""Hardware CAN adapters: what python-can can drive, and how to describe one.

An adapter is a name for a python-can bus configuration: the backend
("interface"), the channel that backend expects (a network device, a serial
port, a device index), the bitrate, and whatever else the backend needs (the
serial baud rate of an slcan stick, the host of a socketcand daemon). The
window keeps a list of them so a bench with a PCAN on one port and a CANable
on another is two clicks, not two retypings.

Detection is best effort. python-can asks each backend what it can see;
serial ports and USB IDs are read on top of that so a CANable or USBtin that
is plugged in shows up as a suggestion even before its driver is asked.

Qt-free. The settings dialog and the toolbar own the widgets.
"""
from __future__ import annotations

import logging
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

log = logging.getLogger(__name__)

# python-can ships no GVRET backend, so CanLab supplies one under that name.
try:
    from canlab.core.gvret import register as _register_gvret
    _register_gvret()
except Exception:                                    # pragma: no cover
    log.debug("could not register the GVRET backend", exc_info=True)

BITRATES = [1_000_000, 500_000, 250_000, 125_000, 100_000, 50_000, 33_333, 20_000, 10_000]
FD_DATA_BITRATES = [2_000_000, 4_000_000, 5_000_000, 8_000_000]


@dataclass
class InterfaceInfo:
    key: str                      # python-can interface name
    label: str                    # what the user sees
    channel_hint: str             # placeholder for the channel field
    kind: str                     # netdev | serial | index | name | host
    requires: str = ""            # pip package or driver the backend needs
    platforms: str = "any"
    notes: str = ""
    extra: tuple = ()             # (kwarg, label, default) backend-specific settings
    fd: bool = False              # backend can run CAN FD
    bitrate_in_software: bool = True   # False: the OS sets it (socketcan)


INTERFACES: dict[str, InterfaceInfo] = {i.key: i for i in [
    InterfaceInfo("socketcan", "SocketCAN (Linux: can0, vcan0, USB adapters with kernel drivers)",
                  "can0", "netdev", platforms="linux", fd=True, bitrate_in_software=False,
                  notes="The kernel owns the bitrate. Bring the device up first:\n"
                        "  sudo ip link set can0 up type can bitrate 500000\n"
                        "candleLight, PEAK, Kvaser and 8devices adapters all appear here on Linux."),
    InterfaceInfo("gvret", "GVRET firmware (Macchina M2/A0, EVTV CANDue, ESP32RET)",
                  "/dev/ttyACM0" if sys.platform != "win32" else "COM3", "serial",
                  requires="pyserial (serial boards)",
                  extra=(("tty_baudrate", "Serial baud", 1_000_000),
                         ("bus_index", "Board bus index", 0)),
                  notes="SavvyCAN's native hardware. For an ESP32RET board on WiFi "
                        "give host:port as the channel instead, usually <ip>:23."),
    InterfaceInfo("slcan", "Serial line CAN (CANable slcan firmware, USBtin, Lawicel CANUSB)",
                  "/dev/ttyACM0" if sys.platform != "win32" else "COM3", "serial",
                  requires="pyserial", extra=(("tty_baudrate", "Serial baud", 115200),),
                  notes="On Linux add yourself to the dialout group to open the port."),
    InterfaceInfo("gs_usb", "candleLight USB (CANable, CANtact, Innomaker) via libusb",
                  "0", "index", requires="gs_usb", fd=False,
                  extra=(("index", "Device index", 0),),
                  notes="Channel is the device index. On Linux the kernel gs_usb driver exposes "
                        "the same stick as can0; SocketCAN is then the better choice."),
    InterfaceInfo("pcan", "PEAK PCAN-USB / PCAN-PCI (PCAN-Basic driver)", "PCAN_USBBUS1", "name",
                  requires="PCAN-Basic library", fd=True,
                  notes="Channels are PCAN_USBBUS1..16, PCAN_PCIBUS1.. as the driver names them."),
    InterfaceInfo("kvaser", "Kvaser (CANlib)", "0", "index", requires="Kvaser CANlib", fd=True,
                  notes="Channel is the CANlib channel number, 0 for the first device."),
    InterfaceInfo("vector", "Vector (VN1610, VN1630, CANcase; XL driver)", "0", "index",
                  requires="Vector XL driver", platforms="windows", fd=True,
                  extra=(("app_name", "Application name", "CANalyzer"),),
                  notes="The channel must be assigned to the application name in Vector "
                        "Hardware Config."),
    InterfaceInfo("ixxat", "IXXAT USB-to-CAN (VCI driver)", "0", "index",
                  requires="IXXAT VCI driver", platforms="windows", fd=True),
    InterfaceInfo("usb2can", "8devices USB2CAN (Windows driver)", "ED000200", "name",
                  requires="usb2can driver, pywin32", platforms="windows",
                  notes="Channel is the device serial number. On Linux use SocketCAN."),
    InterfaceInfo("seeedstudio", "Seeed Studio USB-CAN analyzer (serial)", "/dev/ttyUSB0", "serial",
                  requires="pyserial", extra=(("baudrate", "Serial baud", 2_000_000),)),
    InterfaceInfo("robotell", "Robotell USB-CAN (serial)", "/dev/ttyUSB0", "serial",
                  requires="pyserial", extra=(("ttyBaudrate", "Serial baud", 115200),)),
    InterfaceInfo("serial", "Simple serial protocol (python-can serial backend)", "/dev/ttyUSB0",
                  "serial", requires="pyserial", extra=(("baudrate", "Serial baud", 115200),),
                  notes="Not slcan: this is python-can's own framing over a UART."),
    InterfaceInfo("canalystii", "CANalyst-II (USB)", "0", "index", requires="canalystii",
                  notes="No listen-only mode through the open-source driver. Its "
                        "init packet has a mode field, but the driver's own source "
                        "records the meaning as unknown and that setting it appears "
                        "to crash the device, so CanLab does not set it. Opening "
                        "this adapter means its controller acknowledges frames."),
    InterfaceInfo("neovi", "Intrepid neoVI / ValueCAN", "1", "index", requires="python-ics"),
    InterfaceInfo("socketcand", "socketcand (a remote SocketCAN over TCP)", "can0", "host",
                  extra=(("host", "Host", "192.168.1.10"), ("port", "Port", 29536))),
    InterfaceInfo("udp_multicast", "UDP multicast (virtual bus across machines)",
                  "239.74.163.2", "name", fd=True),
    InterfaceInfo("virtual", "Virtual (in-process, for trying the application)", "vbus0", "name",
                  fd=True),
]}

# The adapters people plug in most, recognised by USB vendor and product id.
# A hint only; the user still picks the backend.
KNOWN_USB: dict[tuple[int, int], tuple[str, str]] = {
    (0x1D50, 0x606F): ("candleLight (CANable, CANtact, Innomaker)", "gs_usb"),
    (0xAD50, 0x60C4): ("CANable (slcan firmware)", "slcan"),
    (0x16D0, 0x117E): ("CANable 2.0 (slcan firmware)", "slcan"),
    (0x04D8, 0x000A): ("USBtin", "slcan"),
    # The CANalyst-II and its clones. python-can's canalystii backend has no
    # detection of its own, so without this entry a plugged-in analyser is
    # invisible however the backends are asked.
    (0x04D8, 0x0053): ("CANalyst-II", "canalystii"),
    (0x04D8, 0x0054): ("CANalyst-II (clone)", "canalystii"),
    (0x2341, 0x804D): ("Macchina M2 (GVRET)", "gvret"),
    (0x2A03, 0x804D): ("Macchina M2 (GVRET)", "gvret"),
    (0x0C72, 0x000C): ("PEAK PCAN-USB", "pcan"),
    (0x0C72, 0x000D): ("PEAK PCAN-USB Pro", "pcan"),
    (0x0BFD, 0x0120): ("Kvaser Leaf Light", "kvaser"),
    (0x1D50, 0x8000): ("Vector VN1610", "vector"),
    (0x0483, 0x1234): ("8devices USB2CAN", "usb2can"),
}


@dataclass
class Adapter:
    name: str
    interface: str
    channel: str
    bitrate: int = 500_000
    fd: bool = False
    data_bitrate: int = 2_000_000
    extra: dict = field(default_factory=dict)
    detected: str = ""            # how it was found, for the detection list

    def bus_kwargs(self) -> dict:
        """What to hand python-can's Bus()."""
        info = INTERFACES.get(self.interface)
        channel: object = self.channel
        if info and info.kind == "index":
            try:
                channel = int(self.channel)
            except ValueError:
                pass
        kw: dict = {"interface": self.interface, "channel": channel}
        if not info or info.bitrate_in_software:
            kw["bitrate"] = int(self.bitrate)
        if self.fd:
            kw["fd"] = True
            kw["data_bitrate"] = int(self.data_bitrate)
        for k, v in self.extra.items():
            if v not in ("", None):
                kw[k] = v
        return kw

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("detected", None)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Adapter":
        return cls(name=str(d.get("name") or d.get("channel") or "adapter"),
                   interface=str(d.get("interface", "socketcan")),
                   channel=str(d.get("channel", "can0")),
                   bitrate=int(d.get("bitrate", 500_000) or 500_000),
                   fd=bool(d.get("fd", False)),
                   data_bitrate=int(d.get("data_bitrate", 2_000_000) or 2_000_000),
                   extra=dict(d.get("extra") or {}))

    def describe(self) -> str:
        fd = f", FD {self.data_bitrate // 1000} kbit/s" if self.fd else ""
        return f"{self.interface} {self.channel} @ {self.bitrate // 1000} kbit/s{fd}"


# ── detection ────────────────────────────────────────────────────────────────

def _linux_can_netdevs() -> list[str]:
    """CAN network devices, from sysfs (ARPHRD_CAN is 280)."""
    out = []
    root = Path("/sys/class/net")
    if not root.is_dir():
        return out
    for dev in sorted(root.iterdir()):
        try:
            if (dev / "type").read_text().strip() == "280":
                out.append(dev.name)
        except OSError:
            continue
    return out


def _serial_ports() -> list[tuple[str, str, tuple[int, int] | None]]:
    """(device, description, (vid, pid)) for USB serial ports only."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    out = []
    for p in list_ports.comports():
        if p.vid is None:               # legacy ttyS* have no USB identity
            continue
        out.append((p.device, p.description or "", (p.vid, p.pid)))
    return out


def _usb_devices() -> list[tuple[int, int, str]]:
    """(vid, pid, product) for every USB device pyusb can enumerate."""
    try:
        import usb.core
    except ImportError:
        return []
    out = []
    try:
        for dev in usb.core.find(find_all=True):
            try:
                product = dev.product or ""
            except Exception:
                product = ""
            out.append((dev.idVendor, dev.idProduct, product))
    except Exception:
        log.debug("usb enumeration failed", exc_info=True)
    return out


def detect_adapters(interfaces: list[str] | None = None, timeout: float = 3.0) -> list[Adapter]:
    """Adapters that can be seen right now, as suggested configurations.

    Asks python-can's backends, then sysfs for CAN netdevs, then the serial
    and USB tables for sticks whose driver is not installed yet (those are
    suggestions: the backend they need is named, and Test tells you whether
    it opens).
    """
    found: list[Adapter] = []
    seen: set[tuple[str, str]] = set()

    def add(a: Adapter):
        key = (a.interface, a.channel)
        if key not in seen:
            seen.add(key)
            found.append(a)

    try:
        import can
        wanted = interfaces or [k for k in INTERFACES if k not in ("virtual", "udp_multicast")]
        for cfg in can.detect_available_configs(wanted, timeout=timeout):
            iface = str(cfg.get("interface", ""))
            ch = str(cfg.get("channel", ""))
            if not iface or not ch or iface not in INTERFACES:
                continue
            a = Adapter(name=f"{iface} {ch}", interface=iface, channel=ch,
                        detected=f"reported by the {iface} backend")
            for k in ("bitrate",):
                if cfg.get(k):
                    a.bitrate = int(cfg[k])
            add(a)
    except Exception:
        log.debug("python-can detection failed", exc_info=True)

    for dev in _linux_can_netdevs():
        add(Adapter(name=dev, interface="socketcan", channel=dev, detected="CAN network device"))

    for device, desc, vidpid in _serial_ports():
        known = KNOWN_USB.get(vidpid)
        if known and known[1] == "gvret":
            add(Adapter(name=f"{known[0]} on {device}", interface="gvret", channel=device,
                        extra={"tty_baudrate": 1_000_000, "bus_index": 0},
                        detected=f"USB serial {desc}".strip()))
        elif known and known[1] in ("slcan",):
            add(Adapter(name=f"{known[0]} on {device}", interface="slcan", channel=device,
                        extra={"tty_baudrate": 115200}, detected=f"USB serial {desc}".strip()))
        else:
            add(Adapter(name=f"serial {device}", interface="slcan", channel=device,
                        extra={"tty_baudrate": 115200},
                        detected=f"USB serial port ({desc}); slcan if it is a CAN stick"))

    for vid, pid, product in _usb_devices():
        known = KNOWN_USB.get((vid, pid))
        if not known:
            continue
        label, iface = known
        if iface == "gs_usb":
            if not any(a.interface == "socketcan" for a in found):
                add(Adapter(name=label, interface="gs_usb", channel="0", extra={"index": 0},
                            detected=f"USB {vid:04X}:{pid:04X} {product}".strip()))
        else:
            # Every other known backend, rather than a hand-written list that
            # silently dropped anything missing from it: a CANalyst-II entry
            # was unreachable by construction because it was not named here.
            info = INTERFACES.get(iface)
            if info is None:
                continue
            needs = f"; needs {info.requires}" if info.requires else ""
            add(Adapter(name=label, interface=iface, channel=info.channel_hint,
                        detected=f"USB {vid:04X}:{pid:04X} {product}".strip() + needs))
    return found


# ── testing one ──────────────────────────────────────────────────────────────

_HINTS = (
    ("No module named 'serial'", "pip install pyserial"),
    ("No module named 'gs_usb'", "pip install gs_usb (and libusb)"),
    ("No module named 'canalystii'", "pip install canalystii"),
    ("No module named 'ics'", "pip install python-ics"),
    ("Permission denied", "on Linux add your user to the dialout group, then log in again"),
    # Reported from a bench: two tools, one adapter. libusb answers with a bare
    # errno and no explanation of who is holding the device.
    ("Resource busy", "the adapter is already open. A CanLab window capturing "
                      "from it holds it exclusively, and so does any other tool "
                      "(SavvyCAN, candump on a USB backend, another script). "
                      "Close the other one, or disconnect here first."),
    ("LIBUSB_ERROR_BUSY", "the adapter is already open in another program or "
                          "another CanLab window. Close that one first."),
    ("Device or resource busy", "the adapter is already open elsewhere. Close "
                                "the other program, or disconnect here first."),
    ("No such device", "bring the device up: sudo ip link set <dev> up type can bitrate <bps>"),
    ("Network is down", "bring the device up: sudo ip link set <dev> up type can bitrate <bps>"),
    ("PCAN-Basic", "install the PCAN-Basic library from PEAK"),
    ("canlib", "install Kvaser CANlib"),
    ("vxlapi", "install the Vector XL driver"),
)


def _silence(adapter: Adapter) -> tuple[bool, str]:
    """Whether opening this adapter is guaranteed to leave the bus alone.

    True only when the interface itself was configured listen-only, which is
    a property of the device, not of how the application opens it.
    """
    if adapter.interface == "virtual":
        return True, ""
    if adapter.interface == "socketcan":
        try:
            from canlab.core.privileged import interface_state
            state = interface_state(adapter.channel)
        except Exception:                                 # pragma: no cover
            return False, ""
        if state.get("listen_only"):
            return True, ""
        return False, (f"{adapter.channel} is not in listen-only mode, so its "
                       f"controller will acknowledge frames it receives. Bring "
                       f"it up with listen-only on to be certain the bus is "
                       f"untouched.")
    return False, ("This backend has no listen-only setting, so the controller "
                   "acknowledges frames in hardware while the adapter is open. "
                   "It sends no frames of its own.")


def probe_adapter(adapter: Adapter, listen_s: float = 1.0) -> dict:
    """Open the adapter, listen briefly, close it. Sends no frames.

    It sends nothing, but "sends nothing" is not the same as "changes
    nothing on the bus". A CAN controller in normal mode acknowledges every
    valid frame it receives in hardware, before any software sees it, and at
    the wrong bitrate it will emit error frames. The only way to be sure a
    bus is untouched is to put the controller in listen-only mode, which is
    set when the interface is configured, not by opening it.

    So the result carries a `silent` flag and a `warning` saying which of
    those two this is. It used to claim it never transmits, full stop, which
    was wrong at the level that matters when the other end is a vehicle.

    Returns ok, the backend's channel_info, how many frames arrived and from
    which IDs, or the error with a hint on what to install or run.
    """
    import can
    kw = adapter.bus_kwargs()
    t0 = time.perf_counter()
    try:
        bus = can.Bus(**kw)
    except Exception as e:
        text = f"{type(e).__name__}: {e}"
        hint = next((h for needle, h in _HINTS if needle.lower() in text.lower()), "")
        return {"ok": False, "error": text, "hint": hint, "kwargs": kw}
    silent, warning = _silence(adapter)
    ids: set[str] = set()
    n = 0
    try:
        info = getattr(bus, "channel_info", "") or ""
        deadline = time.monotonic() + max(0.0, listen_s)
        while time.monotonic() < deadline:
            msg = bus.recv(timeout=0.1)
            if msg is None:
                continue
            if getattr(msg, "is_error_frame", False):
                continue
            n += 1
            ids.add(f"{msg.arbitration_id:X}")
    finally:
        try:
            bus.shutdown()
        except Exception:
            log.debug("shutdown failed", exc_info=True)
    return {"ok": True, "info": str(info), "frames": n, "ids": sorted(ids)[:20],
            "open_ms": round((time.perf_counter() - t0) * 1000), "kwargs": kw,
            "silent": silent, "warning": warning}


# ── persistence helpers (the dialog stores JSON in QSettings) ─────────────────

def adapters_from_json(text: str) -> list[Adapter]:
    import json
    try:
        raw = json.loads(text or "[]")
    except (ValueError, TypeError):
        return []
    out = []
    for d in raw if isinstance(raw, list) else []:
        try:
            out.append(Adapter.from_dict(d))
        except Exception:
            log.debug("bad adapter entry %r", d, exc_info=True)
    return out


def adapters_to_json(adapters: list[Adapter]) -> str:
    import json
    return json.dumps([a.to_dict() for a in adapters])


#: Where the desktop mirrors its saved adapters so the capture kit, which
#: runs without Qt, can open one by name. CANLAB_ADAPTERS_FILE overrides it.
ADAPTERS_FILE = Path.home() / ".canlab" / "adapters.json"


def adapters_file(path=None) -> Path:
    import os
    if path:
        return Path(path).expanduser()
    return Path(os.environ.get("CANLAB_ADAPTERS_FILE") or ADAPTERS_FILE).expanduser()


def save_adapters_file(adapters: list[Adapter], path=None) -> Path:
    p = adapters_file(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(adapters_to_json(adapters))
    return p


def load_adapters_file(path=None) -> list[Adapter]:
    p = adapters_file(path)
    if not p.is_file():
        return []
    return adapters_from_json(p.read_text())
