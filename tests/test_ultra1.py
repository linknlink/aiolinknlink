"""Tests for eMotion Ultra first-generation local support."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from aiolinknlink import (
    DISPLAY_MODEL_ULTRA1,
    TYPE_ULTRA1,
    TYPE_ULTRA2,
    DeviceCapability,
    UltraAuthError,
    UltraClient,
    UltraDevice,
    UltraError,
    UltraProtocolError,
    UltraSession,
    derive_peripheral_did,
    derive_radar_did,
)
from aiolinknlink.client import (
    TYPE_LEGACY_OPT3004,
    TYPE_LEGACY_SHTXX,
    TYPE_ULTRA2_RADAR,
    _command_device_type_candidates,
    _command_message_type_candidates,
)
from aiolinknlink.protocol import dna, emotion

MAC = "02:00:00:00:01:10"
DEVICE = UltraDevice(
    id="020000000110",
    ip="192.0.2.8",
    port=80,
    mac=MAC,
    type_id=TYPE_ULTRA1,
    name=DISPLAY_MODEL_ULTRA1,
    model=DISPLAY_MODEL_ULTRA1,
)


def session() -> UltraSession:
    """Return an isolated authenticated Ultra1 session."""
    return UltraSession(device=DEVICE, session_key=b"0123456789abcdef", auth_mac=MAC)


def response(did: str, **values: object) -> bytes:
    """Build a sanitized first-generation subdevice response."""
    return emotion.build_subdevice_frame(
        emotion.CMD_STATUS_RESPONSE,
        {"did": did, "status": 0, **values},
    )


def list_response(*dids: str) -> bytes:
    """Build a sanitized persisted-peripheral list response."""
    return emotion.build_subdevice_frame(
        emotion.CMD_SUBDEVICE_LIST_RESPONSE,
        {"list": [{"did": did} for did in dids], "status": 0, "total": len(dids), "index": 0},
    )


def persisted_did(peripheral_type: int, suffix: int) -> str:
    """Build a valid peripheral DID with a non-derived persisted tail."""
    return (bytes.fromhex(MAC.replace(":", "")) + peripheral_type.to_bytes(4, "little") + bytes([suffix]) * 6).hex()


async def test_ultra1_discovery_and_authentication(monkeypatch: pytest.MonkeyPatch) -> None:
    discovered = dna.DiscoveredDevice(
        id=MAC,
        ip=DEVICE.ip,
        port=80,
        mac=MAC,
        device_type=TYPE_ULTRA1,
        name="cm_ha",
        status_flags=3,
        is_new=True,
        is_locked=False,
    )
    monkeypatch.setattr("aiolinknlink.client._discover_dna_devices", AsyncMock(return_value=[discovered]))
    client = UltraClient()

    devices = await client.discover()
    assert len(devices) == 1
    assert devices[0].model == DISPLAY_MODEL_ULTRA1
    assert devices[0].discovery_status == 3
    assert devices[0].is_new is True
    assert devices[0].is_locked is False
    assert devices[0].raw["is_locked"] is False
    assert DeviceCapability.OCCUPANCY in devices[0].capabilities

    session_key = b"fedcba9876543210"
    send = AsyncMock(return_value=b"\x00" * 4 + session_key + b"\x00" * 12)
    monkeypatch.setattr(dna, "send_encrypted", send)
    session = await client.connect(devices[0])
    assert session.session_key == session_key
    assert session.command_device_type == 0xE3AC
    assert session.command_message_type == dna.MESSAGE_TYPE_COMMAND
    assert send.call_args.args[2].device_type == TYPE_ULTRA1
    assert session.auth_mac == "10:01:00:00:00:02"
    assert send.call_args.args[2].mac == bytes.fromhex("100100000002")
    assert len(send.call_args.args[3]) == dna.LEGACY_AUTH_PAIR_INFO_SIZE


async def test_ultra1_locked_device_requires_provisioning_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """A locked legacy device must not be sent a pairing request that it will reject."""
    send = AsyncMock()
    monkeypatch.setattr(dna, "send_encrypted", send)

    with pytest.raises(UltraAuthError, match="local control key"):
        await UltraClient().connect(replace(DEVICE, is_locked=True))

    send.assert_not_awaited()


async def test_ultra1_accepts_provisioning_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The BLE provisioning token is the persistent local control key."""
    send = AsyncMock()
    monkeypatch.setattr(dna, "send_encrypted", send)

    current = await UltraClient().connect(
        replace(DEVICE, is_locked=True),
        local_key="30313233343536373839616263646566",
    )

    assert current.session_key == b"0123456789abcdef"
    assert current.auth_status == "provided"
    assert current.auth_mac == "10:01:00:00:00:02"
    assert current.command_device_type == 0xE3AC
    send.assert_not_awaited()


@pytest.mark.parametrize("local_key", [b"short", "not-hex", "00"])
async def test_ultra1_rejects_invalid_provisioning_key(local_key: bytes | str) -> None:
    with pytest.raises(UltraAuthError, match="16 bytes"):
        await UltraClient().connect(DEVICE, local_key=local_key)


async def test_ultra1_reauthentication_restores_validated_command_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = UltraClient()
    current_session = session()
    current_session.command_device_type = TYPE_ULTRA1
    current_session.command_message_type = 0x03E9
    refreshed = session()
    refreshed.command_device_type = 0xE3AC
    refreshed.command_message_type = dna.MESSAGE_TYPE_COMMAND
    refreshed.auth_status = "ok"
    monkeypatch.setattr(client, "connect", AsyncMock(return_value=refreshed))

    await client.reauthenticate(current_session)

    assert current_session.command_device_type == 0xE3AC
    assert current_session.command_message_type == dna.MESSAGE_TYPE_COMMAND


async def test_ultra1_environment_state() -> None:
    client = UltraClient()
    current_session = session()
    radar_did = persisted_did(TYPE_ULTRA2_RADAR, 1)
    light_did = persisted_did(TYPE_LEGACY_OPT3004, 2)
    climate_did = persisted_did(TYPE_LEGACY_SHTXX, 3)
    client.send_command = AsyncMock(
        side_effect=[
            list_response(radar_did, light_did, climate_did),
            response(
                radar_did,
                pir_detected=1,
                sf_opcount=2,
                area1=1,
                area2=0,
                area3=0,
                area4=1,
                level_of_sensitivity=2,
                delaytime=60,
                duration1=60,
                duration2=90,
                duration3=120,
                duration4=180,
            ),
            response(light_did, envlux=325),
            response(climate_did, envtemp=2350, envhumid=4825, tempunit=1),
        ]
    )

    state = await client.get_environment_state(current_session)

    assert state.values == {
        "occupancy": True,
        "target_count": 2,
        "zone_1_presence": True,
        "zone_2_presence": False,
        "zone_3_presence": False,
        "zone_4_presence": True,
        "illuminance": 325.0,
        "temperature": 23.5,
        "humidity": 48.25,
    }
    assert state.available_fields == frozenset(state.values)
    assert current_session.last_seen is not None
    assert current_session.peripheral_dids[TYPE_ULTRA2_RADAR] == radar_did


async def test_ultra1_runtime_capabilities_include_listed_sensors() -> None:
    client = UltraClient()
    radar_did = persisted_did(TYPE_ULTRA2_RADAR, 1)
    light_did = persisted_did(TYPE_LEGACY_OPT3004, 2)
    climate_did = persisted_did(TYPE_LEGACY_SHTXX, 3)
    client.send_command = AsyncMock(return_value=list_response(radar_did, light_did, climate_did))

    capabilities = await client.get_runtime_capabilities(session())

    assert capabilities == frozenset(
        {
            DeviceCapability.ENVIRONMENT,
            DeviceCapability.TEMPERATURE,
            DeviceCapability.HUMIDITY,
            DeviceCapability.ILLUMINANCE,
            DeviceCapability.OCCUPANCY,
            DeviceCapability.TARGET_COUNT,
            DeviceCapability.ZONES,
            DeviceCapability.RADAR_CONFIGURATION,
            DeviceCapability.ABSENCE_DELAY,
        }
    )


async def test_non_ultra1_runtime_capabilities_remain_model_capabilities() -> None:
    client = UltraClient()
    device = UltraDevice(
        id="020000000120",
        ip="192.0.2.9",
        port=80,
        mac="02:00:00:00:01:20",
        type_id=TYPE_ULTRA2,
    )

    assert await client.get_runtime_capabilities(UltraSession(device=device)) == device.capabilities
    assert (
        await client.get_runtime_capabilities(UltraSession(device=UltraDevice("unknown", "192.0.2.10", 80)))
        == frozenset()
    )


async def test_ultra1_optional_sensor_cable_can_be_absent() -> None:
    client = UltraClient()
    current_session = session()
    radar_did = derive_peripheral_did(MAC, TYPE_ULTRA2_RADAR)
    light_did = derive_peripheral_did(MAC, TYPE_LEGACY_OPT3004)
    climate_did = derive_peripheral_did(MAC, TYPE_LEGACY_SHTXX)
    client.send_command = AsyncMock(
        side_effect=[
            list_response(radar_did, light_did, climate_did),
            response(radar_did, pir_detected=0, sf_opcount=0),
            emotion.build_subdevice_frame(
                emotion.CMD_STATUS_RESPONSE,
                {"did": light_did, "status": -1},
            ),
            emotion.build_subdevice_frame(
                emotion.CMD_STATUS_RESPONSE,
                {"did": climate_did, "status": -1},
            ),
        ]
    )

    state = await client.get_environment_state(current_session)

    assert state.values == {"occupancy": False, "target_count": 0}
    assert "temperature" not in state.available_fields
    assert "humidity" not in state.available_fields


async def test_ultra1_fahrenheit_is_normalized_to_celsius() -> None:
    client = UltraClient()
    current_session = session()
    radar_did = derive_peripheral_did(MAC, TYPE_ULTRA2_RADAR)
    light_did = derive_peripheral_did(MAC, TYPE_LEGACY_OPT3004)
    climate_did = derive_peripheral_did(MAC, TYPE_LEGACY_SHTXX)
    client.send_command = AsyncMock(
        side_effect=[
            list_response(radar_did, light_did, climate_did),
            response(radar_did, pir_detected=0),
            response(light_did, envlux=0),
            response(
                climate_did,
                envtemp=7700,
                envhumid=5000,
                tempunit=2,
            ),
        ]
    )

    state = await client.get_environment_state(current_session)
    assert state.values["temperature"] == 25.0


async def test_ultra1_empty_environment_response_is_rejected() -> None:
    client = UltraClient()
    client.send_command = AsyncMock(
        return_value=emotion.build_subdevice_frame(
            emotion.CMD_STATUS_RESPONSE,
            {"did": "wrong", "status": 0},
        )
    )
    with pytest.raises(UltraProtocolError, match="did not return any"):
        await client.get_environment_state(session())


async def test_ultra1_environment_survives_missing_radar_subdevice() -> None:
    client = UltraClient()
    current_session = session()
    light_did = persisted_did(TYPE_LEGACY_OPT3004, 6)
    climate_did = persisted_did(TYPE_LEGACY_SHTXX, 7)
    radar_did = derive_radar_did(MAC)
    client.send_command = AsyncMock(
        side_effect=[
            list_response(light_did, climate_did),
            emotion.build_subdevice_frame(emotion.CMD_STATUS_RESPONSE, {"did": radar_did, "status": -3}),
            response(light_did, envlux=210),
            response(climate_did, envtemp=2460, envhumid=5110, tempunit=1),
        ]
    )

    state = await client.get_environment_state(current_session)

    assert state.values == {"illuminance": 210.0, "temperature": 24.6, "humidity": 51.1}


async def test_ultra1_peripheral_list_filters_and_caches() -> None:
    client = UltraClient()
    current_session = session()
    other_model_radar_did = persisted_did(0xACD9, 4)
    radar_60g_did = persisted_did(TYPE_ULTRA2_RADAR, 8)
    offline_climate_did = persisted_did(TYPE_LEGACY_SHTXX, 7)
    unknown_did = persisted_did(0x1234, 5)
    client.send_command = AsyncMock(
        return_value=emotion.build_subdevice_frame(
            emotion.CMD_SUBDEVICE_LIST_RESPONSE,
            {
                "status": 0,
                "list": [
                    None,
                    {},
                    {"did": 1},
                    {"did": "bad"},
                    {"did": "00"},
                    {"did": unknown_did},
                    {"did": other_model_radar_did},
                    {"did": radar_60g_did},
                    {"did": offline_climate_did, "offline": 2},
                ],
            },
        )
    )

    expected = {TYPE_ULTRA2_RADAR: radar_60g_did}
    assert await client._get_ultra1_peripheral_dids(current_session) == expected
    assert await client._get_ultra1_peripheral_dids(current_session) == expected
    assert await client._get_radar_did(current_session) == radar_60g_did
    client.send_command.assert_awaited_once()


def test_ultra1_command_candidates_use_validated_lan_header() -> None:
    current_session = session()

    assert _command_device_type_candidates(current_session) == [0xE3AC]
    assert _command_message_type_candidates(current_session) == [dna.MESSAGE_TYPE_COMMAND]

    current_session.command_device_type = TYPE_ULTRA1
    current_session.command_message_type = 0x03E9
    assert _command_device_type_candidates(current_session) == [TYPE_ULTRA1, 0xE3AC]
    assert _command_message_type_candidates(current_session) == [0x03E9, dna.MESSAGE_TYPE_COMMAND]


async def test_ultra1_radar_status() -> None:
    client = UltraClient()
    current_session = session()
    radar_did = persisted_did(TYPE_ULTRA2_RADAR, 9)
    client.send_command = AsyncMock(
        side_effect=[
            list_response(radar_did),
            response(
                radar_did,
                level_of_sensitivity=1,
                delaytime=60,
                duration1=60,
                duration2=90,
                duration3=120,
                duration4=180,
            ),
        ]
    )

    status = await client.get_radar_status(current_session)

    assert status.did == radar_did
    assert status.sensitivity == 1
    assert status.trigger_speed is None
    assert status.install_mode is None
    assert status.height is None
    assert status.install_direction is None
    assert status.z_range is None
    assert status.default_absence_delay == 60
    assert status.zone_absence_delays == (60, 90, 120, 180)


@pytest.mark.parametrize(
    "response_payload",
    [
        b"bad",
        emotion.build_subdevice_frame(emotion.CMD_SUBDEVICE_LIST_RESPONSE, {"status": True, "list": []}),
        emotion.build_subdevice_frame(emotion.CMD_SUBDEVICE_LIST_RESPONSE, {"status": 0, "list": "bad"}),
    ],
)
async def test_ultra1_peripheral_list_rejects_invalid_responses(response_payload: bytes) -> None:
    client = UltraClient()
    client.send_command = AsyncMock(return_value=response_payload)

    assert await client._get_ultra1_peripheral_dids(session()) == {}


async def test_ultra1_peripheral_list_allows_transport_fallback() -> None:
    client = UltraClient()
    client.send_command = AsyncMock(side_effect=UltraError("not supported"))

    assert await client._get_ultra1_peripheral_dids(session()) == {}


def test_peripheral_did_validation_and_position_distance() -> None:
    assert derive_radar_did(MAC) == "020000000110dbac00000000dbac0001"
    with pytest.raises(ValueError, match="24 bits"):
        derive_peripheral_did(MAC, 0x1000000)

    update = emotion.parse_local_udp_position_update(
        b'{"detect_position":"[{\\"x\\":1.0,\\"y\\":2.0,\\"z\\":2.0}]"}',
        DEVICE.ip,
        datetime(2026, 7, 16, tzinfo=UTC),
    )
    assert update.target_count == 1
    assert update.nearest_distance == 3.0
    assert not hasattr(update.targets[0], "speed")
