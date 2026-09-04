"""Tests for the legacy radar_env eMotion protocol helpers."""

from __future__ import annotations

import struct

import pytest

from aiolinknlink import PID_EMOTION, TYPE_EMOTION, TYPE_EMOTION_WIRE, UltraDevice
from aiolinknlink.client import _auth_device_type_candidates, _matches_emotion
from aiolinknlink.protocol import dna, emotion


def test_keyvalue_helpers_round_trip() -> None:
    assert emotion.build_keyvalue_request() == b""
    assert emotion.build_keyvalue_request({"delaytime1": 30}) == b'{"delaytime1":30}'
    assert emotion.parse_keyvalue_status(b'{"pir_detected":1,"delaytime1":60}\x00') == {
        "pir_detected": 1,
        "delaytime1": 60,
    }


def test_uart_frame_round_trip() -> None:
    frame = dna.build_uart_frame(dna.UART_GET_STATUS, b'{"x":1}')
    parsed = dna.parse_uart_frame(frame, dna.UART_GET_STATUS)
    assert parsed.payload == b'{"x":1}'


def test_legacy_terminal_add_packet_layout() -> None:
    mac = bytes.fromhex("e04b41006515")
    header = dna.NetworkHeader(
        device_type=TYPE_EMOTION_WIRE,
        message_type=dna.MESSAGE_TYPE_TERMINAL_ADD,
        sequence=1,
        mac=mac,
    )
    packet = dna.build_legacy_terminal_add_packet(header, mac)
    assert len(packet) == dna.LEGACY_NETWORK_HEADER_SIZE + dna.AES_HEADER_SIZE + 80
    parsed_header, body = dna.parse_legacy_packet(packet)
    assert parsed_header.message_type == dna.MESSAGE_TYPE_TERMINAL_ADD
    plain = dna.decrypt_aes_cbc_no_padding(body[8:], dna.INITIAL_KEY, dna.INITIAL_IV)
    assert plain[4:10] == mac
    assert struct.unpack_from("<H", plain, 28)[0] == dna.TERMINAL_TYPE_IOT


def test_emotion_device_matching_and_auth_candidates() -> None:
    device = UltraDevice(
        id="e04b41006515",
        ip="192.168.3.31",
        port=80,
        mac="e0:4b:41:00:65:15",
        pid=PID_EMOTION,
        type_id=TYPE_EMOTION,
    )
    assert _matches_emotion(device)
    assert _auth_device_type_candidates(TYPE_EMOTION, PID_EMOTION) == [
        TYPE_EMOTION,
        TYPE_EMOTION_WIRE,
    ]


def test_keyvalue_status_rejects_invalid_payload() -> None:
    with pytest.raises(emotion.EmotionError, match="empty"):
        emotion.parse_keyvalue_status(b"")
    with pytest.raises(emotion.EmotionError, match="parse"):
        emotion.parse_keyvalue_status(b"not-json")
