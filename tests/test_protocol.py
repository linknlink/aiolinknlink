"""Protocol tests."""

from __future__ import annotations

import json
import math
import struct
from datetime import UTC, datetime, timedelta, timezone

import pytest

from aiolinknlink.protocol import dna, emotion


def test_auth_payload_and_authcode() -> None:
    """Auth payload matches bridge behavior."""
    mac = bytes([0, 1, 2, 3, 4, 5])
    payload = dna.build_auth_payload(mac, 0xD7AC, host="198.51.100.8", terminal_name="test-terminal")
    assert len(payload) == dna.AUTH_PAIR_INFO_SIZE
    assert payload[4:28] == mac * 4
    assert struct.unpack_from("<H", payload, 28)[0] == dna.TERMINAL_TYPE_IOT
    assert payload[84:100] == bytes([5, 4, 3, 2, 1, 0, 0xAC, 0xD7, 0xAE, 0xAF, 0xA8, 0xA9, 0xAC, 0xAD, 0xAE, 0xAF])


def test_legacy_auth_payload_uses_aligned_terminal_structure() -> None:
    """Legacy BLC pairing must not append new-protocol authentication bytes."""
    mac = bytes([5, 4, 3, 2, 1, 0])
    payload = dna.build_legacy_auth_payload(mac, terminal_name="test-terminal")

    assert len(payload) == dna.LEGACY_AUTH_PAIR_INFO_SIZE == 80
    assert payload[4:28] == mac * 4
    assert struct.unpack_from("<H", payload, 28)[0] == dna.TERMINAL_TYPE_IOT
    assert payload[48:72].rstrip(b"\x00") == b"test-terminal"
    assert payload[72:] == b"\x00" * 8


def test_extract_session_key() -> None:
    """Session key extraction uses payload[0x04:0x14]."""
    payload = bytearray(0x20)
    expected = bytes(range(16))
    payload[0x04:0x14] = expected
    assert dna.extract_session_key(bytes(payload)) == expected


def test_discovery_packet() -> None:
    """Discovery packet is shaped like DNA discovery."""
    packet = dna.build_discovery_packet("192.0.2.85", 12345, datetime(2026, 7, 13, 10, 11, 12, tzinfo=UTC))
    assert len(packet) == dna.DISCOVERY_PACKET_SIZE
    assert packet[:8] == dna.MAGIC
    assert struct.unpack_from("<i", packet, 0x08)[0] == 0
    assert struct.unpack_from("<H", packet, 0x0C)[0] == 2026
    assert packet[0x0E:0x14] == bytes([12, 11, 10, 0, 13, 7])
    assert struct.unpack_from("<H", packet, 0x26)[0] == dna.MESSAGE_TYPE_DISCOVERY_REQUEST
    assert dna.verify_checksum_le(packet, 0x20)


def test_discovery_packet_converts_device_time_to_utc() -> None:
    """Discovery sends a GMT0 dna_time_t regardless of the caller timezone."""
    local_time = datetime(2026, 7, 13, 18, 11, 12, tzinfo=timezone(timedelta(hours=8)))

    packet = dna.build_discovery_packet("192.0.2.85", 12345, local_time)

    assert packet[0x0E:0x14] == bytes([12, 11, 10, 0, 13, 7])


def test_parse_short_discovery_response() -> None:
    """Parse short discovery packet."""
    packet = bytearray(dna.SHORT_DISCOVERY_SIZE)
    packet[:8] = dna.MAGIC
    struct.pack_into("<H", packet, 0x24, 0xD7AC)
    struct.pack_into("<H", packet, 0x26, dna.MESSAGE_TYPE_DISCOVERY_RESPONSE)
    packet[0x2A:0x30] = bytes([0x02, 0x00, 0x00, 0x00, 0x01, 0x10])
    device = dna.parse_discovery_device_response(bytes(packet), "192.0.2.8", 80)
    assert device.mac == "02:00:00:00:01:10"
    assert device.device_type == 0xD7AC


def test_parse_discovery_rejects_request_packet() -> None:
    """A peer's discovery request must not be exposed as a device."""
    packet = dna.build_short_discovery_packet("192.0.2.24", 12345)

    with pytest.raises(dna.DNAError, match="unexpected discovery message type"):
        dna.parse_discovery_device_response(packet, "192.0.2.24", 12345)


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


def test_local_udp_upload_response() -> None:
    """Device response reports the inferred destination IP and requested port."""
    response = (
        struct.pack("<I", emotion.GATEWAY_CMD_SET_LOCAL_UDP_UPLOAD) + b'{"ip":1426194624,"port":25825,"timeout":60}'
    )

    config = emotion.parse_local_udp_upload_response(response)

    assert config.ip == "192.0.2.85"
    assert config.port == 25825
    assert config.timeout == 60


def test_local_udp_upload_response_with_signed_ip() -> None:
    """Firmware may serialize IPv4 values with the high bit as signed ints."""
    signed_ip = struct.unpack("<i", bytes([192, 0, 2, 160]))[0]
    response = (
        struct.pack("<I", emotion.GATEWAY_CMD_SET_LOCAL_UDP_UPLOAD)
        + f'{{"ip":{signed_ip},"port":35958,"timeout":60}}'.encode()
    )

    config = emotion.parse_local_udp_upload_response(response)

    assert config.ip == "192.0.2.160"
    assert config.port == 35958


def test_typed_local_udp_position_update() -> None:
    """Typed position updates preserve targets and derive nearest distances."""
    update = emotion.parse_local_udp_position_update(
        b'{"detect_position":"[{\\"x\\":0.3,\\"y\\":0.4,\\"z\\":1.2},'
        b'{\\"x\\":2,\\"y\\":0,\\"z\\":0},'
        b'{\\"x\\":0,\\"y\\":0,\\"z\\":0}]"}',
        "192.0.2.159",
        datetime(2026, 7, 14, tzinfo=UTC),
    )

    assert update.source_ip == "192.0.2.159"
    assert update.target_count == 2
    assert update.targets[0].x == 0.3
    assert math.isclose(update.nearest_horizontal_distance or 0, 0.5)
    assert math.isclose(update.nearest_distance or 0, 1.3)


def test_typed_position_update_discards_invalid_targets() -> None:
    """Malformed, incomplete, and non-finite targets must not affect distances."""
    positions = [
        {"x": "NaN", "y": 1, "z": 2},
        {"x": True, "y": 1, "z": 2},
        {"x": 1, "y": 2},
        {"x": "0.1", "y": 0.2, "z": 0.3},
    ]
    payload = json.dumps({"detect_position": json.dumps(positions)}).encode()

    update = emotion.parse_local_udp_position_update(payload, "192.0.2.159")

    assert update.target_count == 1
    assert update.targets[0].x == 0.1
    assert math.isfinite(update.nearest_distance or math.nan)


def test_blc_encrypted_payload_round_trip() -> None:
    key = b"0123456789abcdef"
    wrapped = dna.build_blc_encrypted_payload(b"ping", key)
    header, payload = dna.parse_blc_encrypted_payload(wrapped, key)
    assert header.payload_checksum == dna.payload_checksum(b"ping")
    assert payload == b"ping"


def test_parse_gateway_json_state() -> None:
    payload = b'\x26\x00\x00\x00{"URL":"198.51.100.8","rssi":-42,"lb_online1":1}\x00'
    response = emotion.parse_gateway_state_response(payload)
    assert response.gateway_state is not None
    assert response.gateway_state.online is True
    assert response.gateway_state.attributes["ip"] == "198.51.100.8"
    assert response.gateway_state.attributes["wifi_rssi"] == -42
