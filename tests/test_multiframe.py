"""Putting multi-frame messages back together, without ever joining in.

Every expected value here comes from a real recording: a Lake Erie marine
bus for the fast packets, a truck for the transport protocol. The frames are
lifted verbatim, so the numbers below are what the instruments said.
"""
import pytest

from canlab.core.j1939 import decode_n2k, decode_pgn, parse_j1939_id
from canlab.core.multiframe import (
    Message, Reassembler, is_transport_id, parse_tp_cm,
    reassemble_dataframe, summarize,
)
from tests.doubles import FakeMsg, RecordingBus

# ── verbatim frames ──────────────────────────────────────────────────────────

GNSS_ID = 0x0DF80523                    # PGN 129029 from source 0x23
GNSS_FRAMES = [
    bytes([0xA0, 0x2B, 0x88, 0x17, 0x49, 0xE8, 0x0D, 0xDF]),
    bytes([0xA1, 0x20, 0x00, 0x08, 0x18, 0x2C, 0xA0, 0x9F]),
    bytes([0xA2, 0xEB, 0x05, 0x00, 0x74, 0x44, 0x14, 0x48]),
    bytes([0xA3, 0xBE, 0xBA, 0xF4, 0x60, 0x66, 0x57, 0x0A]),
    bytes([0xA4, 0x00, 0x00, 0x00, 0x00, 0x23, 0xFC, 0x0A]),
    bytes([0xA5, 0x64, 0x00, 0xA0, 0x00, 0xB8, 0xF2, 0xFF]),
    bytes([0xA6, 0xFF, 0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF]),
]

BAM_CM_ID = 0x1CECFF0F                  # source 0x0F, broadcast
BAM_DT_ID = 0x1CEBFF0F
BAM_CM = bytes([0x20, 0x13, 0x00, 0x03, 0xFF, 0xE1, 0xFE, 0x00])
BAM_DT = [
    bytes([0x01, 0x03, 0x03, 0x60, 0x22, 0x4A, 0x80, 0x4D]),
    bytes([0x02, 0x27, 0x28, 0x2D, 0x2B, 0xB8, 0x42, 0x1D]),
    bytes([0x03, 0x60, 0x3B, 0x5D, 0x04, 0x19, 0xFF, 0xFF]),
]
BAM_PAYLOAD = bytes.fromhex("0303602 24a804d27282d2bb8421d603b5d0419".replace(" ", ""))


def feed(r: Reassembler, arb: int, frames, t0: float = 0.0, gap: float = 0.01):
    out = []
    for i, data in enumerate(frames):
        out.extend(r.update(arb, data, t0 + i * gap))
    return out


# ── J1939 transport protocol ─────────────────────────────────────────────────

def test_a_bam_broadcast_is_reassembled_in_order():
    r = Reassembler()
    assert r.update(BAM_CM_ID, BAM_CM, 0.0) == []
    done = feed(r, BAM_DT_ID, BAM_DT, 0.05, 0.05)
    assert len(done) == 1
    m = done[0]
    assert m.transport == "BAM" and m.protocol == "J1939"
    assert m.pgn == 0xFEE1 and m.pgn_name.startswith("RC")
    assert m.sa == 0x0F and m.da == 0xFF
    assert m.size == 19 and m.data == BAM_PAYLOAD
    assert m.frames == 4 and m.complete
    assert r.stats()["complete"] == 1 and r.stats()["dropped"] == 0


def test_the_announcement_is_read_correctly():
    cm = parse_tp_cm(BAM_CM)
    assert cm["control_name"] == "BAM" and cm["size"] == 19
    assert cm["packets"] == 3 and cm["pgn"] == 0xFEE1


def test_two_senders_broadcasting_at_once_do_not_mix():
    r = Reassembler()
    other_cm, other_dt = 0x1CECFF00, 0x1CEBFF00        # source 0x00
    r.update(BAM_CM_ID, BAM_CM, 0.0)
    r.update(other_cm, BAM_CM, 0.001)
    # interleave the two sessions packet by packet
    done = []
    for i, data in enumerate(BAM_DT):
        done += r.update(BAM_DT_ID, data, 0.01 + i * 0.05)
        done += r.update(other_dt, data, 0.02 + i * 0.05)
    assert sorted(m.sa for m in done) == [0x00, 0x0F]
    assert all(m.data == BAM_PAYLOAD for m in done)


def test_a_missing_data_packet_drops_the_session_with_a_reason():
    r = Reassembler()
    r.update(BAM_CM_ID, BAM_CM, 0.0)
    r.update(BAM_DT_ID, BAM_DT[0], 0.05)
    dropped = r.update(BAM_DT_ID, BAM_DT[2], 0.10)        # packet 2 never came
    assert len(dropped) == 1 and not dropped[0].complete
    assert dropped[0].reason == "sequence"
    assert r.stats()["by_reason"] == {"sequence": 1}
    assert list(r.messages) == []


def test_a_bam_that_stalls_past_t1_is_expired():
    r = Reassembler()
    r.update(BAM_CM_ID, BAM_CM, 0.0)
    r.update(BAM_DT_ID, BAM_DT[0], 0.05)
    # the next frame on the bus, from anyone, is a second later
    late = r.update(GNSS_ID, GNSS_FRAMES[0], 1.5)
    assert any(m.reason == "timeout" and m.pgn == 0xFEE1 for m in late)
    assert r.stats()["pending"] == 1             # only the fast packet just opened


def test_an_rts_cts_session_between_two_other_nodes_is_observed_without_sending():
    """Node 0x21 sends 19 bytes to node 0x03. CanLab watches from a
    subscription on the hub and must never put anything on the bus."""
    from canlab.core.bus_hub import BusHub

    rts = bytes([0x10, 0x13, 0x00, 0x03, 0xFF, 0xE1, 0xFE, 0x00])
    cts = bytes([0x11, 0x03, 0x01, 0xFF, 0xFF, 0xE1, 0xFE, 0x00])
    eom = bytes([0x13, 0x13, 0x00, 0x03, 0xFF, 0xE1, 0xFE, 0x00])
    to_03_from_21 = 0x1CEC0321
    to_21_from_03 = 0x1CEC2103
    dt_from_21 = 0x1CEB0321
    script = [FakeMsg(to_03_from_21, rts, True, 1.0),
              FakeMsg(to_21_from_03, cts, True, 1.01),
              *[FakeMsg(dt_from_21, d, True, 1.02 + i * 0.01) for i, d in enumerate(BAM_DT)],
              FakeMsg(to_21_from_03, eom, True, 1.1)]
    bus = RecordingBus()
    bus.feed(*script)
    hub = BusHub(bus, name="t", bitrate=250_000)
    sub = hub.subscribe(is_transport_id)
    hub.start()
    r = Reassembler()
    done = []
    import time
    deadline = time.monotonic() + 2.0
    while len(done) < 1 and time.monotonic() < deadline:
        msg = sub.recv(timeout=0.05)
        if msg is not None:
            done += r.update_message(msg)
    hub.shutdown()
    assert len(done) == 1
    m = done[0]
    assert m.transport == "RTS/CTS" and m.sa == 0x21 and m.da == 0x03
    assert m.data == BAM_PAYLOAD
    assert bus.sent == [], "the observer transmitted"


def test_an_abort_discards_the_session():
    r = Reassembler()
    r.update(BAM_CM_ID, BAM_CM, 0.0)
    r.update(BAM_DT_ID, BAM_DT[0], 0.05)
    abort = bytes([0xFF, 0x01, 0xFF, 0xFF, 0xFF, 0xE1, 0xFE, 0x00])
    dropped = r.update(BAM_CM_ID, abort, 0.06)
    assert len(dropped) == 1 and dropped[0].reason == "abort"
    assert r.update(BAM_DT_ID, BAM_DT[1], 0.10) == []
    assert r.stats()["orphans"] == 1


def test_a_malformed_announcement_is_not_opened():
    r = Reassembler()
    bad = bytes([0x20, 0x13, 0x00, 0x09, 0xFF, 0xE1, 0xFE, 0x00])   # 19 bytes in 9 packets
    dropped = r.update(BAM_CM_ID, bad, 0.0)
    assert dropped and dropped[0].reason == "abort"
    assert r.stats()["pending"] == 0


def test_a_reassembled_dm1_lists_every_code():
    """Three active codes need 14 bytes: two packets over BAM. decode_dm1
    already walked any length, so the reassembled buffer decodes as is."""
    r = Reassembler()
    lamps = bytes([0x40, 0xFF])
    codes = b""
    for spn, fmi in ((100, 1), (110, 3), (190, 16)):
        codes += bytes([spn & 0xFF, (spn >> 8) & 0xFF, ((spn >> 16) & 0x07) << 5 | fmi, 0x01])
    payload = lamps + codes
    cm = bytes([0x20, len(payload), 0x00, 2, 0xFF, 0xCA, 0xFE, 0x00])
    r.update(0x1CECFF00, cm, 0.0)
    done = r.update(0x1CEBFF00, bytes([1]) + payload[:7], 0.05)
    done += r.update(0x1CEBFF00, bytes([2]) + payload[7:14], 0.10)
    (m,) = done
    assert m.pgn == 0xFECA and m.size == 14
    decoded = decode_pgn(m.pgn, m.data)
    assert [c["spn"] for c in decoded["dtcs"]] == [100, 110, 190]


# ── NMEA 2000 fast packet ────────────────────────────────────────────────────

def test_a_fast_packet_message_is_reassembled_across_seven_frames():
    r = Reassembler()
    done = feed(r, GNSS_ID, GNSS_FRAMES, 58.48, 0.003)
    assert len(done) == 1
    m = done[0]
    assert m.transport == "fast-packet" and m.protocol == "NMEA 2000"
    assert m.pgn == 129029 and m.sa == 0x23 and m.size == 43 and m.frames == 7
    assert m.data[:3] == bytes([0x88, 0x17, 0x49])


def test_reassembled_gnss_position_decodes_to_where_the_boat_was():
    r = Reassembler()
    (m,) = feed(r, GNSS_ID, GNSS_FRAMES)
    out = decode_n2k(129029, m.data)
    assert out["Date"][0] == "2021-03-25"
    assert out["Latitude"][0] == pytest.approx(42.661, abs=1e-3)
    assert out["Longitude"][0] == pytest.approx(-81.2128, abs=1e-3)
    assert out["Altitude"][0] == pytest.approx(173.5, abs=0.1)
    assert out["Satellites Used"][0] == 10
    assert 0 < out["Time"][0] < 86400
    # The single-frame rapid-position message on the same bus (tests/test_nmea2000)
    # decodes to 42.661, -81.2128: two layouts agreeing on the same spot.


def test_single_frame_decoding_of_a_fast_packet_pgn_still_returns_nothing():
    assert decode_n2k(129029, GNSS_FRAMES[0]) == {}
    assert parse_j1939_id(GNSS_ID)["single_frame"] is False


def test_fast_packets_from_two_sequences_interleave_safely():
    r = Reassembler()
    second = [bytes([b[0] + 0x20]) + b[1:] for b in GNSS_FRAMES]   # sequence id 6
    done = []
    for a, b in zip(GNSS_FRAMES, second):
        done += r.update(GNSS_ID, a, 1.0)
        done += r.update(GNSS_ID, b, 1.0)
    assert len(done) == 2 and all(m.size == 43 for m in done)
    assert done[0].data == done[1].data


def test_a_fast_packet_with_a_gap_is_dropped_and_the_next_sequence_starts_clean():
    r = Reassembler()
    r.update(GNSS_ID, GNSS_FRAMES[0], 0.0)
    r.update(GNSS_ID, GNSS_FRAMES[1], 0.01)
    dropped = r.update(GNSS_ID, GNSS_FRAMES[3], 0.02)        # frame 2 missing
    assert dropped and dropped[0].reason == "sequence"
    second = [bytes([b[0] + 0x20]) + b[1:] for b in GNSS_FRAMES]
    (m,) = feed(r, GNSS_ID, second, 1.0)
    assert m.complete and m.size == 43


def test_satellites_in_view_counts_its_records():
    header = bytes([0x87, 0x88, 0x0B])                           # SID, mode, 11 sats
    record = bytes([3, 0x7F, 0x07, 0x4E, 0x95, 0xA0, 0x0F,
                    0xFF, 0xFF, 0xFF, 0x7F, 0xF5])              # residual not available
    out = decode_n2k(129540, header + record * 11)
    assert out["Satellites in View"][0] == 11
    assert len(out["satellites"]) == 11
    assert out["satellites"][0]["prn"] == 3
    assert out["satellites"][0]["range_residual_m"] is None


# ── the feeders agree, and the summary groups ────────────────────────────────

def test_the_dataframe_feeder_matches_the_row_feeder():
    pd = pytest.importorskip("pandas")
    rows = [(0.0, BAM_CM_ID, True, 0, 8, BAM_CM)]
    rows += [(0.05 * (i + 1), BAM_DT_ID, True, 0, 8, d) for i, d in enumerate(BAM_DT)]
    rows += [(1.0 + 0.003 * i, GNSS_ID, True, 0, 8, d) for i, d in enumerate(GNSS_FRAMES)]
    rows.append((2.0, 0x100, False, 0, 8, bytes(8)))            # an 11-bit bystander
    by_rows = Reassembler()
    by_rows.update_frame_rows(rows)
    frame = pd.DataFrame([{"Timestamp": ts, "ID": f"{arb:X}".zfill(3), "Bus": bus,
                           "DLC": dlc, "Extended": ext,
                           **{f"B{k}": data[k] for k in range(8)}}
                          for ts, arb, ext, bus, dlc, data in rows])
    by_frame = reassemble_dataframe(frame)
    assert [(m.pgn, m.data) for m in by_rows.messages] == [(m.pgn, m.data) for m in by_frame]
    assert len(by_frame) == 2


def test_summary_groups_messages_by_pgn_and_source():
    r = Reassembler()
    for k in range(3):
        r.update(BAM_CM_ID, BAM_CM, k * 5.0)
        feed(r, BAM_DT_ID, BAM_DT, k * 5.0 + 0.05, 0.05)
    feed(r, GNSS_ID, GNSS_FRAMES, 20.0)
    rows = summarize(r.messages)
    assert [(row["pgn"], row["count"]) for row in rows] == [(0xFEE1, 3), (129029, 1)]
    assert rows[1]["decoded"]["Date"][0] == "2021-03-25"
    assert rows[0]["last_data_hex"].startswith("03 03 60")


def test_the_scan_names_the_transport_pgns():
    assert parse_j1939_id(BAM_CM_ID)["pgn_name"].startswith("TP.CM")
    assert parse_j1939_id(BAM_DT_ID)["transport"] is True
    assert parse_j1939_id(0x0CF00400)["transport"] is False
    assert is_transport_id(BAM_DT_ID) and is_transport_id(GNSS_ID)
    assert not is_transport_id(0x0CF00400) and not is_transport_id(0x100)


def test_nothing_in_the_module_can_transmit():
    import canlab.core.multiframe as mod
    source = open(mod.__file__).read()
    assert "gated_send" not in source and ".send(" not in source
    assert Message.__dataclass_fields__.keys() >= {"data", "reason"}
