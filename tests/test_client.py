"""Tests for the eMotion Ultra client."""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aiolinknlink import (
    DISPLAY_MODEL_ULTRA2,
    TYPE_ULTRA,
    TYPE_ULTRA2,
    TYPE_ULTRA2_LAN,
    UltraAuthError,
    UltraClient,
    UltraConnectionError,
    UltraDevice,
)
from aiolinknlink.client import (
    _auth_device_type_candidates,
    _canonical_esphome_attrs,
    _command_device_type_candidates,
    _supports_esphome_api,
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


async def test_connect_and_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    session_key = b"0123456789abcdef"
    auth_response = b"\x00" * 4 + session_key + b"\x00" * 12
    gateway_response = b'\x26\x00\x00\x00{"rssi":-43,"lb_online1":1}'
    empty_list = emotion.build_subdevice_frame(emotion.CMD_SUBDEVICE_LIST_RESPONSE, {"list": []})
    send = AsyncMock(side_effect=[auth_response, gateway_response, empty_list])
    monkeypatch.setattr(dna, "send_encrypted", send)

    client = UltraClient(command_timeout=0.1)
    monkeypatch.setattr(client, "_refresh_esphome_state", AsyncMock(return_value=False))
    session = await client.connect(DEVICE)
    state = await client.refresh(session)

    assert session.session_key == session_key
    assert session.device.model == DISPLAY_MODEL_ULTRA2
    assert session.device.name == DISPLAY_MODEL_ULTRA2
    assert state.online is True
    assert state.values["wifi_rssi"] == -43
    assert send.await_count == 3


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
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.states = [
            _State(1, 23.5),
            _State(2, 48.0),
            _State(3, True),
            _State(4, 2.0),
            _State(5, 1.0),
        ]

    async def connect(self, *, login: bool) -> None:
        assert login is True

    async def device_info_and_list_entities(self):
        return (
            SimpleNamespace(mac_address=DEVICE.mac),
            [
                _Entity("sht_temperature", 1),
                _Entity("sht_humidity", 2),
                _Entity("zone_any_presence", 3),
                _Entity("all_target_counts", 4),
                _Entity("zone_1_target_counts", 5),
                _Entity("mqtt_password", 6),
            ],
            [],
        )

    def subscribe_states(self, callback) -> None:
        for state in self.states:
            callback(state)

    async def disconnect(self, *, force: bool) -> None:
        assert force is True


async def test_refresh_prefers_esphome_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ultra2 state is populated from its standard local API."""
    monkeypatch.setattr("aiolinknlink.client.APIClient", _ESPHomeClient)
    send = AsyncMock()
    monkeypatch.setattr(dna, "send_encrypted", send)
    session = UltraSession(device=DEVICE, session_key=b"0123456789abcdef")

    state = await UltraClient(command_timeout=1).refresh(session)

    assert state.values == {
        "state": "online",
        "envtemp": 23.5,
        "envhumid": 48.0,
        "presence": True,
        "target_count": 2,
        "zone_1_target_counts": 1,
    }
    assert state.raw["primary_protocol"] == "esphome_api"
    send.assert_not_awaited()


@pytest.mark.parametrize(
    ("object_id", "expected"),
    [
        ("Zone_2_Presence", ("zone_2_presence",)),
        ("zone_4_target_counts", ("zone_4_target_counts",)),
        ("mqtt_password", ()),
        ("ip_address", ()),
    ],
)
def test_canonical_esphome_attrs(object_id: str, expected: tuple[str, ...]) -> None:
    assert _canonical_esphome_attrs(object_id) == expected


async def test_refresh_skips_esphome_for_legacy_ultra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy Ultra devices use DNA without an ESPHome connection attempt."""
    device = UltraDevice(
        id=DEVICE.id,
        ip=DEVICE.ip,
        port=DEVICE.port,
        mac=DEVICE.mac,
        type_id=TYPE_ULTRA,
    )
    gateway_response = b'\x26\x00\x00\x00{"pir_detected":true}'
    empty_list = emotion.build_subdevice_frame(emotion.CMD_SUBDEVICE_LIST_RESPONSE, {"list": []})
    send = AsyncMock(side_effect=[gateway_response, empty_list])
    monkeypatch.setattr(dna, "send_encrypted", send)
    client = UltraClient(command_timeout=0.1)
    esphome_refresh = AsyncMock(return_value=False)
    monkeypatch.setattr(client, "_refresh_esphome_state", esphome_refresh)
    session = UltraSession(device=device, session_key=b"0123456789abcdef")

    state = await client.refresh(session)

    esphome_refresh.assert_not_awaited()
    assert state.values["presence"] is True


def test_esphome_support_is_limited_to_ultra2() -> None:
    assert _supports_esphome_api(DEVICE)
    assert not _supports_esphome_api(
        UltraDevice(
            id=DEVICE.id,
            ip=DEVICE.ip,
            port=DEVICE.port,
            mac=DEVICE.mac,
            type_id=TYPE_ULTRA,
        )
    )


async def test_connect_requires_mac() -> None:
    client = UltraClient()
    with pytest.raises(UltraAuthError, match="missing mac"):
        await client.connect(UltraDevice(id="device", ip="192.168.1.8", port=80))


async def test_discover_filters_other_dna_devices(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only supported Ultra device types are returned by discovery."""
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
        (TYPE_ULTRA, [TYPE_ULTRA, TYPE_ULTRA2, TYPE_ULTRA2_LAN]),
        (TYPE_ULTRA2, [TYPE_ULTRA2, TYPE_ULTRA2_LAN, TYPE_ULTRA]),
        (TYPE_ULTRA2_LAN, [TYPE_ULTRA2_LAN, TYPE_ULTRA2, TYPE_ULTRA]),
        (0, [TYPE_ULTRA2, TYPE_ULTRA2_LAN, TYPE_ULTRA]),
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
        type_id=TYPE_ULTRA,
    )
    session = UltraSession(device=device, auth_device_type=TYPE_ULTRA)

    assert _command_device_type_candidates(session) == [
        TYPE_ULTRA,
        TYPE_ULTRA2,
        TYPE_ULTRA2_LAN,
    ]


async def test_control_builds_subdevice_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    client = UltraClient()
    session = await _session(client, monkeypatch)
    send = AsyncMock(return_value=b"ok")
    monkeypatch.setattr(dna, "send_encrypted", send)

    await client.control(session, "power", "radar-1", {"state": "on"})

    command = send.await_args.args[3]
    frame = emotion.parse_subdevice_frame(command)
    payload = emotion.parse_subdevice_json_payload(frame)
    assert frame.command_type == emotion.CMD_SET_STATUS
    assert payload == {"did": "radar-1", "power": True}


async def _session(client: UltraClient, monkeypatch: pytest.MonkeyPatch):
    session_key = b"0123456789abcdef"
    response = b"\x00" * 4 + session_key + b"\x00" * 12
    monkeypatch.setattr(dna, "send_encrypted", AsyncMock(return_value=response))
    return await client.connect(DEVICE)
