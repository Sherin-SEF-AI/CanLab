"""SAE J1979 Mode 01 PID table with decoder lambdas.

Each entry: {name, unit, min, max, decode}
  decode(data: bytes) -> float   (data = response payload bytes A, B, C, D...)

The formulas are the published SAE J1979 ones, written in the A, B, C, D
byte notation the standard uses. Only PIDs whose value is a single number
are here; bit-encoded status PIDs (monitor status, fuel system status, O2
sensor presence) are not.

Two helpers for the rest of the standard live beside the table: reading the
"which PIDs do you support" masks so a scan asks only for those, and
decoding the DTC lists that modes 03, 07 and 0A return.
"""

PID_TABLE: dict[int, dict] = {
    0x04: {
        "name": "Engine Load",
        "unit": "%",
        "min": 0, "max": 100,
        "decode": lambda b: b[0] * 100 / 255,
    },
    0x05: {
        "name": "Coolant Temp",
        "unit": "°C",
        "min": -40, "max": 215,
        "decode": lambda b: b[0] - 40,
    },
    0x06: {
        "name": "Short Fuel Trim B1",
        "unit": "%",
        "min": -100, "max": 99.2,
        "decode": lambda b: (b[0] - 128) * 100 / 128,
    },
    0x07: {
        "name": "Long Fuel Trim B1",
        "unit": "%",
        "min": -100, "max": 99.2,
        "decode": lambda b: (b[0] - 128) * 100 / 128,
    },
    0x0B: {
        "name": "MAP Pressure",
        "unit": "kPa",
        "min": 0, "max": 255,
        "decode": lambda b: float(b[0]),
    },
    0x0C: {
        "name": "Engine RPM",
        "unit": "rpm",
        "min": 0, "max": 8000,
        "decode": lambda b: ((b[0] << 8) | b[1]) / 4,
    },
    0x0D: {
        "name": "Vehicle Speed",
        "unit": "km/h",
        "min": 0, "max": 255,
        "decode": lambda b: float(b[0]),
    },
    0x0E: {
        "name": "Timing Advance",
        "unit": "°",
        "min": -64, "max": 63.5,
        "decode": lambda b: b[0] / 2 - 64,
    },
    0x0F: {
        "name": "Intake Air Temp",
        "unit": "°C",
        "min": -40, "max": 215,
        "decode": lambda b: b[0] - 40,
    },
    0x10: {
        "name": "MAF Rate",
        "unit": "g/s",
        "min": 0, "max": 655.35,
        "decode": lambda b: ((b[0] << 8) | b[1]) / 100,
    },
    0x11: {
        "name": "Throttle Position",
        "unit": "%",
        "min": 0, "max": 100,
        "decode": lambda b: b[0] * 100 / 255,
    },
    0x1F: {
        "name": "Run Time",
        "unit": "s",
        "min": 0, "max": 65535,
        "decode": lambda b: float((b[0] << 8) | b[1]),
    },
    0x21: {
        "name": "Distance (MIL on)",
        "unit": "km",
        "min": 0, "max": 65535,
        "decode": lambda b: float((b[0] << 8) | b[1]),
    },
    0x2C: {
        "name": "EGR Commanded",
        "unit": "%",
        "min": 0, "max": 100,
        "decode": lambda b: b[0] * 100 / 255,
    },
    0x2F: {
        "name": "Fuel Level",
        "unit": "%",
        "min": 0, "max": 100,
        "decode": lambda b: b[0] * 100 / 255,
    },
    0x31: {
        "name": "Distance (cleared)",
        "unit": "km",
        "min": 0, "max": 65535,
        "decode": lambda b: float((b[0] << 8) | b[1]),
    },
    0x33: {
        "name": "Baro Pressure",
        "unit": "kPa",
        "min": 0, "max": 255,
        "decode": lambda b: float(b[0]),
    },
    0x42: {
        "name": "ECU Voltage",
        "unit": "V",
        "min": 0, "max": 65.535,
        "decode": lambda b: ((b[0] << 8) | b[1]) / 1000,
    },
    0x43: {
        "name": "Abs. Load Value",
        "unit": "%",
        "min": 0, "max": 25700,
        "decode": lambda b: ((b[0] << 8) | b[1]) * 100 / 255,
    },
    0x45: {
        "name": "Throttle (relative)",
        "unit": "%",
        "min": 0, "max": 100,
        "decode": lambda b: b[0] * 100 / 255,
    },
    0x46: {
        "name": "Ambient Temp",
        "unit": "°C",
        "min": -40, "max": 215,
        "decode": lambda b: b[0] - 40,
    },
    0x47: {
        "name": "Throttle Pos B",
        "unit": "%",
        "min": 0, "max": 100,
        "decode": lambda b: b[0] * 100 / 255,
    },
    0x49: {
        "name": "Accelerator Pedal Position D",
        "unit": "%",
        "min": 0, "max": 100,
        "decode": lambda b: b[0] * 100 / 255,
    },
    0x4C: {
        "name": "Commanded Throttle",
        "unit": "%",
        "min": 0, "max": 100,
        "decode": lambda b: b[0] * 100 / 255,
    },
    0x5C: {
        "name": "Oil Temp",
        "unit": "°C",
        "min": -40, "max": 210,
        "decode": lambda b: b[0] - 40,
    },
    0x5E: {
        "name": "Fuel Rate",
        "unit": "L/h",
        "min": 0, "max": 3212.75,
        "decode": lambda b: ((b[0] << 8) | b[1]) * 0.05,
    },
}



def _u16(b) -> int:
    return (b[0] << 8) | b[1]


def _s16(b) -> int:
    v = _u16(b)
    return v - 0x10000 if v & 0x8000 else v


def _add(pid, name, unit, lo, hi, decode):
    PID_TABLE.setdefault(pid, {"name": name, "unit": unit, "min": lo, "max": hi,
                               "decode": decode})


# Fuel trims share one formula: 100/128 * A - 100.
def _trim(b):
    return (b[0] - 128) * 100 / 128


def _pct(b):
    return b[0] * 100 / 255


_add(0x08, "Short Fuel Trim B2", "%", -100, 99.2, _trim)
_add(0x09, "Long Fuel Trim B2", "%", -100, 99.2, _trim)
_add(0x0A, "Fuel Pressure", "kPa", 0, 765, lambda b: 3 * b[0])
for _n in range(8):
    # 0x14..0x1B: voltage in A; B is that sensor's short term trim
    _add(0x14 + _n, f"O2 Sensor {_n + 1} Voltage", "V", 0, 1.275,
         lambda b: b[0] / 200)
_add(0x22, "Fuel Rail Pressure (vacuum ref.)", "kPa", 0, 5177.265,
     lambda b: 0.079 * _u16(b))
_add(0x23, "Fuel Rail Gauge Pressure", "kPa", 0, 655350, lambda b: 10 * _u16(b))
for _n in range(8):
    # 0x24..0x2B: wide-range sensors report an equivalence ratio in A, B
    _add(0x24 + _n, f"O2 Sensor {_n + 1} Equivalence Ratio", "", 0, 2,
         lambda b: 2 / 65536 * _u16(b))
_add(0x2D, "EGR Error", "%", -100, 99.2, _trim)
_add(0x2E, "Commanded Evaporative Purge", "%", 0, 100, _pct)
_add(0x30, "Warm-ups Since Codes Cleared", "", 0, 255, lambda b: b[0])
_add(0x32, "Evap System Vapor Pressure", "Pa", -8192, 8191.75, lambda b: _s16(b) / 4)
for _n, _where in enumerate(("Bank 1, Sensor 1", "Bank 2, Sensor 1",
                             "Bank 1, Sensor 2", "Bank 2, Sensor 2")):
    _add(0x3C + _n, f"Catalyst Temperature {_where}", "°C", -40, 6513.5,
         lambda b: _u16(b) / 10 - 40)
_add(0x44, "Commanded Equivalence Ratio", "", 0, 2, lambda b: 2 / 65536 * _u16(b))
_add(0x48, "Absolute Throttle Position C", "%", 0, 100, _pct)
_add(0x4A, "Accelerator Pedal Position E", "%", 0, 100, _pct)
_add(0x4B, "Accelerator Pedal Position F", "%", 0, 100, _pct)
_add(0x4D, "Time Run With MIL On", "min", 0, 65535, lambda b: _u16(b))
_add(0x4E, "Time Since Trouble Codes Cleared", "min", 0, 65535, lambda b: _u16(b))
_add(0x50, "Maximum Air Flow Rate", "g/s", 0, 2550, lambda b: b[0] * 10)
_add(0x52, "Ethanol Fuel", "%", 0, 100, _pct)
_add(0x53, "Absolute Evap System Vapor Pressure", "kPa", 0, 327.675,
     lambda b: _u16(b) / 200)
_add(0x54, "Evap System Vapor Pressure (wide)", "Pa", -32768, 32767, lambda b: _s16(b))
_add(0x55, "Short Secondary O2 Trim B1", "%", -100, 99.2, _trim)
_add(0x56, "Long Secondary O2 Trim B1", "%", -100, 99.2, _trim)
_add(0x57, "Short Secondary O2 Trim B2", "%", -100, 99.2, _trim)
_add(0x58, "Long Secondary O2 Trim B2", "%", -100, 99.2, _trim)
_add(0x59, "Fuel Rail Absolute Pressure", "kPa", 0, 655350, lambda b: 10 * _u16(b))
_add(0x5A, "Relative Accelerator Pedal Position", "%", 0, 100, _pct)
_add(0x5B, "Hybrid Battery Remaining Life", "%", 0, 100, _pct)
_add(0x5D, "Fuel Injection Timing", "°", -210, 301.992, lambda b: _u16(b) / 128 - 210)
_add(0x61, "Driver's Demand Engine Torque", "%", -125, 130, lambda b: b[0] - 125)
_add(0x62, "Actual Engine Torque", "%", -125, 130, lambda b: b[0] - 125)
_add(0x63, "Engine Reference Torque", "Nm", 0, 65535, lambda b: _u16(b))
_add(0x8E, "Engine Friction Torque", "%", -125, 130, lambda b: b[0] - 125)
_add(0xA6, "Odometer", "km", 0, 429496729.5,
     lambda b: ((b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]) / 10)

#: How many data bytes each PID's answer carries, for the ones that are not one.
_TWO_BYTE = {0x0C, 0x10, 0x1F, 0x21, 0x22, 0x23, 0x31, 0x32, 0x3C, 0x3D, 0x3E, 0x3F,
             0x42, 0x43, 0x44, 0x4D, 0x4E, 0x53, 0x54, 0x59, 0x5D, 0x5E, 0x63,
             *range(0x24, 0x2C)}
_FOUR_BYTE = {0xA6}


def response_length(pid: int) -> int:
    """Data bytes in a Mode 01 answer for ``pid`` (after 41 and the PID)."""
    if pid in _FOUR_BYTE:
        return 4
    return 2 if pid in _TWO_BYTE else 1


# ── diagnostic trouble codes (modes 03, 07, 0A) ──────────────────────────────

#: Positive response byte for each DTC-reading mode, and what it lists.
DTC_MODES = {0x03: (0x43, "stored"), 0x07: (0x47, "pending"), 0x0A: (0x4A, "permanent")}


def decode_obd_dtcs(payload: bytes, mode: int = 0x03) -> list[str] | None:
    """The codes in a mode 03, 07 or 0A answer, or None if it is not one.

    Over CAN (ISO 15765-4) the answer is ``43 <count> <hi> <lo> ...``: a count
    byte, then two bytes per code. An empty list means the ECU answered and
    has no codes, which is not the same thing as no answer at all.
    """
    from canlab.core.uds import decode_dtc
    expect = DTC_MODES.get(mode, (0x43, ""))[0]
    if not payload or payload[0] != expect:
        return None
    body = payload[1:]
    if body and len(body) % 2 == 1:
        count, body = body[0], body[1:]
    else:
        count = len(body) // 2
    codes = []
    for i in range(0, min(len(body), 2 * count), 2):
        hi, lo = body[i], body[i + 1]
        if hi == 0 and lo == 0:
            continue                      # padding, not P0000
        codes.append(decode_dtc(hi, lo))
    return codes


# Subset shown by default on the gauge tab
DEFAULT_PIDS = [0x0C, 0x0D, 0x05, 0x11, 0x10, 0x2F]


def decode_pid(pid: int, data: bytes) -> float | None:
    """Decode a Mode 01 PID response payload (bytes after SID/PID stripped)."""
    entry = PID_TABLE.get(pid)
    if entry is None or len(data) < response_length(pid):
        return None
    try:
        return float(entry["decode"](data))
    except Exception:
        return None


def discover_supported(request, bases=(0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0)):
    """Ask which PIDs the vehicle supports; ``request(bytes)`` returns the
    response payload or None. Returns (supported pids, answered at all).

    PID 0x00 lists 0x01-0x20, and if 0x20 is among them, PID 0x20 lists the
    next window, and so on. A vehicle that does not answer 0x00 does not
    speak Mode 01, and asking it for sixty PIDs one timeout at a time tells
    you nothing more.
    """
    found: list[int] = []
    answered = False
    for base in bases:
        payload = request(bytes([0x01, base]))
        if not (payload and len(payload) >= 6 and payload[0] == 0x41 and payload[1] == base):
            break
        answered = True
        window = supported_pids_from_mask(payload[2:6], base=base)
        found.extend(window)
        if (base + 0x20) not in window:
            break
    return found, answered


def supported_pids_from_mask(mask_data: bytes, base: int = 0x00) -> list[int]:
    """Parse a 4-byte 'supported PIDs' bit-mask into a list of PID numbers.

    ``base`` is the query PID (0x00, 0x20, 0x40, 0x60, 0x80, 0xA0, 0xC0). Bit i
    (MSB first) of the mask means PID ``base + i`` is supported, so this decodes
    the continuation windows too, not just PIDs 1–32.
    """
    if len(mask_data) < 4:
        return []
    mask = (mask_data[0] << 24) | (mask_data[1] << 16) | (mask_data[2] << 8) | mask_data[3]
    return [base + i for i in range(1, 33) if mask & (1 << (32 - i))]
