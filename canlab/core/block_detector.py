"""Find runs of near-identical messages and propose a shared layout.

A battery pack with 96 cells does not get 96 signals in one frame. It gets a
block of consecutive identifiers, each carrying the same layout for a few
cells, all sent at the same rate with the same length. On the Tigor EV that
block is 0x380 to 0x39A: 27 messages at 2 Hz, DLC 8, nine of them never
changing. Per-ID analysis sees 27 unrelated messages. This module sees the
block: consecutive IDs with the same DLC and rate, a layout the members
agree on, and a proposal for the field they share, so one decision ("byte 2,
16 bits, big-endian") becomes a candidate signal in every member at once.

Nothing here claims a unit. A block that agrees is evidence of a repeated
structure, not proof of what the structure means; every name it emits ends
in CANDIDATE and every description says the scale is unknown.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from canlab.core.canid import normalize_id

BYTE_COLS = [f"B{i}" for i in range(8)]
ROLES = ("constant", "counter", "value", "noise")
NOISE_BITS = 7.0          # a byte spread almost evenly over 256 values
COUNTER_SHARE = 0.8       # share of steps that are +1 for a byte to be a counter


@dataclass
class BlockMember:
    can_id: str
    arb: int
    frames: int
    rate_hz: float
    dlc: int
    roles: list[str]
    constant: bool

    def as_dict(self) -> dict:
        return {"id": self.can_id, "frames": self.frames, "rate_hz": round(self.rate_hz, 3),
                "dlc": self.dlc, "roles": list(self.roles), "constant": self.constant}


@dataclass
class FieldProposal:
    start_byte: int
    width_bits: int
    byte_order: str
    members_active: int
    consistency: float
    band: tuple[float, float]
    medians: dict[str, float]
    scale_guess: float = 1.0
    hint: str = ""

    @property
    def label(self) -> str:
        end = self.start_byte + self.width_bits // 8 - 1
        where = f"B{self.start_byte}" if end == self.start_byte else f"B{self.start_byte}-{end}"
        return f"{where} {self.width_bits}-bit {self.byte_order}"

    def as_dict(self) -> dict:
        return {"start_byte": self.start_byte, "width_bits": self.width_bits,
                "byte_order": self.byte_order, "members_active": self.members_active,
                "consistency": round(self.consistency, 4),
                "band": [round(self.band[0], 2), round(self.band[1], 2)],
                "medians": {k: round(v, 2) for k, v in self.medians.items()},
                "scale_guess": self.scale_guess, "hint": self.hint, "label": self.label}


@dataclass
class Block:
    first: str
    last: str
    dlc: int
    rate_hz: float
    gap_max: int
    members: list[BlockMember]
    layout_agreement: float
    constant_members: int
    fields: list[FieldProposal] = field(default_factory=list)
    score: float = 0.0

    @property
    def name(self) -> str:
        return f"BLOCK_{self.first}_{self.last}"

    @property
    def best_field(self) -> FieldProposal | None:
        return self.fields[0] if self.fields else None

    def as_dict(self) -> dict:
        return {"name": self.name, "first": self.first, "last": self.last,
                "members": len(self.members), "dlc": self.dlc,
                "rate_hz": round(self.rate_hz, 3), "gap_max": self.gap_max,
                "layout_agreement": round(self.layout_agreement, 4),
                "constant_members": self.constant_members, "score": round(self.score, 4),
                "ids": [m.can_id for m in self.members],
                "member_roles": {m.can_id: m.roles for m in self.members},
                "fields": [f.as_dict() for f in self.fields]}


# ── per-ID statistics ────────────────────────────────────────────────────────

def _entropy_bits(vals: np.ndarray) -> float:
    counts = np.bincount(vals.astype(np.int64) & 0xFF, minlength=256).astype(float)
    probs = counts[counts > 0] / counts.sum()
    return float(-np.sum(probs * np.log2(probs)))


def _counter_share(col: np.ndarray) -> float:
    """Share of consecutive steps that are +1, for the byte and either nibble."""
    if len(col) < 3:
        return 0.0
    v = col.astype(np.int64)
    best = 0.0
    for series, modulus in ((v, 256), (v >> 4, 16), (v & 0x0F, 16)):
        step = (np.diff(series) % modulus) == 1
        best = max(best, float(step.mean()))
    return best


def _roles(mat: np.ndarray, dlc: int) -> list[str]:
    roles: list[str] = []
    for i in range(min(dlc, 8)):
        col = mat[:, i]
        col = col[np.isfinite(col)]
        if len(col) == 0 or np.ptp(col) == 0:
            roles.append("constant")
        elif _counter_share(col) >= COUNTER_SHARE:
            roles.append("counter")
        elif _entropy_bits(col) > NOISE_BITS:
            roles.append("noise")
        else:
            roles.append("value")
    return roles


def _id_matrices(df: pd.DataFrame) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """{id: (timestamps, byte matrix)} with one sort of the whole capture."""
    ids = df["ID"].astype(str).to_numpy()
    order = np.argsort(ids, kind="stable")
    ids_sorted = ids[order]
    ts = pd.to_numeric(df["Timestamp"], errors="coerce").to_numpy(dtype=float)[order]
    mat = np.full((len(df), 8), np.nan)
    for i, c in enumerate(BYTE_COLS):
        if c in df.columns:
            mat[:, i] = pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float)[order]
    out = {}
    uniq, starts = np.unique(ids_sorted, return_index=True)
    bounds = list(starts) + [len(ids_sorted)]
    for k, cid in enumerate(uniq):
        out[str(cid)] = (ts[bounds[k]:bounds[k + 1]], mat[bounds[k]:bounds[k + 1]])
    return out


def _dlc_mode(df: pd.DataFrame) -> dict[str, int]:
    if "DLC" not in df.columns:
        return {}
    modes = df.groupby(df["ID"].astype(str))["DLC"].agg(
        lambda s: int(pd.to_numeric(s, errors="coerce").mode().iloc[0]) if len(s) else 8)
    return {str(k): int(v) for k, v in modes.items()}


# ── field proposals ──────────────────────────────────────────────────────────

def _read(mat: np.ndarray, start: int, width: int, big: bool) -> np.ndarray:
    if width == 8:
        return mat[:, start]
    lo, hi = mat[:, start], mat[:, start + 1]
    return 256.0 * lo + hi if big else lo + 256.0 * hi


def _smoothness(x: np.ndarray) -> float:
    """Normalised mean absolute first difference; lower reads as smoother."""
    x = x[np.isfinite(x)]
    if len(x) < 2 or np.ptp(x) == 0:
        return 0.0
    return float(np.mean(np.abs(np.diff(x))) / np.ptp(x))


def _propose_fields(members: list[BlockMember], mats: dict[str, np.ndarray],
                    dlc: int, min_members: int) -> list[FieldProposal]:
    proposals: list[FieldProposal] = []
    candidates: list[tuple[int, int]] = [(b, 8) for b in range(min(dlc, 8))]
    candidates += [(b, 16) for b in (0, 2, 4, 6) if b + 1 < min(dlc, 8)]
    for start, width in candidates:
        by_order: dict[str, dict] = {}
        for order in (("big", "little") if width == 16 else ("little",)):
            medians: dict[str, float] = {}
            lo, hi, rough = np.inf, -np.inf, []
            low_varies, high_varies, high_nonzero = False, False, False
            for m in members:
                span = m.roles[start:start + width // 8]
                # a member carries the field only where it holds a value:
                # a counter or a checksum byte is never a shared quantity
                if "value" not in span or "counter" in span or "noise" in span:
                    continue
                mat = mats[m.can_id]
                vals = _read(mat, start, width, order == "big")
                vals = vals[np.isfinite(vals)]
                if len(vals) == 0:
                    continue
                if width == 16:
                    low_i, high_i = (start + 1, start) if order == "big" else (start, start + 1)
                    low_varies |= span[low_i - start] != "constant"
                    high_varies |= span[high_i - start] != "constant"
                    high_nonzero |= bool(np.nanmax(mat[:, high_i]) > 0)
                medians[m.can_id] = float(np.median(vals))
                lo, hi = min(lo, float(vals.min())), max(hi, float(vals.max()))
                rough.append(_smoothness(vals))
            if len(medians) < min_members:
                continue
            # A word whose low byte never moves is a byte read with a shift;
            # a word whose high byte is always zero is the low byte again.
            # Either is already covered by the 8-bit proposal. A constant but
            # non-zero high byte is kept: cell voltages at 0x0E10..0x0E74
            # look exactly like that.
            if width == 16 and (not low_varies or (not high_varies and not high_nonzero)):
                continue
            by_order[order] = {"medians": medians, "band": (lo, hi),
                               "rough": float(np.mean(rough)) if rough else 0.0}
        if not by_order:
            continue
        # a value read the right way round changes a little at a time
        order = min(by_order, key=lambda o: (round(by_order[o]["rough"], 6), o != "big"))
        pick = by_order[order]
        meds = np.array(list(pick["medians"].values()))
        spread = float(np.ptp(meds))
        scale = max(1.0, float(np.max(np.abs(meds))))
        consistency = max(0.0, 1.0 - spread / scale)
        centre = float(np.median(meds))
        hint = (f"{len(meds)} members hold values within {100 * spread / scale:.1f}% of "
                f"each other around {centre:.0f}; scale unknown")
        proposals.append(FieldProposal(start, width, order, len(meds), consistency,
                                       pick["band"], pick["medians"], 1.0, hint))
    # Most members first. Among fields the members agree on (consistency of
    # at least a half) the wider reading is the better proposal: a word the
    # block shares says more than the byte inside it. Fields the members do
    # not agree on rank last, however wide.
    proposals.sort(key=lambda p: (-p.members_active, p.consistency < 0.5,
                                  -p.width_bits, -p.consistency))
    return proposals


# ── the detector ─────────────────────────────────────────────────────────────

def detect_blocks(df: pd.DataFrame, *, min_members: int = 3, max_gap: int = 2,
                  rate_tol: float = 0.15, min_frames: int = 20,
                  should_stop=None) -> list[Block]:
    """Blocks of consecutive IDs with one DLC, one rate and a shared layout.

    ``max_gap`` is the largest jump in ID value that keeps a run going (2
    lets a block skip one identifier). ``rate_tol`` is the relative
    distance from the run's median rate a member may sit at. IDs with fewer
    than ``min_frames`` frames are ignored and do not break a run.
    """
    if df.empty or "ID" not in df.columns:
        return []
    mats = _id_matrices(df)
    dlcs = _dlc_mode(df)
    stats: list[BlockMember] = []
    for cid, (ts, mat) in mats.items():
        if should_stop is not None and should_stop():
            return []
        n = len(ts)
        if n < min_frames:
            continue
        try:
            arb = int(normalize_id(cid), 16)
        except ValueError:
            continue
        span = float(np.nanmax(ts) - np.nanmin(ts)) if n > 1 else 0.0
        rate = (n - 1) / span if span > 0 else 0.0
        dlc = dlcs.get(cid, 8)
        roles = _roles(mat, dlc)
        stats.append(BlockMember(normalize_id(cid), arb, n, rate, dlc, roles,
                                 all(r == "constant" for r in roles)))
    stats.sort(key=lambda m: m.arb)

    runs: list[list[BlockMember]] = []
    run: list[BlockMember] = []
    for m in stats:
        if run:
            median_rate = float(np.median([r.rate_hz for r in run]))
            close = abs(m.rate_hz - median_rate) <= rate_tol * max(median_rate, 1e-9)
            if m.arb - run[-1].arb <= max_gap and m.dlc == run[-1].dlc and close:
                run.append(m)
                continue
            runs.append(run)
        run = [m]
    if run:
        runs.append(run)

    blocks: list[Block] = []
    for members in runs:
        if len(members) < min_members:
            continue
        if should_stop is not None and should_stop():
            break
        dlc = members[0].dlc
        agreement = []
        for i in range(min(dlc, 8)):
            roles = [m.roles[i] for m in members if i < len(m.roles)]
            if roles:
                agreement.append(max(roles.count(r) for r in ROLES) / len(roles))
        layout_agreement = float(np.mean(agreement)) if agreement else 0.0
        fields = _propose_fields(members, {m.can_id: mats[m.can_id][1] for m in members},
                                 dlc, min_members)
        gap = max(b.arb - a.arb for a, b in zip(members, members[1:]))
        best = fields[0].consistency if fields else 0.0
        blocks.append(Block(
            first=members[0].can_id, last=members[-1].can_id, dlc=dlc,
            rate_hz=float(np.median([m.rate_hz for m in members])), gap_max=gap,
            members=members, layout_agreement=layout_agreement,
            constant_members=sum(1 for m in members if m.constant), fields=fields,
            score=0.5 * layout_agreement + 0.5 * best))
    blocks.sort(key=lambda b: (-len(b.members), -b.score))
    return blocks


def block_to_signals(block: Block, field: FieldProposal, prefix: str | None = None) -> list[dict]:
    """One candidate signal per member the field is live in. Scale 1, offset 0,
    the name says CANDIDATE and the description says the scale is unknown."""
    prefix = (prefix or block.name).strip() or block.name
    width = field.width_bits
    start_bit = field.start_byte * 8 + (7 if field.byte_order == "big" and width > 8 else 0)
    out = []
    for index, m in enumerate(block.members):
        if m.can_id not in field.medians:
            continue
        out.append({
            "message_id": m.can_id,
            "signal_name": f"{prefix}_B{field.start_byte}_{index:02d}_CANDIDATE",
            "start_bit": start_bit,
            "length": width,
            "byte_order": field.byte_order if width > 8 else "little",
            "value_type": "unsigned",
            "scale": 1.0,
            "offset": 0.0,
            "min_val": 0,
            "max_val": (1 << width) - 1,
            "unit": "",
            "msg_length": block.dlc,
            "description": (f"Block {block.first}..{block.last} member {index + 1} of "
                            f"{len(block.members)}; field {field.label}; consistency "
                            f"{field.consistency:.0%}; scale unknown"),
        })
    return out
