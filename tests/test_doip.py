"""Unit tests for the pure DoIP (ISO 13400-2) encode/decode logic — no sockets."""
import struct

import pytest

from core.doip import (
    DEFAULT_SOURCE_ADDRESS,
    HEADER_SIZE,
    INVERSE_VERSION,
    PROTOCOL_VERSION,
    PT_DIAGNOSTIC_MESSAGE,
    PT_DIAGNOSTIC_MESSAGE_ACK,
    PT_ROUTING_ACTIVATION_REQUEST,
    PT_ROUTING_ACTIVATION_RESPONSE,
    PT_VEHICLE_ANNOUNCEMENT,
    ROUTING_ACTIVATION_SUCCESS,
    DoIPError,
    decode_diagnostic_ack,
    decode_diagnostic_message,
    decode_header,
    decode_routing_activation_response,
    decode_vehicle_id_response,
    encode_diagnostic_message,
    encode_header,
    encode_routing_activation_request,
    encode_vehicle_id_request,
)


# --------------------------------------------------------------------------- #
# Header round-trip
# --------------------------------------------------------------------------- #
def test_header_roundtrip():
    payload = b"\xde\xad\xbe\xef"
    msg = encode_header(PT_DIAGNOSTIC_MESSAGE, payload)

    # Raw layout: version, inverse, type, length.
    assert msg[0] == PROTOCOL_VERSION
    assert msg[1] == INVERSE_VERSION
    assert msg[2:4] == struct.pack(">H", PT_DIAGNOSTIC_MESSAGE)
    assert msg[4:8] == struct.pack(">I", len(payload))
    assert msg[HEADER_SIZE:] == payload

    hdr = decode_header(msg)
    assert hdr.protocol_version == PROTOCOL_VERSION
    assert hdr.payload_type == PT_DIAGNOSTIC_MESSAGE
    assert hdr.payload_length == len(payload)


def test_header_empty_payload_roundtrip():
    msg = encode_vehicle_id_request()
    assert len(msg) == HEADER_SIZE
    hdr = decode_header(msg)
    assert hdr.payload_type == 0x0001
    assert hdr.payload_length == 0


def test_decode_header_rejects_short_buffer():
    with pytest.raises(DoIPError):
        decode_header(b"\x02\xfd\x00")


def test_decode_header_rejects_bad_inverse_version():
    bad = bytes([0x02, 0x00]) + struct.pack(">H", 0x0001) + struct.pack(">I", 0)
    with pytest.raises(DoIPError):
        decode_header(bad)


# --------------------------------------------------------------------------- #
# Routing activation
# --------------------------------------------------------------------------- #
def test_routing_activation_request_encoding():
    msg = encode_routing_activation_request(DEFAULT_SOURCE_ADDRESS)

    hdr = decode_header(msg)
    assert hdr.payload_type == PT_ROUTING_ACTIVATION_REQUEST
    assert hdr.payload_length == 7          # SA(2) + type(1) + reserved(4)

    payload = msg[HEADER_SIZE:]
    assert payload == struct.pack(">H", DEFAULT_SOURCE_ADDRESS) + b"\x00" + b"\x00\x00\x00\x00"


def test_routing_activation_request_with_oem():
    msg = encode_routing_activation_request(0x0E80, activation_type=0x01,
                                            oem_specific=b"\x01\x02\x03\x04")
    payload = msg[HEADER_SIZE:]
    assert len(payload) == 11
    assert payload[0:2] == struct.pack(">H", 0x0E80)
    assert payload[2] == 0x01
    assert payload[7:] == b"\x01\x02\x03\x04"


def test_routing_activation_response_decode_success():
    payload = struct.pack(">HH", 0x0E00, 0x1234) + bytes([ROUTING_ACTIVATION_SUCCESS]) + b"\x00\x00\x00\x00"
    resp = decode_routing_activation_response(payload)
    assert resp.tester_address == 0x0E00
    assert resp.entity_address == 0x1234
    assert resp.response_code == ROUTING_ACTIVATION_SUCCESS


def test_routing_activation_response_too_short():
    with pytest.raises(DoIPError):
        decode_routing_activation_response(b"\x00\x00")


# --------------------------------------------------------------------------- #
# Diagnostic message wrapping / unwrapping
# --------------------------------------------------------------------------- #
def test_diagnostic_message_roundtrip():
    uds = bytes([0x22, 0xF1, 0x90])         # ReadDataByIdentifier
    msg = encode_diagnostic_message(0x0E00, 0x1234, uds)

    hdr = decode_header(msg)
    assert hdr.payload_type == PT_DIAGNOSTIC_MESSAGE
    assert hdr.payload_length == 4 + len(uds)

    dm = decode_diagnostic_message(msg[HEADER_SIZE:])
    assert dm.source_address == 0x0E00
    assert dm.target_address == 0x1234
    assert dm.uds_payload == uds


def test_diagnostic_response_parses():
    # A UDS positive response (0x62 ...) wrapped by the ECU as a 0x8001 message.
    uds_resp = bytes([0x62, 0xF1, 0x90, 0x57, 0x30, 0x4C])
    payload = struct.pack(">HH", 0x1234, 0x0E00) + uds_resp
    dm = decode_diagnostic_message(payload)
    assert dm.source_address == 0x1234      # ECU replies as source
    assert dm.target_address == 0x0E00
    assert dm.uds_payload == uds_resp


def test_diagnostic_ack_decode():
    payload = struct.pack(">HH", 0x1234, 0x0E00) + bytes([0x00])
    ack = decode_diagnostic_ack(payload)
    assert ack.source_address == 0x1234
    assert ack.target_address == 0x0E00
    assert ack.code == 0x00
    assert PT_DIAGNOSTIC_MESSAGE_ACK == 0x8002


def test_diagnostic_message_too_short():
    with pytest.raises(DoIPError):
        decode_diagnostic_message(b"\x12\x34")


# --------------------------------------------------------------------------- #
# Vehicle announcement
# --------------------------------------------------------------------------- #
def test_vehicle_id_response_decode():
    vin = b"WVWZZZ1JZXW000001"          # 17 chars
    assert len(vin) == 17
    logical = struct.pack(">H", 0x1234)
    eid = bytes([0xAA] * 6)
    gid = bytes([0xBB] * 6)
    further = bytes([0x00])
    payload = vin + logical + eid + gid + further

    info = decode_vehicle_id_response(payload)
    assert info["vin"] == vin.decode()
    assert info["logical_address"] == 0x1234
    assert info["eid"] == eid
    assert info["gid"] == gid
    assert info["further_action_required"] == 0x00
    assert PT_VEHICLE_ANNOUNCEMENT == 0x0004


def test_vehicle_id_response_too_short():
    with pytest.raises(DoIPError):
        decode_vehicle_id_response(b"\x00" * 10)
