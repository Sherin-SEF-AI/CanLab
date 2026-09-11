"""Selectable vehicle profiles: where a counter and checksum live, and which
algorithm computes the checksum.

CanLab used to assume one car. Injected frames were stamped with a Hyundai
checksum in byte 7 and a counter in the upper nibble of byte 0 whatever the
target, the openpilot exporter annotated every message with Hyundai metadata,
and the AI prompt asserted the vehicle was a Kona. None of that is knowable
from a capture, so it is a setting now, and the default asserts nothing.

Profiles only describe framing conventions. They never rename a signal or
claim to identify a vehicle.
"""
from __future__ import annotations

from dataclasses import dataclass

from canlab.core.checksums import ALGORITHMS, compute

GENERIC = "generic"


@dataclass(frozen=True)
class VehicleProfile:
    id: str
    name: str
    checksum_algorithm: str | None = None   # key into checksums.ALGORITHMS
    checksum_byte: int | None = None        # None = the last byte of the frame
    counter_byte: int | None = None
    counter_nibble: str = "upper"           # "upper" | "lower" | "full"
    counter_bits: int = 4
    ai_hint: str = ""

    # ── framing ──────────────────────────────────────────────────────────
    @property
    def has_checksum(self) -> bool:
        return self.checksum_algorithm is not None

    @property
    def has_counter(self) -> bool:
        return self.counter_byte is not None

    def checksum_index(self, length: int) -> int:
        if self.checksum_byte is None:
            return length - 1
        return min(self.checksum_byte, length - 1)

    def apply_counter(self, data: bytearray, counter: int) -> None:
        """Write ``counter`` into its nibble/byte, leaving the rest untouched."""
        if not self.has_counter or self.counter_byte >= len(data):
            return
        idx = self.counter_byte
        mask = (1 << self.counter_bits) - 1
        value = counter & mask
        if self.counter_nibble == "full":
            data[idx] = value & 0xFF
        elif self.counter_nibble == "lower":
            data[idx] = (data[idx] & ~mask & 0xFF) | value
        else:                                   # upper
            data[idx] = (data[idx] & 0x0F) | ((value & 0x0F) << 4)

    def apply_checksum(self, data: bytearray, arb_id: int) -> None:
        """Compute and write the checksum byte for this frame."""
        if not self.has_checksum or not len(data):
            return
        idx = self.checksum_index(len(data))
        algo = ALGORITHMS.get(self.checksum_algorithm)
        if algo is None:
            return
        value = compute(self.checksum_algorithm, bytes(data), arb_id, idx)
        if algo.width == 4:
            data[idx] = (data[idx] & 0xF0) | (value & 0x0F)
        else:
            data[idx] = value & 0xFF

    def annotate(self, data: bytearray, arb_id: int, counter: int = 0) -> bytearray:
        """Apply the counter then the checksum (order matters: the checksum
        covers the counter)."""
        self.apply_counter(data, counter)
        self.apply_checksum(data, arb_id)
        return data


PROFILES: dict[str, VehicleProfile] = {p.id: p for p in [
    VehicleProfile(GENERIC, "Generic (no counter or checksum)"),
    VehicleProfile(
        "hyundai", "Hyundai / Kia",
        checksum_algorithm="crc8_hyundai", checksum_byte=7,
        counter_byte=0, counter_nibble="upper", counter_bits=4,
        ai_hint=("Hyundai/Kia buses commonly run at 500 kbps, little-endian, "
                 "with a 4-bit rolling counter in the upper nibble of byte 0 "
                 "and a CRC-8 checksum in byte 7."),
    ),
    VehicleProfile(
        "toyota", "Toyota / Lexus",
        checksum_algorithm="toyota", checksum_byte=None,
        ai_hint=("Toyota/Lexus messages usually put the checksum in the last "
                 "byte, computed over the address, the length and the data."),
    ),
    VehicleProfile(
        "honda", "Honda / Acura",
        checksum_algorithm="honda", checksum_byte=None,
        counter_byte=None,
        ai_hint=("Honda/Acura messages carry a 4-bit checksum in the low "
                 "nibble of the last byte and a 2-bit counter above it."),
    ),
    VehicleProfile(
        "subaru", "Subaru (global)",
        checksum_algorithm="subaru", checksum_byte=0,
        ai_hint="Subaru global-platform messages put the checksum in byte 0.",
    ),
    VehicleProfile(
        "autosar", "AUTOSAR E2E (CRC8H2F)",
        checksum_algorithm="crc8_autosar", checksum_byte=0,
        counter_byte=1, counter_nibble="lower", counter_bits=4,
        ai_hint=("AUTOSAR end-to-end protection: CRC-8/H2F plus a 4-bit "
                 "sequence counter."),
    ),
]}


def list_profiles() -> list[tuple[str, str]]:
    """[(id, display name)] for a settings combo box."""
    return [(p.id, p.name) for p in PROFILES.values()]


def get_profile(profile_id: str | None = None) -> VehicleProfile:
    """The named profile, or the generic one when unknown/unset."""
    if not profile_id:
        return PROFILES[GENERIC]
    return PROFILES.get(str(profile_id), PROFILES[GENERIC])


def active_profile(state=None) -> VehicleProfile:
    """The profile currently selected in the app."""
    if state is None:
        from canlab.core.state import get_state
        state = get_state()
    return get_profile(getattr(state, "vehicle_profile", GENERIC))


def message_meta(profile: VehicleProfile, message_ids, length: int = 8) -> dict:
    """openpilot-style per-message metadata derived from the profile.

    Replaces the hard-coded Hyundai table that was attached to every export
    regardless of the vehicle.
    """
    meta = {}
    if not profile.has_checksum and not profile.has_counter:
        return meta
    for mid in message_ids:
        meta[mid] = {
            "has_checksum": profile.has_checksum,
            "checksum_byte": profile.checksum_index(length),
            "has_counter": profile.has_counter,
            "counter_byte": profile.counter_byte or 0,
        }
    return meta
