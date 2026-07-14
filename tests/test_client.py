"""Tests for the eMotion Ultra2 client."""

from __future__ import annotations

from datetime import UTC, datetime
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
    derive_ultra2_protocol_mac,
    derive_ultra2_radar_did,
)
from aiolinknlink.client import (
    _auth_device_type_candidates,
    _command_device_type_candidates,
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
            "status": 0,
        },
    )
    send_command = AsyncMock(return_value=response)
    client.send_command = send_command

    status = await client.get_radar_status(session)

    assert status.did == "e04b410167bbdbac00000000dbac0001"
    assert status.sensitivity == 2
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
