"""SAE J1979 Mode 01 PID table with decoder lambdas.

Each entry: {name, unit, min, max, decode}
  decode(data: bytes) -> float   (data = response payload bytes A, B, C, D...)
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
        "name": "Throttle Pos D",
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

# Subset shown by default on the gauge tab
DEFAULT_PIDS = [0x0C, 0x0D, 0x05, 0x11, 0x10, 0x2F]


def decode_pid(pid: int, data: bytes) -> float | None:
    """Decode a Mode 01 PID response payload (bytes after SID/PID stripped)."""
    entry = PID_TABLE.get(pid)
    if entry is None or not data:
        return None
    try:
        return float(entry["decode"](data))
    except Exception:
        return None


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
