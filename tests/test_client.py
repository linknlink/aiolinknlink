"""Tests for the eMotion Ultra2 client."""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aiolinknlink import (
    DISPLAY_MODEL_ULTRA2,
    TYPE_ULTRA2,
    TYPE_ULTRA2_LAN,
    UltraAuthError,
    UltraClient,
    UltraConnectionError,
    UltraDevice,
    UltraProtocolError,
    UltraRadarStatus,
    UltraRadarZRange,
    derive_ultra2_protocol_mac,
    derive_ultra2_radar_did,
)
from aiolinknlink.client import (
    _auth_device_type_candidates,
    _canonical_esphome_attrs,
    _command_device_type_candidates,
    _normalize_esphome_value,
)
from aiolinknlink.models import UltraSession
from aiolinknlink.protocol import dna, emotion

DEVICE = UltraDevice(
    id="e04b410167bb",
    ip="192.168.1.8",
    port=80,
    mac="e0:4b:41:01:67:bb",
    type_id=TYPE_ULTRA2,
)

RADAR_STATUS = UltraRadarStatus(
    did=derive_ultra2_radar_did(DEVICE.mac),
    sensitivity=2,
    received_at=datetime.now(UTC),
    trigger_speed=1,
    install_mode=0,
    height=240,
    install_direction=1,
    z_range=UltraRadarZRange(minimum=-2.5, maximum=1.75),
    default_absence_delay=60,
    zone_absence_delays=(60, 90, 120, 180),
)


@dataclass
class _Entity:
    object_id: str
    key: int
    device_id: int = 0


@dataclass
class _State:
    key: int
    state: object
    device_id: int = 0
    missing_state: bool = False


class _ESPHomeClient:
    entities = [
        _Entity("sht_temperature", 1),
        _Entity("sht_humidity", 2),
        _Entity("opt3004_light", 3),
        _Entity("zone_any_presence", 4),
        _Entity("all_target_counts", 5),
        _Entity("persons_in_fenced_zones", 6),
        _Entity("wifi_signal_sensor", 7),
        _Entity("zone_1_presence", 8),
        _Entity("zone_1_target_counts", 9),
        _Entity("mqtt_password", 10),
    ]
    states = [
        _State(1, 23.5),
        _State(2, 48.25),
        _State(3, 325.0),
        _State(4, True),
        _State(5, 2.0),
        _State(6, 1.0),
        _State(7, -52.0),
        _State(8, False),
        _State(9, 1.0),
        _State(10, "secret"),
    ]
    mac_address = DEVICE.mac

    def __init__(self, *args: object, **kwargs: object) -> None:
        self.disconnected = False

    async def connect(self, *, login: bool) -> None:
        assert login is True

    async def device_info_and_list_entities(self):
        return SimpleNamespace(mac_address=self.mac_address), self.entities, []

    def subscribe_states(self, callback) -> None:
        for state in self.states:
            callback(state)

    async def disconnect(self, *, force: bool) -> None:
        assert force is True
        self.disconnected = True


async def test_connect(monkeypatch: pytest.MonkeyPatch) -> None:
    session_key = b"0123456789abcdef"
    auth_response = b"\x00" * 4 + session_key + b"\x00" * 12
    send = AsyncMock(return_value=auth_response)
    monkeypatch.setattr(dna, "send_encrypted", send)

    client = UltraClient(command_timeout=0.1, auth_timeout=0.2)
    session = await client.connect(DEVICE)

    assert session.session_key == session_key
    assert session.auth_mac == DEVICE.mac
    assert session.device.model == DISPLAY_MODEL_ULTRA2
    assert session.device.name == DISPLAY_MODEL_ULTRA2
    assert send.await_count == 1
    assert send.call_args.kwargs["timeout"] == 0.2


async def test_connect_preserves_caller_display_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Internal Ultra2 authentication must not overwrite a public model name."""
    session_key = b"0123456789abcdef"
    monkeypatch.setattr(
        dna,
        "send_encrypted",
        AsyncMock(return_value=b"\x00" * 4 + session_key + b"\x00" * 12),
    )
    device = replace(DEVICE, model="eMotion Ultra", name="eMotion Ultra")

    await UltraClient().connect(device)

    assert device.model == "eMotion Ultra"
    assert device.name == "eMotion Ultra"


async def test_get_environment_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only supported local environmental entities are returned."""
    monkeypatch.setattr("aiolinknlink.client.APIClient", _ESPHomeClient)
    session = UltraSession(device=DEVICE, session_key=b"0123456789abcdef")

    state = await UltraClient().get_environment_state(session)

    assert state.values == {
        "temperature": 23.5,
        "humidity": 48.25,
        "illuminance": 325.0,
        "occupancy": True,
        "target_count": 2,
        "persons_in_fenced_zones": 1,
        "wifi_signal": -52,
        "zone_1_presence": False,
        "zone_1_target_counts": 1,
    }
    assert state.available_fields == frozenset(state.values)
    assert "mqtt_password" not in state.values
    assert session.last_seen is not None


@pytest.mark.parametrize(
    ("object_id", "expected"),
    [
        ("Zone-2 Presence", ("zone_2_presence",)),
        ("zone_4_target_counts", ("zone_4_target_counts",)),
        ("mqtt_password", ()),
        ("ip_address", ()),
    ],
)
def test_canonical_esphome_attrs(object_id: str, expected: tuple[str, ...]) -> None:
    assert _canonical_esphome_attrs(object_id) == expected


@pytest.mark.parametrize(
    ("attr", "value", "expected"),
    [
        ("occupancy", 1, None),
        ("zone_1_presence", False, False),
        ("temperature", True, None),
        ("temperature", "23", None),
        ("temperature", float("nan"), None),
        ("temperature", 23, 23.0),
        ("target_count", 2.4, 2),
    ],
)
def test_normalize_esphome_value(attr: str, value: object, expected: object) -> None:
    assert _normalize_esphome_value(attr, value) == expected


async def test_reauthenticate_preserves_working_command_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A renewed auth key must keep the previously confirmed command variant."""
    client = UltraClient()
    session = UltraSession(
        device=DEVICE,
        session_key=b"old-session-key!",
        auth_mac="e0:4b:41:02:44:c9",
        command_device_type=TYPE_ULTRA2_LAN,
        command_message_type=0x03E9,
    )
    refreshed = UltraSession(
        device=DEVICE,
        session_key=b"new-session-key!",
        auth_mac="e0:4b:41:02:44:c9",
        auth_device_type=TYPE_ULTRA2,
        auth_status="ok",
    )
    connect = AsyncMock(return_value=refreshed)
    monkeypatch.setattr(client, "connect", connect)

    await client.reauthenticate(session)

    assert session.session_key == b"new-session-key!"
    assert session.command_device_type == TYPE_ULTRA2_LAN
    assert session.command_message_type == 0x03E9


async def test_connect_requires_mac() -> None:
    client = UltraClient()
    with pytest.raises(UltraAuthError, match="missing mac"):
        await client.connect(UltraDevice(id="device", ip="192.168.1.8", port=80))


def test_derive_ultra2_protocol_mac() -> None:
    assert derive_ultra2_protocol_mac("E0:4B:41:02:44:C7") == "e0:4b:41:02:44:c9"
    with pytest.raises(ValueError, match="invalid Ultra2 LAN MAC"):
        derive_ultra2_protocol_mac("not-a-mac")


def test_derive_ultra2_radar_did() -> None:
    assert derive_ultra2_radar_did("E0:4B:41:02:44:C7") == "e04b410244c7dbac00000000dbac0001"
    with pytest.raises(ValueError, match="invalid Ultra2 LAN MAC"):
        derive_ultra2_radar_did("not-a-mac")


async def test_get_radar_status_uses_peripheral_did() -> None:
    client = UltraClient()
    session = UltraSession(device=DEVICE, session_key=b"0123456789abcdef")
    response = emotion.build_subdevice_frame(
        emotion.CMD_STATUS_RESPONSE,
        {
            "did": derive_ultra2_radar_did(DEVICE.mac),
            "level_of_sensitivity": 2,
            "triger_speed": 1,
            "install_mode": 0,
            "height": 240,
            "install_direction": 1,
            "z_range": '{"min":-2.5,"max":1.75}',
            "delaytime": 60,
            "duration1": 60,
            "duration2": 90,
            "duration3": 120,
            "duration4": 180,
            "status": 0,
        },
    )
    send_command = AsyncMock(return_value=response)
    client.send_command = send_command

    status = await client.get_radar_status(session)

    assert status.did == "e04b410167bbdbac00000000dbac0001"
    assert status.sensitivity == 2
    assert status.trigger_speed == 1
    assert status.install_mode == 0
    assert status.height == 240
    assert status.install_direction == 1
    assert status.z_range == UltraRadarZRange(minimum=-2.5, maximum=1.75)
    assert status.default_absence_delay == 60
    assert status.zone_absence_delays == (60, 90, 120, 180)
    command = emotion.parse_subdevice_frame(send_command.call_args.args[1])
    assert emotion.parse_subdevice_json_payload(command)["did"] == status.did


async def test_set_radar_sensitivity_requires_matching_readback() -> None:
    client = UltraClient()
    session = UltraSession(device=DEVICE, session_key=b"0123456789abcdef")
    client.send_command = AsyncMock(return_value=b"set-response")
    status = UltraRadarStatus(
        did=derive_ultra2_radar_did(DEVICE.mac),
        sensitivity=1,
        received_at=datetime.now(UTC),
    )
    client.get_radar_status = AsyncMock(return_value=status)

    assert await client.set_radar_sensitivity(session, 1) is status
    command = emotion.parse_subdevice_frame(client.send_command.call_args.args[1])
    assert emotion.parse_subdevice_json_payload(command) == {
        "did": status.did,
        "level_of_sensitivity": 1,
    }

    client.get_radar_status.return_value = UltraRadarStatus(
        did=status.did,
        sensitivity=2,
        received_at=datetime.now(UTC),
    )
    with pytest.raises(UltraProtocolError, match="read-back mismatch"):
        await client.set_radar_sensitivity(session, 1)
    with pytest.raises(ValueError, match="must be 0, 1, or 2"):
        await client.set_radar_sensitivity(session, 3)


@pytest.mark.parametrize(
    ("method", "arguments", "field", "value", "readback"),
    [
        (
            "set_radar_trigger_speed",
            (2,),
            "triger_speed",
            2,
            replace(RADAR_STATUS, trigger_speed=2),
        ),
        (
            "set_radar_install_mode",
            (1,),
            "install_mode",
            1,
            replace(RADAR_STATUS, install_mode=1),
        ),
        (
            "set_radar_height",
            (260,),
            "height",
            260,
            replace(RADAR_STATUS, height=260),
        ),
        (
            "set_radar_install_direction",
            (0,),
            "install_direction",
            0,
            replace(RADAR_STATUS, install_direction=0),
        ),
        (
            "set_radar_default_absence_delay",
            (75,),
            "delaytime",
            75,
            replace(RADAR_STATUS, default_absence_delay=75),
        ),
        (
            "set_radar_zone_absence_delay",
            (3, 150),
            "duration3",
            150,
            replace(RADAR_STATUS, zone_absence_delays=(60, 90, 150, 180)),
        ),
    ],
)
async def test_set_radar_scalar_fields_require_matching_readback(
    method: str,
    arguments: tuple[int, ...],
    field: str,
    value: int,
    readback: UltraRadarStatus,
) -> None:
    """Each public setter writes one firmware field and returns a fresh status."""
    client = UltraClient()
    session = UltraSession(device=DEVICE, session_key=b"0123456789abcdef")
    client.send_command = AsyncMock(return_value=b"set-response")
    client.get_radar_status = AsyncMock(return_value=readback)

    assert await getattr(client, method)(session, *arguments) is readback
    command = emotion.parse_subdevice_frame(client.send_command.call_args.args[1])
    assert emotion.parse_subdevice_json_payload(command) == {
        "did": RADAR_STATUS.did,
        field: value,
    }


async def test_set_radar_z_range_uses_firmware_json_and_tolerant_readback() -> None:
    """Z limits use the firmware JSON field and tolerate float serialization."""
    client = UltraClient()
    session = UltraSession(device=DEVICE, session_key=b"0123456789abcdef")
    client.send_command = AsyncMock(return_value=b"set-response")
    readback = replace(
        RADAR_STATUS,
        z_range=UltraRadarZRange(minimum=-2.5, maximum=1.25001),
    )
    client.get_radar_status = AsyncMock(return_value=readback)

    assert await client.set_radar_z_range(session, -2.5, 1.25) is readback
    command = emotion.parse_subdevice_frame(client.send_command.call_args.args[1])
    payload = emotion.parse_subdevice_json_payload(command)
    assert payload["did"] == RADAR_STATUS.did
    assert json.loads(payload["z_range"]) == {"min": -2.5, "max": 1.25}

    with pytest.raises(ValueError, match="less than"):
        await client.set_radar_z_range(session, 1.0, 1.0)
    with pytest.raises(ValueError, match="between"):
        await client.set_radar_z_range(session, -7.0, 1.0)


async def test_get_radar_status_rejects_invalid_optional_fields() -> None:
    """Malformed device-read configuration must not reach callers as valid state."""
    client = UltraClient()
    session = UltraSession(device=DEVICE, session_key=b"0123456789abcdef")
    response = emotion.build_subdevice_frame(
        emotion.CMD_STATUS_RESPONSE,
        {
            "did": RADAR_STATUS.did,
            "level_of_sensitivity": 2,
            "z_range": "not-json",
            "status": 0,
        },
    )
    client.send_command = AsyncMock(return_value=response)

    with pytest.raises(UltraProtocolError, match="invalid radar z_range"):
        await client.get_radar_status(session)


async def test_send_command_allows_more_time_for_confirmed_variant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A known working command variant tolerates a slow device response."""
    send = AsyncMock(return_value=b"response")
    monkeypatch.setattr(dna, "send_encrypted", send)
    client = UltraClient(command_timeout=0.1, preferred_command_timeout=0.3)
    session = UltraSession(
        device=DEVICE,
        session_key=b"0123456789abcdef",
        auth_mac=DEVICE.mac,
        command_device_type=TYPE_ULTRA2_LAN,
        command_message_type=0x03E9,
    )

    assert await client.send_command(session, b"command", try_all=False) == b"response"
    assert send.call_args.kwargs["timeout"] == 0.3


async def test_discover_filters_other_dna_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only supported Ultra2 device types are returned by discovery."""
    discovered = [
        dna.DiscoveredDevice(
            id="other",
            ip="192.168.1.7",
            port=80,
            mac="e0:4b:41:01:67:ba",
            device_type=0x702B,
            name="IBG",
        ),
        dna.DiscoveredDevice(
            id="emotion-air",
            ip="192.168.1.6",
            port=80,
            mac="e0:4b:41:01:67:b9",
            device_type=0x702B,
            name="eMotion Air",
        ),
        dna.DiscoveredDevice(
            id=DEVICE.id,
            ip=DEVICE.ip,
            port=DEVICE.port,
            mac=DEVICE.mac,
            device_type=TYPE_ULTRA2,
            name="eMotion Ultra 2",
        ),
    ]
    discover = AsyncMock(return_value=discovered)
    monkeypatch.setattr("aiolinknlink.client._discover_dna_devices", discover)

    devices = await UltraClient().discover()

    assert [device.id for device in devices] == [DEVICE.id]


async def test_discover_host_returns_matching_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """Targeted discovery selects the device at the requested host."""
    devices = [
        UltraDevice(
            id="other",
            ip="192.168.1.7",
            port=80,
            mac="e0:4b:41:01:67:ba",
        ),
        DEVICE,
    ]
    discover = AsyncMock(return_value=devices)
    monkeypatch.setattr(UltraClient, "discover", discover)

    device = await UltraClient().discover_host(DEVICE.ip)

    assert device is DEVICE


async def test_discover_host_rejects_unsupported_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Targeted discovery fails when the target is not a supported device."""
    monkeypatch.setattr(UltraClient, "discover", AsyncMock(return_value=[]))

    with pytest.raises(UltraConnectionError, match="no supported LinknLink device"):
        await UltraClient().discover_host(DEVICE.ip)


async def test_discover_host_retries_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Targeted discovery tolerates a lost discovery response."""
    discover = AsyncMock(side_effect=[[], [DEVICE]])
    monkeypatch.setattr(UltraClient, "discover", discover)

    assert await UltraClient().discover_host(DEVICE.ip) is DEVICE
    assert discover.await_count == 2


@pytest.mark.parametrize(
    ("device_type", "expected"),
    [
        (TYPE_ULTRA2, [TYPE_ULTRA2, TYPE_ULTRA2_LAN]),
        (TYPE_ULTRA2_LAN, [TYPE_ULTRA2_LAN, TYPE_ULTRA2]),
        (0, [TYPE_ULTRA2, TYPE_ULTRA2_LAN]),
    ],
)
def test_auth_device_type_candidates(device_type: int, expected: list[int]) -> None:
    assert _auth_device_type_candidates(device_type) == expected


def test_command_candidates_prefer_authenticated_type() -> None:
    device = UltraDevice(
        id="e04b410167bb",
        ip="192.168.1.8",
        port=80,
        mac="e0:4b:41:01:67:bb",
        type_id=TYPE_ULTRA2,
    )
    session = UltraSession(device=device, auth_device_type=TYPE_ULTRA2_LAN)

    assert _command_device_type_candidates(session) == [
        TYPE_ULTRA2_LAN,
        TYPE_ULTRA2,
    ]
