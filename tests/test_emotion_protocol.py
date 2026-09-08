"""Tests for the legacy radar_env eMotion protocol helpers."""

from __future__ import annotations

import struct
from unittest.mock import AsyncMock

import pytest

from aiolinknlink import (
    PID_EMOTION,
    PID_EMOTION_PRO,
    PID_EMOTION_PRO_RADAR,
    TYPE_EMOTION,
    TYPE_EMOTION_PRO,
    TYPE_EMOTION_PRO_RADAR,
    TYPE_EMOTION_WIRE,
    TYPE_ULTRA,
    UltraClient,
    UltraDevice,
)
from aiolinknlink.client import (
    _auth_device_type_candidates,
    _matches_emotion,
    _matches_emotion_pro,
    _matches_ultra,
)
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


def test_emotion_pro_device_matching_and_auth_candidates() -> None:
    normal = UltraDevice(
        id="e04b41006515",
        ip="192.168.3.31",
        port=80,
        mac="e0:4b:41:00:65:15",
        pid=PID_EMOTION_PRO,
        type_id=TYPE_EMOTION_PRO,
    )
    radar = UltraDevice(
        id="e04b41006515",
        ip="192.168.3.31",
        port=80,
        mac="e0:4b:41:00:65:15",
        pid=PID_EMOTION_PRO_RADAR,
        type_id=TYPE_EMOTION_PRO_RADAR,
    )

    assert _matches_emotion_pro(normal)
    assert _matches_emotion_pro(radar)
    assert not _matches_ultra(normal)
    assert not _matches_ultra(radar)
    assert _auth_device_type_candidates(TYPE_EMOTION_PRO, PID_EMOTION_PRO) == [TYPE_EMOTION_PRO]
    assert _auth_device_type_candidates(TYPE_EMOTION_PRO_RADAR, PID_EMOTION_PRO_RADAR) == [
        TYPE_ULTRA,
        TYPE_EMOTION_PRO_RADAR,
    ]


def test_legacy_auth_payload_has_aligned_pro_layout() -> None:
    mac = bytes.fromhex("e04b41006515")
    payload = dna.build_legacy_auth_payload(mac)

    assert len(payload) == dna.LEGACY_AUTH_PAIR_INFO_SIZE
    assert payload[4:28] == (mac * 4)[:24]
    assert struct.unpack_from("<H", payload, 28)[0] == dna.TERMINAL_TYPE_IOT
    assert payload[32:48] == b"1" * 16


async def test_emotion_pro_auth_forces_legacy_blc_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    device = UltraDevice(
        id="e04b41006515",
        ip="192.168.3.31",
        port=80,
        mac="e0:4b:41:00:65:15",
        pid=PID_EMOTION_PRO,
        type_id=TYPE_EMOTION_PRO,
    )
    send = AsyncMock(return_value=b"\x00" * 4 + b"0123456789abcdef" + b"\x00" * 12)
    monkeypatch.setattr(dna, "send_encrypted", send)

    session = await UltraClient().connect(device)

    assert session.session_key == b"0123456789abcdef"
    assert send.call_args.kwargs["force_blc"] is True
