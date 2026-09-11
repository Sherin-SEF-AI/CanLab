"""
Parse openpilot .rlog / .qlog files into a standard CAN frames DataFrame.

Requires pycapnp. Gracefully returns empty DataFrame if missing.

openpilot log format (simplified):
  Each log entry is a capnp-encoded Event with a union field.
  CAN frames live in Event.can[], each entry has:
    address, busTime, dat (bytes), src (bus index).
"""
from pathlib import Path
import pandas as pd
import logging

log = logging.getLogger(__name__)

_CAPNP_AVAILABLE = False
try:
    import capnp  # noqa: F401
    _CAPNP_AVAILABLE = True
except ImportError:
    log.debug("suppressed exception", exc_info=True)


def is_available() -> bool:
    return _CAPNP_AVAILABLE


def _normalize_id(val: int) -> str:
    return format(val, "03X")


def parse_rlog(filepath: str) -> pd.DataFrame:
    """
    Parse an openpilot .rlog or .qlog file → standard CAN DataFrame.

    Raises RuntimeError if pycapnp is not installed, the cereal schema is
    missing, or the log cannot be decoded. Returns an empty DataFrame only when
    the log genuinely contains no CAN events.
    """
    if not _CAPNP_AVAILABLE:
        raise RuntimeError(
            "pycapnp not installed. "
            "Run: pip install pycapnp --break-system-packages"
        )

    path = Path(filepath)

    # openpilot logs are a concatenated stream of capnp-encoded Event messages.
    # Decoding requires the cereal `log.capnp` schema. If parsing fails we raise
    # rather than fabricating frames from arbitrary bytes (the old heuristic
    # fallback emitted garbage "frames" that looked real).
    try:
        rows = _parse_with_cereal(path)
    except FileNotFoundError as e:
        raise RuntimeError(
            "openpilot cereal schema (log.capnp) not found. Install openpilot's "
            "cereal or place log.capnp under canlab/resources/. An rlog cannot be "
            "decoded without it."
        ) from e
    except Exception as e:
        raise RuntimeError(f"Failed to parse openpilot rlog: {e}") from e

    from canlab.core.log_parser import _finish
    return _finish(rows)


def _parse_with_cereal(path: Path) -> list:
    """Try to read using cereal capnp schemas bundled with openpilot."""
    import capnp
    # Look for cereal schema in common openpilot locations
    import os
    schema_candidates = [
        os.path.expanduser("~/openpilot/cereal/log.capnp"),
        "/opt/openpilot/cereal/log.capnp",
        str(Path(__file__).parent.parent / "resources" / "log.capnp"),
    ]
    schema_path = next((p for p in schema_candidates if os.path.exists(p)), None)
    if not schema_path:
        raise FileNotFoundError("cereal schema (log.capnp) not found")

    log_capnp = capnp.load(schema_path)
    rows = []
    ts_base = 0.0

    # Use pycapnp's streaming reader over the concatenated message stream rather
    # than a hand-rolled length-prefix framing (which did not match the real
    # format).
    with open(path, "rb") as f:
        for event in log_capnp.Event.read_multiple(f):
            try:
                if event.which() != "can":
                    continue
                ts = event.logMonoTime / 1e9
                if ts_base == 0.0:
                    ts_base = ts
                for frame in event.can:
                    from canlab.core.log_parser import make_row
                    dat = bytes(frame.dat)[:64]
                    rows.append(make_row(ts - ts_base, frame.address,
                                         int(frame.address) > 0x7FF, frame.src, dat))
            except Exception:
                continue

    return rows


