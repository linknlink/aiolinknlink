"""Edge-case tests for the Ultra2 asynchronous client."""

from __future__ import annotations

import asyncio
import math
import socket
import struct
from unittest.mock import AsyncMock

import pytest

import aiolinknlink.client as client_module
from aiolinknlink import (
    PID_ULTRA2,
    TYPE_ULTRA2,
    TYPE_ULTRA2_LAN,
    UltraAuthError,
    UltraClient,
    UltraConnectionError,
    UltraDevice,
    UltraError,
    UltraProtocolError,
    UltraRadarStatus,
    UltraRadarZRange,
    derive_ultra2_radar_did,
)
from aiolinknlink.models import UltraSession
from aiolinknlink.protocol import dna, emotion

MAC = "e0:4b:41:02:44:c7"


def device(**changes: object) -> UltraDevice:
    """Create a fresh device for a test."""
    values: dict[str, object] = {
        "id": "e04b410244c7",
        "ip": "192.168.3.159",
        "port": 80,
        "mac": MAC,
        "type_id": TYPE_ULTRA2,
    }
    values.update(changes)
    return UltraDevice(**values)  # type: ignore[arg-type]


def radar_response(**changes: object) -> bytes:
    """Build one radar status response."""
    values: dict[str, object] = {
        "did": derive_ultra2_radar_did(MAC),
        "level_of_sensitivity": 2,
        "status": 0,
    }
    values.update(changes)
    return emotion.build_subdevice_frame(emotion.CMD_STATUS_RESPONSE, values)


async def test_discover_deduplicates_supported_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = dna.DiscoveredDevice(
        id="first",
        ip="192.168.3.159",
        port=80,
        mac=MAC,
        device_type=TYPE_ULTRA2,
    )
    duplicate = dna.DiscoveredDevice(
        id="second",
        ip="192.168.3.160",
        port=80,
        mac=MAC,
        device_type=TYPE_ULTRA2,
    )
    monkeypatch.setattr(
        client_module,
        "_discover_dna_devices",
        AsyncMock(return_value=[raw, duplicate]),
    )

    assert len(await UltraClient().discover()) == 1


async def test_discover_host_input_and_resolution_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(UltraConnectionError, match="host is required"):
        await UltraClient().discover_host("  ")

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop,
        "getaddrinfo",
        AsyncMock(side_effect=OSError("resolution failed")),
    )
    with pytest.raises(UltraConnectionError, match="could not resolve"):
        await UltraClient().discover_host("ultra.local")


async def test_connect_retries_and_normalizes_unknown_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session_key = b"0123456789abcdef"
    send = AsyncMock(
        side_effect=[
            dna.DNAError("first variant failed"),
            b"\x00" * 4 + session_key + b"\x00" * 12,
        ]
    )
    monkeypatch.setattr(dna, "send_encrypted", send)
    unknown = device(type_id=0, name="")

    session = await UltraClient().connect(unknown)

    assert session.session_key == session_key
    assert session.auth_device_type == TYPE_ULTRA2_LAN
    assert unknown.type_id == TYPE_ULTRA2_LAN
    assert unknown.name == "eMotion Ultra2"
    assert send.await_count == 2


async def test_connect_reports_all_auth_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dna,
        "send_encrypted",
        AsyncMock(side_effect=dna.DNAError("offline")),
    )
    with pytest.raises(UltraConnectionError, match="offline"):
        await UltraClient().connect(device())


async def test_subscribe_requires_key_and_wraps_protocol_errors() -> None:
    client = UltraClient()
    no_key = UltraSession(device=device())
    with pytest.raises(UltraAuthError, match="session key"):
        await client.subscribe_local_udp_push(no_key, 1234, 60)

    session = UltraSession(device=device(), session_key=b"0123456789abcdef")
    client.send_command = AsyncMock(return_value=b"bad")
    with pytest.raises(UltraProtocolError, match="too short"):
        await client.subscribe_local_udp_push(session, 1234, 60)


@pytest.mark.parametrize(
    ("changes", "match"),
    [
        ({"did": "wrong"}, "DID mismatch"),
        ({"status": True}, "read failed"),
        ({"status": 1}, "read failed"),
        ({"level_of_sensitivity": None}, "missing radar"),
        ({"level_of_sensitivity": True}, "invalid radar"),
        ({"level_of_sensitivity": 3}, "invalid radar"),
        ({"height": -1}, "invalid radar height"),
        ({"install_direction": 256}, "invalid radar install_direction"),
        ({"z_range": []}, "invalid radar z_range"),
        ({"z_range": {"min": "bad", "max": 1}}, "invalid radar z_range.min"),
        ({"z_range": {"min": 1, "max": 1}}, "minimum"),
        ({"z_range": {"min": 0, "max": math.inf}}, "invalid radar z_range.max"),
    ],
)
async def test_get_radar_status_rejects_invalid_responses(
    changes: dict[str, object],
    match: str,
) -> None:
    client = UltraClient()
    client.send_command = AsyncMock(return_value=radar_response(**changes))
    with pytest.raises(UltraProtocolError, match=match):
        await client.get_radar_status(UltraSession(device=device(), session_key=b"0123456789abcdef"))


async def test_get_radar_status_wraps_invalid_subdevice_frame() -> None:
    client = UltraClient()
    client.send_command = AsyncMock(return_value=b"bad")
    with pytest.raises(UltraProtocolError, match="too short"):
        await client.get_radar_status(UltraSession(device=device(), session_key=b"0123456789abcdef"))


async def test_get_radar_status_allows_absent_optional_fields() -> None:
    client = UltraClient()
    client.send_command = AsyncMock(return_value=radar_response())
    status = await client.get_radar_status(UltraSession(device=device(), session_key=b"0123456789abcdef"))
    assert status.trigger_speed is None
    assert status.z_range is None


@pytest.mark.parametrize(
    ("method", "arguments", "match"),
    [
        ("set_radar_trigger_speed", (3,), "0, 1, or 2"),
        ("set_radar_install_mode", (2,), "0 or 1"),
        ("set_radar_height", (-1,), "between"),
        ("set_radar_install_direction", (2,), "0 or 1"),
        ("set_radar_z_range", (math.nan, 1), "between"),
        ("set_radar_z_range", (-1, math.inf), "between"),
        ("set_radar_default_absence_delay", (64801,), "between"),
        ("set_radar_zone_absence_delay", (0, 60), "between 1 and 4"),
        ("set_radar_zone_absence_delay", (1, -1), "between"),
    ],
)
async def test_radar_setter_validation(
    method: str,
    arguments: tuple[object, ...],
    match: str,
) -> None:
    client = UltraClient()
    session = UltraSession(device=device(), session_key=b"0123456789abcdef")
    with pytest.raises(ValueError, match=match):
        await getattr(client, method)(session, *arguments)


async def test_z_range_readback_mismatch() -> None:
    client = UltraClient()
    client.send_command = AsyncMock(return_value=b"ok")
    client.get_radar_status = AsyncMock(
        return_value=UltraRadarStatus(
            did=derive_ultra2_radar_did(MAC),
            sensitivity=2,
            received_at=client_module.datetime.now(client_module.UTC),
            z_range=UltraRadarZRange(-1, 1),
        )
    )
    with pytest.raises(UltraProtocolError, match="read-back mismatch"):
        await client.set_radar_z_range(
            UltraSession(device=device(), session_key=b"0123456789abcdef"),
            -2,
            2,
        )


async def test_send_command_requires_key_and_reports_transport_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = UltraClient(command_timeout=0.01)
    with pytest.raises(UltraAuthError, match="session key"):
        await client.send_command(UltraSession(device=device()), b"command")

    monkeypatch.setattr(
        dna,
        "send_encrypted",
        AsyncMock(side_effect=dna.DNAError("no response")),
    )
    with pytest.raises(UltraError, match="no response"):
        await client.send_command(
            UltraSession(device=device(), session_key=b"0123456789abcdef"),
            b"command",
        )


class DiscoveryServer(asyncio.DatagramProtocol):
    """Return a valid short discovery response for every request."""

    transport: asyncio.DatagramTransport

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        response = bytearray(dna.SHORT_DISCOVERY_SIZE)
        response[:8] = dna.MAGIC
        struct.pack_into("<H", response, 0x24, TYPE_ULTRA2)
        struct.pack_into("<H", response, 0x26, dna.MESSAGE_TYPE_DISCOVERY_RESPONSE)
        response[0x2A:0x30] = bytes.fromhex("e04b410244c7")
        self.transport.sendto(response, addr)


async def test_real_async_discovery_transport() -> None:
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        DiscoveryServer,
        local_addr=("127.0.0.1", 0),
    )
    try:
        port = transport.get_extra_info("sockname")[1]
        devices = await client_module._discover_dna_devices(
            broadcast_address="127.0.0.1",
            default_port=port,
            timeout=0.05,
        )
    finally:
        transport.close()

    assert len(devices) == 1
    assert devices[0].mac == MAC


async def test_discovery_timeout_validation() -> None:
    with pytest.raises(ValueError, match="greater than zero"):
        await client_module._discover_dna_devices(
            broadcast_address="127.0.0.1",
            default_port=80,
            timeout=0,
        )


def test_discovery_and_sequence_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    targets = client_module._discovery_targets("127.0.0.1", 80)
    assert targets[0][0] == "127.0.0.1"
    assert len(targets) == len(set(targets))

    class FailingSocket:
        def __enter__(self) -> FailingSocket:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def connect(self, _target: tuple[str, int]) -> None:
            raise OSError("no route")

    monkeypatch.setattr(socket, "socket", lambda *_args, **_kwargs: FailingSocket())
    assert client_module._outbound_ipv4() == "127.0.0.1"

    assert client_module._matches_ultra(device(type_id=0, pid=PID_ULTRA2.upper()))
    assert not client_module._matches_ultra(device(type_id=0, pid="other"))

    session = UltraSession(device=device(), command_sequence=0xFFFF)
    assert client_module._next_command_sequence(session) == 1
    assert client_module._dedupe_ints([1, 1, 2]) == [1, 2]
