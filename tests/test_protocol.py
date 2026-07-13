"""Protocol tests."""

from __future__ import annotations

import struct
from datetime import UTC, datetime

from aiolinknlink.protocol import dna, emotion


def test_auth_payload_and_authcode() -> None:
    """Auth payload matches bridge behavior."""
    mac = bytes([0, 1, 2, 3, 4, 5])
    payload = dna.build_auth_payload(mac, 0x9CAC, host="192.168.1.8", terminal_name="test-terminal")
    assert len(payload) == dna.AUTH_PAIR_INFO_SIZE
    assert payload[4:28] == mac * 4
    assert struct.unpack_from("<H", payload, 28)[0] == dna.TERMINAL_TYPE_IOT
    assert payload[84:100] == bytes([5, 4, 3, 2, 1, 0, 0xAC, 0x9C, 0xAE, 0xAF, 0xA8, 0xA9, 0xAC, 0xAD, 0xAE, 0xAF])


def test_extract_session_key() -> None:
    """Session key extraction uses payload[0x04:0x14]."""
    payload = bytearray(0x20)
    expected = bytes(range(16))
    payload[0x04:0x14] = expected
    assert dna.extract_session_key(bytes(payload)) == expected


def test_discovery_packet() -> None:
    """Discovery packet is shaped like DNA discovery."""
    packet = dna.build_discovery_packet("192.168.3.85", 12345, datetime(2026, 7, 13, 10, 11, 12, tzinfo=UTC))
    assert len(packet) == dna.DISCOVERY_PACKET_SIZE
    assert packet[:8] == dna.MAGIC
    assert struct.unpack_from("<H", packet, 0x26)[0] == dna.MESSAGE_TYPE_DISCOVERY_REQUEST
    assert dna.verify_checksum_le(packet, 0x20)


def test_parse_short_discovery_response() -> None:
    """Parse short discovery packet."""
    packet = bytearray(dna.SHORT_DISCOVERY_SIZE)
    packet[:8] = dna.MAGIC
    struct.pack_into("<H", packet, 0x24, 0x9CAC)
    struct.pack_into("<H", packet, 0x26, dna.MESSAGE_TYPE_DISCOVERY_RESPONSE)
    packet[0x2A:0x30] = bytes([0xE0, 0x4B, 0x41, 0x01, 0x67, 0xBB])
    device = dna.parse_discovery_device_response(bytes(packet), "192.168.3.8", 80)
    assert device.mac == "e0:4b:41:01:67:bb"
    assert device.device_type == 0x9CAC


def test_subdevice_frame_round_trip() -> None:
    """SubdeviceFrame build/parse round trip."""
    frame = emotion.build_get_status_frame("did-1")
    parsed = emotion.parse_subdevice_frame(frame)
    assert parsed.command_type == emotion.CMD_GET_STATUS
    assert emotion.verify_subdevice_checksum(frame)
    payload = emotion.parse_subdevice_json_payload(parsed)
    assert payload["did"] == "did-1"


def test_local_udp_position_payload_distance() -> None:
    """Local UDP position payload derives distances."""
    payload = emotion.parse_local_udp_position_payload(b'{"detect_position":"[{\\"x\\":0.3,\\"y\\":0.4,\\"z\\":1.2}]"}')
    assert payload["distance"] == 50
    assert payload["target_distance"] == 130


def test_blc_encrypted_payload_round_trip() -> None:
    key = b"0123456789abcdef"
    wrapped = dna.build_blc_encrypted_payload(b"ping", key)
    header, payload = dna.parse_blc_encrypted_payload(wrapped, key)
    assert header.payload_checksum == dna.payload_checksum(b"ping")
    assert payload == b"ping"


def test_parse_gateway_json_state() -> None:
    payload = b'\x26\x00\x00\x00{"URL":"192.168.1.8","rssi":-42,"lb_online1":1}\x00'
    response = emotion.parse_gateway_state_response(payload)
    assert response.gateway_state is not None
    assert response.gateway_state.online is True
    assert response.gateway_state.attributes["ip"] == "192.168.1.8"
    assert response.gateway_state.attributes["wifi_rssi"] == -42
