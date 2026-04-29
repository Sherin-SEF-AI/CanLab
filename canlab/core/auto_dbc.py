"""Auto-generate DBC signal definitions from statistical analysis."""
from core.signal_analyzer import analyze_id, _classify
from core.state import get_state


# Known Hyundai Kona message names keyed by hex ID
_KNOWN_IDS = {
    "018": ("MDPS12",    "Motor-Driven Power Steering torque"),
    "02C": ("BRAKE11",   "Brake pressure and switch"),
    "050": ("LKAS11",    "Lane Keep Assist"),
    "0A6": ("WHL_SPD11", "Wheel speeds (x4)"),
    "251": ("MDPS11",    "MDPS status"),
    "260": ("SAS11",     "Steering angle"),
    "316": ("TCS13",     "Traction control status"),
    "544": ("CLU11",     "Cluster / speed"),
    "593": ("TPMS11",    "Tyre pressure"),
    "4F1": ("CLUSTER11", "Instrument cluster"),
}

_UNIT_MAP = {
    "SENSOR":       "val",
    "COUNTER":      "",
    "STATUS_FLAG":  "",
    "DIAGNOSTIC":   "",
    "UNKNOWN":      "",
}


def build_from_analyzer(state=None) -> list:
    """
    Iterate all loaded IDs, run signal_analyzer, and produce a list of signal
    dicts compatible with state.dbc_signals.  Signals are NOT added to state
    here — caller decides.
    """
    if state is None:
        state = get_state()

    if state.frames_df.empty:
        return []

    signals = []
    for can_id in state.get_unique_ids():
        frames = state.get_frames_for_id(can_id)
        stats  = analyze_id(frames)
        sig_type = stats.get("suspected_type", "UNKNOWN")

        msg_name, description = _KNOWN_IDS.get(
            can_id.upper(), (f"MSG_{can_id}", f"Auto-detected {sig_type}")
        )

        # Dominant byte (highest entropy) carries the primary signal
        entropies = stats.get("byte_entropy", [0]*8)
        dom_byte  = int(entropies.index(max(entropies))) if entropies else 0

        # Estimate length from unique value range
        byte_range = stats.get("byte_ranges", [])
        if byte_range and dom_byte < len(byte_range):
            lo, hi = byte_range[dom_byte]
            raw_range = max(1, hi - lo)
        else:
            raw_range = 255

        length = 8
        if raw_range <= 1:
            length = 1
        elif raw_range <= 15:
            length = 4
        elif raw_range <= 255:
            length = 8
        elif raw_range <= 65535:
            length = 16

        freq = stats.get("frequency_hz", 0)
        scale = 1.0
        if "WHL_SPD" in msg_name or "SPD" in msg_name:
            scale = 0.03125
        elif "SAS" in msg_name or "MDPS" in msg_name:
            scale = 0.1

        sig = {
            "message_id":   can_id,
            "message_name": msg_name,
            "signal_name":  f"{msg_name}_SIG{dom_byte}",
            "start_bit":    dom_byte * 8,
            "length":       length,
            "byte_order":   "little",
            "value_type":   "unsigned",
            "scale":        scale,
            "offset":       0.0,
            "min_val":      0,
            "max_val":      (2 ** length) - 1,
            "unit":         _UNIT_MAP.get(sig_type, ""),
            "description":  description,
        }
        signals.append(sig)

    return signals
