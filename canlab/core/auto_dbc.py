"""Draft DBC signal definitions from statistical analysis.

These are *starting points* for the DBC Builder, not identifications: the
byte-role classifier says which bytes look like counters, checksums or padding,
and adjacent payload bytes are grouped into candidate signals. Names are
generic — guessing OEM signal names from an ID is how a tool ends up confidently
wrong. (The previous version read statistics keys the analyzer never produced,
so every message came out as a single 8-bit signal at byte 0, and it carried a
table of Hyundai names that disagreed with opendbc.)
"""
from __future__ import annotations

import logging

from canlab.core.signal_analyzer import analyze_id
from canlab.core.state import get_state

log = logging.getLogger(__name__)

# A byte must vary at least this much to be worth proposing as a signal.
MIN_RANGE = 2


def _byte_roles(frames, can_id: str) -> dict:
    try:
        from canlab.core.signal_classifier import classify_frame
        return classify_frame(frames, can_id) or {}
    except Exception:
        log.debug("byte-role classification failed for %s", can_id, exc_info=True)
        return {}


def _candidate_groups(stats: dict, roles: dict) -> list[list[int]]:
    """Group adjacent payload bytes that vary into candidate signals."""
    byte_stats = stats.get("bytes", {})
    groups: list[list[int]] = []
    current: list[int] = []
    for idx in range(64):
        col = f"B{idx}"
        b = byte_stats.get(col)
        if b is None:
            break
        role = (roles.get(col) or {}).get("role", "")
        interesting = (role not in ("COUNTER", "CHECKSUM", "PADDING")
                       and b.get("range", 0) >= MIN_RANGE)
        if interesting:
            current.append(idx)
            if len(current) == 2:          # cap a run at 16 bits
                groups.append(current)
                current = []
        else:
            if current:
                groups.append(current)
                current = []
    if current:
        groups.append(current)
    return groups


def build_from_analyzer(state=None) -> list:
    """Propose signal dicts for every loaded ID (the caller decides to add them)."""
    if state is None:
        state = get_state()
    if len(state.store) == 0:
        return []

    signals = []
    for can_id in state.get_unique_ids():
        frames = state.get_frames_for_id(can_id)
        if frames.empty:
            continue
        stats = analyze_id(frames)
        roles = _byte_roles(frames, can_id)
        msg_name = f"MSG_{can_id}"
        msg_type = stats.get("suspected_type", "UNKNOWN")

        for group in _candidate_groups(stats, roles):
            length = 8 * len(group)
            start = group[0] * 8
            signals.append({
                "message_id":   can_id,
                "message_name": msg_name,
                "signal_name":  f"{msg_name}_B{group[0]}",
                "start_bit":    start,
                "length":       length,
                "byte_order":   "little",
                "value_type":   "unsigned",
                "scale":        1.0,
                "offset":       0.0,
                "min_val":      0,
                "max_val":      (2 ** length) - 1,
                "unit":         "",
                "description":  (f"Auto-drafted from {msg_type} message "
                                 f"0x{can_id}; scale/offset unverified"),
            })

        # Record the roles we did identify, so the counter/checksum bytes are
        # visible in the builder rather than silently dropped.
        for col, info in sorted(roles.items()):
            role = info.get("role", "")
            if role not in ("COUNTER", "CHECKSUM"):
                continue
            idx = int(col[1:])
            signals.append({
                "message_id":   can_id,
                "message_name": msg_name,
                "signal_name":  f"{msg_name}_{role}",
                "start_bit":    idx * 8,
                "length":       8,
                "byte_order":   "little",
                "value_type":   "unsigned",
                "scale":        1.0,
                "offset":       0.0,
                "min_val":      0,
                "max_val":      255,
                "unit":         "",
                "description":  f"Detected {role.lower()} byte",
            })
    return signals
