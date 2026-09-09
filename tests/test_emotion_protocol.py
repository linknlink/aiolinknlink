"""Tests for the legacy radar_env eMotion protocol helpers."""

from __future__ import annotations

import json
import struct
from unittest.mock import AsyncMock

import pytest

from aiolinknlink import (
    PID_EMOTION,
    PID_EMOTION_PRO,
    PID_EMOTION_PRO_RADAR,
    PID_EMOTION_MAX1,
    PID_EMOTION_MAX2,
    TYPE_EMOTION,
    TYPE_EMOTION_PRO,
    TYPE_EMOTION_PRO_RADAR,
    TYPE_EMOTION_MAX1,
    TYPE_EMOTION_MAX2,
    TYPE_ULTRA,
    TYPE_LEGACY_SHTXX,
    TYPE_PRO_RADAR_24G,
    TYPE_EMOTION_WIRE,
    UltraClient,
    UltraDevice,
    UltraProtocolError,
    derive_peripheral_did,
    derive_radar_did,
)
from aiolinknlink.client import (
    TYPE_LEGACY_OPT3004,
    TYPE_ULTRA2_RADAR,
    _auth_device_type_candidates,
    _matches_emotion,
    _matches_emotion_pro,
    _matches_ultra,
)
from aiolinknlink.models import UltraSession
from aiolinknlink.protocol import dna, emotion, keyvalue


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
    assert _matches_ultra(radar)
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


def test_keyvalue_frame_round_trip() -> None:
    frame = keyvalue.build_set_status_frame({"delaytime": 60, "enabled": True})

    command, payload = keyvalue.parse_frame(frame)

    assert command == keyvalue.CMD_SET_STATUS
    assert payload == b'{"delaytime":60,"enabled":true}'


def test_keyvalue_status_response_round_trip() -> None:
    response = keyvalue.build_frame(
        keyvalue.CMD_STATUS_RESPONSE,
        b'{"tempsensor":235,"humsensor":48,"pir_detected":1}\x00',
    )

    assert keyvalue.parse_status_response(response) == {
        "tempsensor": 235,
        "humsensor": 48,
        "pir_detected": 1,
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda frame: frame[:4] + b"\x00" + frame[5:],
        lambda frame: frame[:8] + b"\xff\xff" + frame[10:],
        lambda frame: frame[:-1],
    ],
)
def test_keyvalue_rejects_corrupt_frames(mutate: object) -> None:
    frame = keyvalue.build_get_status_frame()

    with pytest.raises(keyvalue.KeyValueError):
        keyvalue.parse_frame(mutate(frame))  # type: ignore[operator]


def test_keyvalue_rejects_non_object_status() -> None:
    response = keyvalue.build_frame(keyvalue.CMD_STATUS_RESPONSE, b"[]")

    with pytest.raises(keyvalue.KeyValueError, match="not an object"):
        keyvalue.parse_status_response(response)


async def test_emotion_pro_reads_normalized_environment_state() -> None:
    device = UltraDevice(
        id="e04b41006515",
        ip="192.168.3.31",
        port=80,
        mac="e0:4b:41:00:65:15",
        pid=PID_EMOTION_PRO,
        type_id=TYPE_EMOTION_PRO,
    )
    client = UltraClient()
    client.send_command = AsyncMock(
        return_value=keyvalue.build_frame(
            keyvalue.CMD_STATUS_RESPONSE,
            b'{"tempsensor":235,"humsensor":48,"pir_detected":1,"delaytime":5}',
        )
    )

    state = await client.get_environment_state(UltraSession(device=device, session_key=b"0123456789abcdef"))

    assert state.values == {
        "temperature": 23.5,
        "humidity": 48,
        "occupancy": True,
        "absence_delay": 300,
    }
    assert state.available_fields == frozenset(state.values)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tempsensor", 1301),
        ("humsensor", 101),
        ("pir_detected", 2),
        ("delaytime", 65536),
    ],
)
async def test_emotion_pro_rejects_invalid_environment_state(field: str, value: object) -> None:
    device = UltraDevice(
        id="e04b41006515",
        ip="192.168.3.31",
        port=80,
        mac="e0:4b:41:00:65:15",
        pid=PID_EMOTION_PRO,
        type_id=TYPE_EMOTION_PRO,
    )
    client = UltraClient()
    client.send_command = AsyncMock(
        return_value=keyvalue.build_frame(
            keyvalue.CMD_STATUS_RESPONSE,
            json.dumps({field: value}).encode(),
        )
    )

    with pytest.raises(UltraProtocolError, match=field):
        await client.get_environment_state(UltraSession(device=device, session_key=b"0123456789abcdef"))


async def test_emotion_pro_radar_reads_virtual_peripherals() -> None:
    device = UltraDevice(
        id="e04b41006515",
        ip="192.168.3.31",
        port=80,
        mac="e0:4b:41:00:65:15",
        pid=PID_EMOTION_PRO_RADAR,
        type_id=TYPE_EMOTION_PRO_RADAR,
    )
    radar_did = derive_peripheral_did(device.mac, TYPE_PRO_RADAR_24G)
    climate_did = derive_peripheral_did(device.mac, TYPE_LEGACY_SHTXX)
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            emotion.build_subdevice_frame(
                emotion.CMD_SUBDEVICE_LIST_RESPONSE,
                {"status": 0, "list": [{"did": radar_did, "offline": 0}, {"did": climate_did, "offline": 0}]},
            ),
            emotion.build_subdevice_frame(
                emotion.CMD_STATUS_RESPONSE,
                {"did": radar_did, "status": 0, "pir_detected": 1, "delaytime": 120},
            ),
            emotion.build_subdevice_frame(
                emotion.CMD_STATUS_RESPONSE,
                {"did": climate_did, "status": 0, "envtemp": 2350, "envhumid": 4850},
            ),
        ]
    )

    state = await client.get_environment_state(UltraSession(device=device, session_key=b"0123456789abcdef"))

    assert state.values == {
        "occupancy": True,
        "absence_delay": 120,
        "temperature": 23.5,
        "humidity": 48.5,
    }


async def test_shared_ultra_type_probes_first_generation_peripherals() -> None:
    device = UltraDevice(
        id="e04b41006515",
        ip="192.168.3.31",
        port=80,
        mac="e0:4b:41:00:65:15",
        pid="0000000000000000000000009cac0000",
        type_id=TYPE_ULTRA,
    )
    radar_did = derive_peripheral_did(device.mac, TYPE_ULTRA2_RADAR)
    light_did = derive_peripheral_did(device.mac, TYPE_LEGACY_OPT3004)
    climate_did = derive_peripheral_did(device.mac, TYPE_LEGACY_SHTXX)
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            emotion.build_subdevice_frame(
                emotion.CMD_SUBDEVICE_LIST_RESPONSE,
                {"status": 0, "list": [{"did": radar_did}, {"did": light_did}, {"did": climate_did}]},
            ),
            emotion.build_subdevice_frame(
                emotion.CMD_STATUS_RESPONSE,
                {"did": radar_did, "status": 0, "pir_detected": 1, "sf_opcount": 2, "area1": 1},
            ),
            emotion.build_subdevice_frame(
                emotion.CMD_STATUS_RESPONSE,
                {"did": light_did, "status": 0, "envlux": 150},
            ),
            emotion.build_subdevice_frame(
                emotion.CMD_STATUS_RESPONSE,
                {"did": climate_did, "status": 0, "envtemp": 7700, "envhumid": 5000, "tempunit": 2},
            ),
        ]
    )

    session = UltraSession(device=device, session_key=b"0123456789abcdef")
    state = await client.get_environment_state(session)

    assert state.values == {
        "occupancy": True,
        "target_count": 2,
        "zone_1_presence": True,
        "illuminance": 150.0,
        "temperature": 25.0,
        "humidity": 50.0,
    }
    assert session.ultra1_probe is True
    assert session.peripheral_dids[TYPE_ULTRA2_RADAR] == radar_did
    assert derive_radar_did(device.mac) == radar_did


async def test_emotion_max1_reads_keyvalue_environment() -> None:
    device = UltraDevice(
        id="e04b41006515",
        ip="192.168.3.31",
        port=80,
        mac="e0:4b:41:00:65:15",
        pid=PID_EMOTION_MAX1,
        type_id=TYPE_EMOTION_MAX1,
    )
    client = UltraClient()
    client.send_command = AsyncMock(
        return_value=keyvalue.build_frame(
            keyvalue.CMD_STATUS_RESPONSE,
            b'{"pir_detected":1,"sf_opcount":2,"area1":1,"envtemp":2350,"envhumid":4800,"envlux":120}',
        )
    )

    state = await client.get_environment_state(UltraSession(device=device, session_key=b"0123456789abcdef"))

    assert state.values == {
        "occupancy": True,
        "target_count": 2,
        "zone_1_presence": True,
        "temperature": 23.5,
        "humidity": 48.0,
        "illuminance": 120.0,
    }


async def test_emotion_max2_reads_virtual_peripherals() -> None:
    device = UltraDevice(
        id="e04b41006515",
        ip="192.168.3.31",
        port=80,
        mac="e0:4b:41:00:65:15",
        pid=PID_EMOTION_MAX2,
        type_id=TYPE_EMOTION_MAX2,
    )
    radar_did = derive_radar_did(device.mac)
    light_did = derive_peripheral_did(device.mac, TYPE_LEGACY_OPT3004)
    climate_did = derive_peripheral_did(device.mac, TYPE_LEGACY_SHTXX)
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            emotion.build_subdevice_frame(
                emotion.CMD_STATUS_RESPONSE,
                {"did": radar_did, "status": 0, "pir_detected": 1, "sf_opcount": 1, "area2": 1},
            ),
            emotion.build_subdevice_frame(
                emotion.CMD_STATUS_RESPONSE,
                {"did": light_did, "status": 0, "envlux": 96},
            ),
            emotion.build_subdevice_frame(
                emotion.CMD_STATUS_RESPONSE,
                {"did": climate_did, "status": 0, "envtemp": 2575, "envhumid": 5330},
            ),
        ]
    )

    state = await client.get_environment_state(UltraSession(device=device, session_key=b"0123456789abcdef"))

    assert state.values == {
        "occupancy": True,
        "target_count": 1,
        "zone_2_presence": True,
        "illuminance": 96.0,
        "temperature": 25.75,
        "humidity": 53.3,
    }
