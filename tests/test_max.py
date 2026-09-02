"""Tests for the eMotion Max local protocol variants."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aiolinknlink import (
    TYPE_EMOTION_MAX1,
    TYPE_EMOTION_MAX2,
    TYPE_EMOTION_MAX3,
    DeviceCapability,
    DeviceModel,
    UltraClient,
    UltraDevice,
    UltraPositionSubscription,
    UltraProtocolError,
    UltraRadarZRange,
    derive_peripheral_did,
    derive_radar_did,
)
from aiolinknlink.models import UltraSession
from aiolinknlink.protocol import dna, emotion, keyvalue

MAC = "02:00:00:00:01:10"
SESSION_KEY = b"0123456789abcdef"
RADAR_TYPE = 0xACDB
ILLUMINANCE_TYPE = 0xACD8
CLIMATE_TYPE = 0xACDC


def _device(device_type: int) -> UltraDevice:
    return UltraDevice(
        id=MAC.replace(":", ""),
        ip="198.51.100.8",
        port=80,
        mac=MAC,
        type_id=device_type,
    )


def _session(device_type: int) -> UltraSession:
    return UltraSession(device=_device(device_type), session_key=SESSION_KEY, auth_mac=MAC)


def _keyvalue_response(values: dict[str, object]) -> bytes:
    return keyvalue.build_frame(
        keyvalue.CMD_STATUS_RESPONSE,
        json.dumps(values, separators=(",", ":")).encode(),
    )


def _subdevice_response(device_type: int, values: dict[str, object]) -> bytes:
    did = derive_peripheral_did(MAC, device_type)
    return emotion.build_subdevice_frame(
        emotion.CMD_STATUS_RESPONSE,
        {"did": did, "status": 0, **values},
    )


@pytest.mark.parametrize(
    ("device_type", "model", "display_name"),
    [
        (TYPE_EMOTION_MAX1, DeviceModel.EMOTION_MAX1, "eMotion Max"),
        (TYPE_EMOTION_MAX2, DeviceModel.EMOTION_MAX2, "eMotion Max 2"),
        (TYPE_EMOTION_MAX3, DeviceModel.EMOTION_MAX3, "eMotion Max 3"),
    ],
)
async def test_discover_max_generations(
    monkeypatch: pytest.MonkeyPatch,
    device_type: int,
    model: DeviceModel,
    display_name: str,
) -> None:
    raw = dna.DiscoveredDevice(
        id=MAC,
        ip="198.51.100.8",
        port=80,
        mac=MAC,
        device_type=device_type,
    )
    monkeypatch.setattr(
        "aiolinknlink.client._discover_dna_devices",
        AsyncMock(return_value=[raw]),
    )

    devices = await UltraClient().discover()

    assert len(devices) == 1
    assert devices[0].profile is not None
    assert devices[0].profile.model is model
    assert devices[0].model == display_name
    assert DeviceCapability.TARGET_SPEED not in devices[0].capabilities


async def test_connect_uses_max_identity_and_wire_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    send = AsyncMock(return_value=b"\x00" * 4 + SESSION_KEY + b"\x00" * 12)
    monkeypatch.setattr(dna, "send_encrypted", send)
    device = _device(TYPE_EMOTION_MAX2)

    session = await UltraClient().connect(device)

    assert session.auth_device_type == TYPE_EMOTION_MAX2
    assert device.name == "eMotion Max 2"
    assert device.model == "eMotion Max 2"
    assert send.await_count == 1
    assert send.call_args.args[2].device_type == TYPE_EMOTION_MAX2


async def test_max1_environment_uses_keyvalue_status() -> None:
    client = UltraClient()
    client.send_command = AsyncMock(
        return_value=_keyvalue_response(
            {
                "pir_detected": 1,
                "sf_opcount": 2,
                "area1": 1,
                "area2": 0,
                "area3": 1,
                "area4": 0,
                "envtemp": 2345,
                "envhumid": 4812,
                "envlux": 321,
            }
        )
    )
    session = _session(TYPE_EMOTION_MAX1)

    state = await client.get_environment_state(session)

    assert state.values == {
        "occupancy": True,
        "target_count": 2,
        "zone_1_presence": True,
        "zone_2_presence": False,
        "zone_3_presence": True,
        "zone_4_presence": False,
        "illuminance": 321.0,
        "temperature": 23.45,
        "humidity": 48.12,
    }
    command, payload = keyvalue.parse_frame(client.send_command.call_args.args[1])
    assert command == keyvalue.CMD_GET_STATUS
    assert payload == b""
    assert state.available_fields == frozenset(state.values)
    assert session.last_seen is not None


@pytest.mark.parametrize("device_type", [TYPE_EMOTION_MAX2, TYPE_EMOTION_MAX3])
async def test_max_subdevice_environment(device_type: int) -> None:
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            _subdevice_response(
                RADAR_TYPE,
                {
                    "pir_detected": 1,
                    "sf_opcount": 1,
                    "area1": 0,
                    "area2": 1,
                    "area3": 0,
                    "area4": 0,
                },
            ),
            _subdevice_response(ILLUMINANCE_TYPE, {"envlux": 96}),
            _subdevice_response(CLIMATE_TYPE, {"envtemp": 2575, "envhumid": 5330}),
        ]
    )

    state = await client.get_environment_state(_session(device_type))

    assert state.values == {
        "occupancy": True,
        "target_count": 1,
        "zone_1_presence": False,
        "zone_2_presence": True,
        "zone_3_presence": False,
        "zone_4_presence": False,
        "illuminance": 96.0,
        "temperature": 25.75,
        "humidity": 53.3,
    }
    requested_dids = []
    for call in client.send_command.await_args_list:
        frame = emotion.parse_subdevice_frame(call.args[1])
        requested_dids.append(emotion.parse_subdevice_json_payload(frame)["did"])
    assert requested_dids == [
        derive_peripheral_did(MAC, RADAR_TYPE),
        derive_peripheral_did(MAC, ILLUMINANCE_TYPE),
        derive_peripheral_did(MAC, CLIMATE_TYPE),
    ]


async def test_max_subdevice_optional_sensor_can_be_missing() -> None:
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            _subdevice_response(RADAR_TYPE, {"pir_detected": 0, "sf_opcount": 0}),
            b"invalid optional illuminance response",
            b"invalid optional climate response",
        ]
    )

    state = await client.get_environment_state(_session(TYPE_EMOTION_MAX2))

    assert state.values == {"occupancy": False, "target_count": 0}
    assert "temperature" not in state.available_fields
    assert "humidity" not in state.available_fields
    assert "illuminance" not in state.available_fields


async def test_max_subdevice_required_radar_failure_is_reported() -> None:
    client = UltraClient()
    client.send_command = AsyncMock(
        return_value=emotion.build_subdevice_frame(
            emotion.CMD_STATUS_RESPONSE,
            {"did": derive_radar_did(MAC), "status": 1},
        )
    )

    with pytest.raises(UltraProtocolError, match="subdevice status read failed"):
        await client.get_environment_state(_session(TYPE_EMOTION_MAX3))


async def test_max1_radar_status_and_default_delay_setter() -> None:
    status_values = {
        "level_of_sensitivity": 2,
        "triger_speed": 1,
        "install_mode": 0,
        "height": 250,
        "install_direction": 1,
        "z_range": '{"min":-2.0,"max":2.0}',
        "delaytime1": 75,
        "duration1": 60,
        "duration2": 90,
        "duration3": 120,
        "duration4": 180,
    }
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            _keyvalue_response(status_values),
            _keyvalue_response({"delaytime1": 90}),
            _keyvalue_response({**status_values, "delaytime1": 90}),
        ]
    )
    session = _session(TYPE_EMOTION_MAX1)

    status = await client.get_radar_status(session)
    updated = await client.set_radar_default_absence_delay(session, 90)

    assert status.sensitivity == 2
    assert status.trigger_speed == 1
    assert status.z_range == UltraRadarZRange(minimum=-2.0, maximum=2.0)
    assert status.default_absence_delay == 75
    assert status.zone_absence_delays == (60, 90, 120, 180)
    assert updated.default_absence_delay == 90
    command, payload = keyvalue.parse_frame(client.send_command.await_args_list[1].args[1])
    assert command == keyvalue.CMD_SET_STATUS
    assert json.loads(payload) == {"delaytime1": 90}


async def test_max1_z_range_setter_uses_json_string() -> None:
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            _keyvalue_response({"z_range": '{"min":-1.5,"max":2.5}'}),
            _keyvalue_response(
                {
                    "level_of_sensitivity": 1,
                    "z_range": '{"min":-1.5,"max":2.5}',
                }
            ),
        ]
    )

    status = await client.set_radar_z_range(_session(TYPE_EMOTION_MAX1), -1.5, 2.5)

    assert status.z_range == UltraRadarZRange(minimum=-1.5, maximum=2.5)
    _, payload = keyvalue.parse_frame(client.send_command.await_args_list[0].args[1])
    assert json.loads(payload) == {"z_range": '{"min":-1.5,"max":2.5}'}


async def test_max1_local_udp_configuration() -> None:
    client = UltraClient()
    client.send_command = AsyncMock(return_value=_keyvalue_response({"port": 45678, "timeout": 60}))

    config = await client.subscribe_local_udp_push(
        _session(TYPE_EMOTION_MAX1),
        45678,
        60,
    )

    assert config.ip == "0.0.0.0"
    assert config.port == 45678
    assert config.timeout == 60
    command, payload = keyvalue.parse_frame(client.send_command.call_args.args[1])
    assert command == keyvalue.CMD_SET_STATUS
    assert json.loads(payload) == {"port": 45678, "timeout": 60}


async def test_max_keyvalue_errors_are_protocol_errors() -> None:
    client = UltraClient()
    client.send_command = AsyncMock(return_value=b"invalid")

    with pytest.raises(UltraProtocolError, match="too short"):
        await client.get_environment_state(_session(TYPE_EMOTION_MAX1))
    with pytest.raises(UltraProtocolError, match="too short"):
        await client.subscribe_local_udp_push(_session(TYPE_EMOTION_MAX1), 1234, 60)


async def test_max_position_subscription_keeps_device_mac() -> None:
    session = _session(TYPE_EMOTION_MAX3)

    subscription = UltraPositionSubscription(UltraClient(), session)

    assert subscription._protocol_mac == MAC


def test_peripheral_did_validation() -> None:
    assert derive_radar_did(MAC) == "020000000110dbac00000000dbac0001"
    with pytest.raises(ValueError, match="invalid LinknLink LAN MAC"):
        derive_peripheral_did("invalid", RADAR_TYPE)
    with pytest.raises(ValueError, match="24 bits"):
        derive_peripheral_did(MAC, 0x1000000)
