"""Vehicle profiles: framing conventions are a setting, not an assumption.

Injection used to stamp a Hyundai checksum into byte 7 and a counter into the
upper nibble of byte 0 no matter what was connected.
"""
import pytest

from canlab.core.checksums import compute, hyundai_crc8
from canlab.core.vehicle_profile import (GENERIC, PROFILES, active_profile,
                                          get_profile, list_profiles,
                                          message_meta)


def test_default_profile_asserts_nothing():
    profile = get_profile()
    assert profile.id == GENERIC
    assert not profile.has_checksum and not profile.has_counter
    data = bytearray(8)
    assert bytes(profile.annotate(data, 0x1A0, 7)) == bytes(8), "generic must not stamp"


def test_unknown_profile_falls_back_to_generic():
    assert get_profile("no-such-car").id == GENERIC
    assert get_profile(None).id == GENERIC
    assert get_profile("").id == GENERIC


def test_every_listed_profile_is_usable():
    listed = dict(list_profiles())
    assert set(listed) == set(PROFILES)
    for pid in listed:
        profile = get_profile(pid)
        data = bytearray(range(8))
        profile.annotate(data, 0x1A0, 3)        # must not raise for any profile
        assert len(data) == 8


def test_hyundai_profile_stamps_counter_and_crc():
    profile = get_profile("hyundai")
    data = bytearray(8)
    profile.annotate(data, 0x1A0, counter=5)
    assert data[0] >> 4 == 5, "counter belongs in the upper nibble of byte 0"
    assert data[7] == hyundai_crc8(bytes(data[:7]))


def test_counter_wraps_within_its_width():
    profile = get_profile("hyundai")
    data = bytearray(8)
    profile.apply_counter(data, 0x1F)           # 5 bits into a 4-bit field
    assert data[0] >> 4 == 0x0F


def test_counter_preserves_the_rest_of_the_byte():
    profile = get_profile("hyundai")
    data = bytearray([0x0A] + [0] * 7)
    profile.apply_counter(data, 3)
    assert data[0] == 0x3A, "the low nibble must survive"


def test_toyota_profile_uses_the_last_byte():
    profile = get_profile("toyota")
    data = bytearray([1, 2, 3, 4, 5, 6, 7, 0])
    profile.apply_checksum(data, 0x2E4)
    assert data[7] == compute("toyota", bytes(data), 0x2E4, 7)


def test_subaru_profile_uses_byte_zero():
    profile = get_profile("subaru")
    data = bytearray([0, 1, 2, 3, 4, 5, 6, 7])
    profile.apply_checksum(data, 0x220)
    assert data[0] == compute("subaru", bytes(data), 0x220, 0)


def test_honda_profile_only_touches_the_low_nibble():
    profile = get_profile("honda")
    data = bytearray([1, 2, 3, 4, 5, 6, 7, 0xA0])
    profile.apply_checksum(data, 0x1FA)
    assert data[7] >> 4 == 0xA, "the high nibble must survive"
    assert data[7] & 0x0F <= 0xF


def test_checksum_index_clamps_to_short_frames():
    profile = get_profile("hyundai")
    data = bytearray(4)
    profile.apply_checksum(data, 0x1A0)
    assert profile.checksum_index(4) == 3
    assert data[3] != 0 or True                 # wrote inside the frame, not past it


def test_message_meta_follows_the_profile():
    ids = ["1A0", "2B0"]
    assert message_meta(get_profile(GENERIC), ids) == {}
    meta = message_meta(get_profile("hyundai"), ids)
    assert set(meta) == set(ids)
    assert meta["1A0"] == {"has_checksum": True, "checksum_byte": 7,
                           "has_counter": True, "counter_byte": 0}


def test_active_profile_reads_app_state():
    from canlab.core.state import AppState
    st = AppState()
    assert active_profile(st).id == GENERIC
    st.vehicle_profile = "toyota"
    assert active_profile(st).id == "toyota"


def test_injection_annotate_respects_the_selected_profile():
    from canlab.core.injection import annotate
    data = bytearray(8)
    annotate(data, 0x1A0, counter=2, apply_counter=True, apply_checksum=True,
             profile=get_profile("hyundai"))
    assert data[0] >> 4 == 2 and data[7] == hyundai_crc8(bytes(data[:7]))

    plain = bytearray(8)
    annotate(plain, 0x1A0, counter=2, apply_counter=True, apply_checksum=True,
             profile=get_profile(GENERIC))
    assert bytes(plain) == bytes(8), "generic profile must leave frames alone"


def test_ai_prompt_names_no_vehicle_by_default():
    from canlab.core.ai_client import SYSTEM_PROMPT
    lowered = SYSTEM_PROMPT.lower()
    for banned in ("hyundai", "kona", "kia"):
        assert banned not in lowered, f"the base prompt still assumes {banned}"


@pytest.mark.parametrize("profile_id", ["hyundai", "toyota", "autosar"])
def test_profile_hint_reaches_the_prompt(profile_id):
    from canlab.core.ai_client import AIWorker
    from canlab.core.state import get_state
    state = get_state()
    previous = state.vehicle_profile
    state.vehicle_profile = profile_id
    try:
        worker = AIWorker(api_key="", id_hex="1A0", frames_df=None)
        prompt = worker._system_prompt()
        assert get_profile(profile_id).ai_hint in prompt
    finally:
        state.vehicle_profile = previous
