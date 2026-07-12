"""Canonical CAN arbitration-ID normalization — single source of truth.

The whole app stores IDs as uppercase hex, zero-padded to a minimum of three
digits and *without* an ``0x`` prefix (e.g. ``"0A6"``, ``"260"``, ``"18FEF100"``).

Historically several call-sites tried to strip a leading ``0x`` with
``text.lstrip("0x")`` — but ``str.lstrip`` strips a *character set*, not a
prefix, so ``"0A6".lstrip("0x")`` returns ``"A6"`` and every ID below 0x100 was
silently corrupted. Route all ID normalization through :func:`normalize_id`
instead.
"""
from __future__ import annotations


def normalize_id(val) -> str:
    """Return the canonical hex string form of a CAN arbitration ID.

    Accepts ints, or strings that may carry an ``0x``/``0X`` prefix, surrounding
    whitespace, or mixed case. Returns uppercase hex, zero-padded to at least
    three digits. Non-numeric input is returned upper-cased and stripped so the
    caller still gets a stable key rather than a crash.
    """
    if isinstance(val, str):
        s = val.strip()
        if s[:2].lower() == "0x":
            s = s[2:]
        try:
            return format(int(s, 16), "03X")
        except (ValueError, TypeError):
            return s.upper()
    try:
        return format(int(val), "03X")
    except (ValueError, TypeError):
        return str(val).strip().upper()
