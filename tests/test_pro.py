"""Tests for conditional eMotionPro local support."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from aiolinknlink import (
    TYPE_EMOTION_PRO,
    TYPE_EMOTION_PRO_RADAR,
    TYPE_ULTRA2,
    DeviceCapability,
    DeviceModel,
    UltraClient,
    UltraDevice,
    UltraError,
    UltraProtocolError,
)
from aiolinknlink.client import (
    TYPE_LEGACY_SHTXX,
    TYPE_PRO_RADAR_24G,
    _auth_device_type_candidates,
    _command_device_type_candidates,
    _command_message_type_candidates,
    derive_peripheral_did,
)
from aiolinknlink.models import UltraSession
from aiolinknlink.protocol import dna, emotion, keyvalue

MAC = "02:00:00:00:01:10"
SESSION_KEY = b"0123456789abcdef"


def _device(device_type: int = TYPE_EMOTION_PRO) -> UltraDevice:
    return UltraDevice(
        id=MAC.replace(":", ""),
        ip="198.51.100.8",
        port=80,
        mac=MAC,
        type_id=device_type,
    )


def _session(device_type: int = TYPE_EMOTION_PRO) -> UltraSession:
    return UltraSession(device=_device(device_type), session_key=SESSION_KEY, auth_mac=MAC)


def _response(values: dict[str, object]) -> bytes:
    return keyvalue.build_frame(
        keyvalue.CMD_STATUS_RESPONSE,
        json.dumps(values, separators=(",", ":")).encode(),
    )


def _did(peripheral_type: int, suffix: int) -> str:
    return (bytes.fromhex(MAC.replace(":", "")) + peripheral_type.to_bytes(4, "little") + bytes([suffix]) * 6).hex()


def _subdevice_response(did: str, **values: object) -> bytes:
    return emotion.build_subdevice_frame(
        emotion.CMD_STATUS_RESPONSE,
        {"did": did, "status": 0, **values},
    )


def _list_response(*dids: str) -> bytes:
    return emotion.build_subdevice_frame(
        emotion.CMD_SUBDEVICE_LIST_RESPONSE,
        {"status": 0, "list": [{"did": did} for did in dids]},
    )


async def test_discover_emotion_pro(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = dna.DiscoveredDevice(
        id=MAC,
        ip="198.51.100.8",
        port=80,
        mac=MAC,
        device_type=TYPE_EMOTION_PRO,
    )
    monkeypatch.setattr(
        "aiolinknlink.client._discover_dna_devices",
        AsyncMock(return_value=[raw]),
    )

    devices = await UltraClient().discover()

    assert len(devices) == 1
    assert devices[0].profile is not None
    assert devices[0].profile.model is DeviceModel.EMOTION_PRO
    assert devices[0].model == "eMotion Pro"
    assert devices[0].capabilities == frozenset(
        {
            DeviceCapability.ENVIRONMENT,
            DeviceCapability.TEMPERATURE,
            DeviceCapability.HUMIDITY,
            DeviceCapability.OCCUPANCY,
            DeviceCapability.ABSENCE_DELAY,
        }
    )
    assert DeviceCapability.REMOTE not in devices[0].capabilities


async def test_discover_radar_emotion_pro(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = dna.DiscoveredDevice(
        id=MAC,
        ip="198.51.100.8",
        port=80,
        mac=MAC,
        device_type=TYPE_EMOTION_PRO_RADAR,
        name="rm_radar",
    )
    monkeypatch.setattr("aiolinknlink.client._discover_dna_devices", AsyncMock(return_value=[raw]))

    devices = await UltraClient().discover()

    assert len(devices) == 1
    assert devices[0].profile is not None
    assert devices[0].profile.model is DeviceModel.EMOTION_PRO
    assert devices[0].model == "eMotion Pro"
    assert _auth_device_type_candidates(TYPE_EMOTION_PRO_RADAR) == [0x9CAC, TYPE_EMOTION_PRO_RADAR]


async def test_radar_pro_auth_uses_compatible_type_and_command_header(monkeypatch: pytest.MonkeyPatch) -> None:
    session_key = b"fedcba9876543210"
    send = AsyncMock(return_value=b"\x00" * 4 + session_key + b"\x00" * 12)
    monkeypatch.setattr(dna, "send_encrypted", send)

    session = await UltraClient().connect(_device(TYPE_EMOTION_PRO_RADAR))

    assert session.auth_device_type == 0x9CAC
    assert session.command_device_type == 0xE3AC
    assert session.command_message_type == dna.MESSAGE_TYPE_COMMAND
    assert send.call_args.args[2].device_type == 0x9CAC

    assert _command_device_type_candidates(session) == [0xE3AC]
    assert _command_message_type_candidates(session) == [dna.MESSAGE_TYPE_COMMAND]


async def test_get_pro_environment_state() -> None:
    client = UltraClient()
    client.send_command = AsyncMock(
        return_value=_response(
            {
                "tempsensor": 235,
                "humsensor": 48,
                "pir_detected": 1,
                "delaytime": 5,
                "ac_pwr": 1,
                "temp": 25,
            }
        )
    )
    session = _session()

    state = await client.get_environment_state(session)

    assert state.values == {
        "temperature": 23.5,
        "humidity": 48,
        "occupancy": True,
        "absence_delay": 300,
    }
    assert "ac_pwr" not in state.values
    assert "temp" not in state.values
    assert state.available_fields == frozenset(state.values)
    assert session.last_seen is not None
    command, payload = keyvalue.parse_frame(client.send_command.call_args.args[1])
    assert command == keyvalue.CMD_GET_STATUS
    assert payload == b""


async def test_get_radar_pro_environment_state() -> None:
    radar_did = _did(TYPE_PRO_RADAR_24G, 1)
    climate_did = _did(TYPE_LEGACY_SHTXX, 2)
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            _list_response(radar_did, climate_did),
            _subdevice_response(radar_did, pir_detected=1, delaytime=45, range=225),
            _subdevice_response(climate_did, envtemp=2365, envhumid=4875),
        ]
    )

    state = await client.get_environment_state(_session(TYPE_EMOTION_PRO_RADAR))

    assert state.values == {
        "occupancy": True,
        "absence_delay": 45,
        "temperature": 23.65,
        "humidity": 48.75,
    }
    assert "range" not in state.values


async def test_radar_pro_allows_missing_optional_climate() -> None:
    radar_did = _did(TYPE_PRO_RADAR_24G, 1)
    climate_did = derive_peripheral_did(MAC, TYPE_LEGACY_SHTXX)
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            _list_response(radar_did),
            _subdevice_response(radar_did, pir_detected=0, delaytime=60),
            emotion.build_subdevice_frame(
                emotion.CMD_STATUS_RESPONSE,
                {"did": climate_did, "status": -1},
            ),
        ]
    )

    state = await client.get_environment_state(_session(TYPE_EMOTION_PRO_RADAR))

    assert state.values == {"occupancy": False, "absence_delay": 60}


async def test_radar_pro_derives_climate_when_peripheral_list_fails() -> None:
    radar_did = derive_peripheral_did(MAC, TYPE_PRO_RADAR_24G)
    climate_did = derive_peripheral_did(MAC, TYPE_LEGACY_SHTXX)
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            emotion.build_subdevice_frame(
                emotion.CMD_SUBDEVICE_LIST_RESPONSE,
                {"status": -1},
            ),
            _subdevice_response(radar_did, pir_detected=1, delaytime=60),
            _subdevice_response(climate_did, envtemp=2659, envhumid=5191),
        ]
    )

    state = await client.get_environment_state(_session(TYPE_EMOTION_PRO_RADAR))

    assert state.values == {
        "occupancy": True,
        "absence_delay": 60,
        "temperature": 26.59,
        "humidity": 51.91,
    }


async def test_radar_pro_ignores_unavailable_optional_climate() -> None:
    radar_did = _did(TYPE_PRO_RADAR_24G, 1)
    climate_did = _did(TYPE_LEGACY_SHTXX, 2)
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            _list_response(radar_did, climate_did),
            _subdevice_response(radar_did, pir_detected=1),
            emotion.build_subdevice_frame(
                emotion.CMD_STATUS_RESPONSE,
                {"did": climate_did, "status": -1},
            ),
        ]
    )

    state = await client.get_environment_state(_session(TYPE_EMOTION_PRO_RADAR))

    assert state.values == {"occupancy": True}


async def test_radar_pro_rejects_malformed_required_radar() -> None:
    radar_did = _did(TYPE_PRO_RADAR_24G, 1)
    client = UltraClient()
    client.send_command = AsyncMock(side_effect=[_list_response(radar_did), b"bad"])

    with pytest.raises(UltraProtocolError, match="subdevice frame too short"):
        await client.get_environment_state(_session(TYPE_EMOTION_PRO_RADAR))


async def test_radar_pro_peripheral_list_filters_invalid_entries_and_caches() -> None:
    radar_did = _did(TYPE_PRO_RADAR_24G, 1)
    offline_climate_did = _did(TYPE_LEGACY_SHTXX, 2)
    client = UltraClient()
    client.send_command = AsyncMock(
        return_value=emotion.build_subdevice_frame(
            emotion.CMD_SUBDEVICE_LIST_RESPONSE,
            {
                "status": 0,
                "list": [None, {}, {"did": "bad"}, {"did": radar_did}, {"did": offline_climate_did, "offline": 1}],
            },
        )
    )
    session = _session(TYPE_EMOTION_PRO_RADAR)

    assert await client._get_pro_radar_peripheral_dids(session) == {TYPE_PRO_RADAR_24G: radar_did}
    assert await client._get_pro_radar_peripheral_dids(session) == {TYPE_PRO_RADAR_24G: radar_did}
    client.send_command.assert_awaited_once()


async def test_pro_partial_state_omits_missing_fields() -> None:
    client = UltraClient()
    client.send_command = AsyncMock(return_value=_response({"pir_detected": 0}))

    state = await client.get_environment_state(_session())

    assert state.values == {"occupancy": False}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tempsensor", True),
        ("tempsensor", 1301),
        ("humsensor", -1),
        ("humsensor", 101),
        ("pir_detected", 2),
        ("delaytime", 65536),
    ],
)
async def test_pro_rejects_invalid_state(field: str, value: object) -> None:
    client = UltraClient()
    client.send_command = AsyncMock(return_value=_response({field: value}))

    with pytest.raises(UltraProtocolError, match=field):
        await client.get_environment_state(_session())


async def test_pro_rejects_empty_or_malformed_state() -> None:
    client = UltraClient()
    client.send_command = AsyncMock(return_value=_response({"ac_pwr": 1}))

    with pytest.raises(UltraProtocolError, match="did not contain"):
        await client.get_environment_state(_session())

    client.send_command.return_value = b"invalid"
    with pytest.raises(UltraProtocolError, match="too short"):
        await client.get_environment_state(_session())


async def test_set_pro_absence_delay_writes_minutes_and_reads_back() -> None:
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            _response({"delaytime": 10}),
            _response(
                {
                    "tempsensor": 241,
                    "humsensor": 50,
                    "pir_detected": 0,
                    "delaytime": 10,
                }
            ),
        ]
    )
    session = _session()

    state = await client.set_pro_absence_delay(session, 600)

    assert state.values["absence_delay"] == 600
    command, payload = keyvalue.parse_frame(client.send_command.await_args_list[0].args[1])
    assert command == keyvalue.CMD_SET_STATUS
    assert json.loads(payload) == {"delaytime": 10}
    command, payload = keyvalue.parse_frame(client.send_command.await_args_list[1].args[1])
    assert command == keyvalue.CMD_GET_STATUS
    assert payload == b""


async def test_set_radar_pro_absence_delay_writes_seconds_and_reads_back() -> None:
    radar_did = _did(TYPE_PRO_RADAR_24G, 1)
    climate_did = derive_peripheral_did(MAC, TYPE_LEGACY_SHTXX)
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            _list_response(radar_did),
            _subdevice_response(radar_did),
            _subdevice_response(radar_did, pir_detected=0, delaytime=61),
            emotion.build_subdevice_frame(
                emotion.CMD_STATUS_RESPONSE,
                {"did": climate_did, "status": -1},
            ),
        ]
    )

    state = await client.set_pro_absence_delay(_session(TYPE_EMOTION_PRO_RADAR), 61)

    assert state.values["absence_delay"] == 61
    frame = emotion.parse_subdevice_frame(client.send_command.await_args_list[1].args[1])
    assert emotion.parse_subdevice_json_payload(frame) == {"did": radar_did, "delaytime": 61}


async def test_set_radar_pro_absence_delay_retries_transient_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radar_did = _did(TYPE_PRO_RADAR_24G, 1)
    climate_did = derive_peripheral_did(MAC, TYPE_LEGACY_SHTXX)
    sleep = AsyncMock()
    monkeypatch.setattr("aiolinknlink.client.asyncio.sleep", sleep)
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            _list_response(radar_did),
            _subdevice_response(radar_did),
            UltraError("device is applying the setting"),
            _subdevice_response(radar_did, pir_detected=1, delaytime=61),
            emotion.build_subdevice_frame(
                emotion.CMD_STATUS_RESPONSE,
                {"did": climate_did, "status": -1},
            ),
        ]
    )

    state = await client.set_pro_absence_delay(_session(TYPE_EMOTION_PRO_RADAR), 61)

    assert state.values["absence_delay"] == 61
    sleep.assert_awaited_once_with(1.0)


async def test_set_radar_pro_absence_delay_limits_readback_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radar_did = _did(TYPE_PRO_RADAR_24G, 1)
    sleep = AsyncMock()
    monkeypatch.setattr("aiolinknlink.client.asyncio.sleep", sleep)
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            _list_response(radar_did),
            _subdevice_response(radar_did),
            UltraError("first timeout"),
            UltraError("second timeout"),
        ]
    )

    with pytest.raises(UltraProtocolError, match="second timeout"):
        await client.set_pro_absence_delay(_session(TYPE_EMOTION_PRO_RADAR), 61)

    sleep.assert_awaited_once_with(1.0)


async def test_set_radar_pro_absence_delay_requires_valid_ack() -> None:
    radar_did = _did(TYPE_PRO_RADAR_24G, 1)
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            _list_response(radar_did),
            emotion.build_subdevice_frame(
                emotion.CMD_STATUS_RESPONSE,
                {"did": radar_did, "status": -1},
            ),
        ]
    )

    with pytest.raises(UltraProtocolError, match="absence delay write failed"):
        await client.set_pro_absence_delay(_session(TYPE_EMOTION_PRO_RADAR), 60)


async def test_set_radar_pro_absence_delay_requires_matching_readback() -> None:
    radar_did = _did(TYPE_PRO_RADAR_24G, 1)
    climate_did = derive_peripheral_did(MAC, TYPE_LEGACY_SHTXX)
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            _list_response(radar_did),
            _subdevice_response(radar_did),
            _subdevice_response(radar_did, delaytime=59),
            emotion.build_subdevice_frame(
                emotion.CMD_STATUS_RESPONSE,
                {"did": climate_did, "status": -1},
            ),
        ]
    )

    with pytest.raises(UltraProtocolError, match="read-back mismatch"):
        await client.set_pro_absence_delay(_session(TYPE_EMOTION_PRO_RADAR), 60)


def test_radar_pro_peripheral_did_validation() -> None:
    with pytest.raises(ValueError, match="invalid LinknLink LAN MAC"):
        derive_peripheral_did("bad", TYPE_PRO_RADAR_24G)
    with pytest.raises(ValueError, match="fit in 24 bits"):
        derive_peripheral_did(MAC, 0x1000000)


async def test_set_pro_absence_delay_requires_matching_readback() -> None:
    client = UltraClient()
    client.send_command = AsyncMock(
        side_effect=[
            _response({"delaytime": 10}),
            _response({"delaytime": 9}),
        ]
    )

    with pytest.raises(UltraProtocolError, match="read-back mismatch"):
        await client.set_pro_absence_delay(_session(), 600)


async def test_set_pro_absence_delay_validates_response() -> None:
    client = UltraClient()
    client.send_command = AsyncMock(return_value=b"invalid")

    with pytest.raises(UltraProtocolError, match="too short"):
        await client.set_pro_absence_delay(_session(), 60)


@pytest.mark.parametrize(
    ("seconds", "message"),
    [
        (-60, "between"),
        (0x10000 * 60, "between"),
        (61, "whole number of minutes"),
    ],
)
async def test_set_pro_absence_delay_validates_value(seconds: int, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        await UltraClient().set_pro_absence_delay(_session(), seconds)


async def test_set_pro_absence_delay_rejects_other_models() -> None:
    with pytest.raises(UltraProtocolError, match="not an eMotion Pro operation"):
        await UltraClient().set_pro_absence_delay(_session(TYPE_ULTRA2), 60)
