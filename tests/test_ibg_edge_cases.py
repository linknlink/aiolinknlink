"""Edge-case and recovery tests for the iBG client."""

from __future__ import annotations

import asyncio
import struct

import pytest

import aiolinknlink.ibg as ibg_module
from aiolinknlink import (
    IbgClient,
    IbgConnectionError,
    IbgDevice,
    IbgProtocolError,
    IbgSession,
    IbgSubDevice,
)
from aiolinknlink.ibg import PID_BOX7_CONTROLLER, PID_SR3_SENSOR
from aiolinknlink.protocol import dna, gateway

GATEWAY = IbgDevice(
    id="001122334455",
    ip="192.168.1.10",
    port=80,
    mac="00:11:22:33:44:55",
    type_id=0x2B71,
)
SESSION_KEY = b"0123456789abcdef"
DID = "00112233445566778899aabbccddeeff"


def _session(*, sequence: int = 1) -> IbgSession:
    return IbgSession(device=GATEWAY, session_key=SESSION_KEY, command_sequence=sequence)


def _response(request: bytes, command: int, payload: dict[str, object]) -> bytes:
    header, _body = dna.parse_blc_packet(request)
    frame = gateway.build_gateway_frame(command, payload)
    return dna.build_blc_packet(header, dna.build_blc_encrypted_payload(frame, SESSION_KEY))


async def test_discover_filters_duplicates_and_non_ibg(monkeypatch: pytest.MonkeyPatch) -> None:
    async def discover(**_kwargs: object) -> list[dna.DiscoveredDevice]:
        return [
            dna.DiscoveredDevice("one", "192.168.1.10", 80, GATEWAY.mac, 0x2B71, name="IBG"),
            dna.DiscoveredDevice("duplicate", "192.168.1.11", 80, GATEWAY.mac, 0x2B71, name="iBG2 SE"),
            dna.DiscoveredDevice("other", "192.168.1.12", 80, name="Ultra2"),
        ]

    monkeypatch.setattr(ibg_module, "_discover_dna_devices", discover)

    devices = await IbgClient().discover()

    assert len(devices) == 1
    assert devices[0].id == GATEWAY.id
    assert devices[0].ip == GATEWAY.ip
    assert devices[0].name == "IBG"


async def test_discover_host_validates_and_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    client = IbgClient()
    with pytest.raises(IbgConnectionError, match="host is required"):
        await client.discover_host("  ")

    attempts = 0

    async def discover(self: IbgClient) -> list[IbgDevice]:
        nonlocal attempts
        attempts += 1
        return [] if attempts == 1 else [GATEWAY]

    monkeypatch.setattr(IbgClient, "discover", discover)
    assert await client.discover_host(GATEWAY.ip) == GATEWAY
    assert attempts == 2


async def test_connect_rejects_missing_mac_and_wraps_transport_error() -> None:
    missing_mac = IbgDevice("id", GATEWAY.ip, 80, type_id=GATEWAY.type_id)
    with pytest.raises(IbgConnectionError, match="valid MAC"):
        await IbgClient().connect(missing_mac)

    async def exchange(*_args: object) -> bytes:
        raise OSError("network unavailable")

    with pytest.raises(IbgConnectionError, match="network unavailable"):
        await IbgClient().connect(GATEWAY, exchange=exchange)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"status": 0, "total": True, "list": []}, "invalid total"),
        ({"status": 0, "total": 0, "list": {}}, "invalid list"),
        ({"status": 4, "total": 0, "list": []}, "status 4"),
    ],
)
async def test_list_rejects_invalid_gateway_responses(payload: dict[str, object], message: str) -> None:
    async def exchange(
        _ip: str,
        _port: int,
        packet: bytes,
        _timeout: float,
        _accept: dna.PacketAcceptor | None,
    ) -> bytes:
        return _response(packet, gateway.CMD_GATEWAY_LIST_RESPONSE, payload)

    with pytest.raises(IbgProtocolError, match=message):
        await IbgClient().list_subdevices(_session(), exchange=exchange)


async def test_list_rejects_stalled_page_and_invalid_entry() -> None:
    responses: list[dict[str, object]] = [
        {"status": 0, "total": 1, "list": []},
        {"status": 0, "total": 1, "list": [{"did": "bad", "pid": "bad"}]},
    ]

    for payload in responses:

        async def exchange(
            _ip: str,
            _port: int,
            packet: bytes,
            _timeout: float,
            _accept: dna.PacketAcceptor | None,
            response_payload: dict[str, object] = payload,
        ) -> bytes:
            return _response(packet, gateway.CMD_GATEWAY_LIST_RESPONSE, response_payload)

        with pytest.raises(IbgProtocolError):
            await IbgClient().list_subdevices(_session(), exchange=exchange)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"did": "f" * 32}, "identity"),
        ({"pid": "0" * 32}, "product identity"),
        ({"pid": 123}, "product identity"),
    ],
)
async def test_state_rejects_mismatched_identity(changes: dict[str, object], message: str) -> None:
    payload: dict[str, object] = {
        "status": 0,
        "did": DID,
        "pid": PID_SR3_SENSOR,
        "envtemp": 250,
    }
    payload.update(changes)

    async def exchange(
        _ip: str,
        _port: int,
        packet: bytes,
        _timeout: float,
        _accept: dna.PacketAcceptor | None,
    ) -> bytes:
        return _response(packet, gateway.CMD_STATUS_RESPONSE, payload)

    device = IbgSubDevice(DID, PID_SR3_SENSOR, "Sensor", True)
    with pytest.raises(IbgProtocolError, match=message):
        await IbgClient().get_subdevice_state(_session(), device, exchange=exchange)


async def test_state_read_isolates_offline_and_failing_devices() -> None:
    online = IbgSubDevice(DID, PID_SR3_SENSOR, "Online", True)
    offline = IbgSubDevice("1" * 32, PID_SR3_SENSOR, "Offline", False)
    unsupported = IbgSubDevice("2" * 32, "0" * 32, "Unknown", True)

    async def exchange(*_args: object) -> bytes:
        raise OSError("sensor did not answer")

    states = await IbgClient().read_supported_states(_session(), [online, offline, unsupported], exchange=exchange)

    assert states == {DID: None, offline.did: None}


async def test_set_state_requires_device_confirmation() -> None:
    device = IbgSubDevice(DID, PID_BOX7_CONTROLLER, "BOX7", True)

    async def exchange(
        _ip: str,
        _port: int,
        packet: bytes,
        _timeout: float,
        _accept: dna.PacketAcceptor | None,
    ) -> bytes:
        return _response(
            packet,
            gateway.CMD_STATUS_RESPONSE,
            {"did": DID, "pid": PID_BOX7_CONTROLLER, "pwr1": 0},
        )

    with pytest.raises(IbgProtocolError, match="did not confirm"):
        await IbgClient().set_subdevice_state(_session(), device, {"pwr1": True}, exchange=exchange)


async def test_command_requires_session_and_valid_response_type() -> None:
    client = IbgClient()
    with pytest.raises(IbgConnectionError, match="not authenticated"):
        await client.list_subdevices(IbgSession(device=GATEWAY))

    async def exchange(
        _ip: str,
        _port: int,
        packet: bytes,
        _timeout: float,
        _accept: dna.PacketAcceptor | None,
    ) -> bytes:
        return _response(packet, gateway.CMD_STATUS_RESPONSE, {"status": 0})

    with pytest.raises(IbgProtocolError, match="unexpected"):
        await client.list_subdevices(_session(sequence=0xFFFF), exchange=exchange)


def test_gateway_frame_validation_edges() -> None:
    with pytest.raises(gateway.GatewayProtocolError, match="command type"):
        gateway.build_gateway_frame(0x10000, {})
    with pytest.raises(gateway.GatewayProtocolError, match="too short"):
        gateway.parse_gateway_frame(b"short")

    valid = gateway.build_gateway_frame(gateway.CMD_GATEWAY_LIST, {})
    bad_magic = bytearray(valid)
    struct.pack_into("<I", bad_magic, 0, 0)
    with pytest.raises(gateway.GatewayProtocolError, match="magic"):
        gateway.parse_gateway_frame(bytes(bad_magic))
    with pytest.raises(gateway.GatewayProtocolError, match="length"):
        gateway.parse_gateway_frame(valid + b"extra")

    invalid_json = bytearray(struct.pack("<IHHHH", gateway.UART_MAGIC, 0, 1, 1, 1) + b"{")
    dna.write_checksum_le(invalid_json, 4)
    with pytest.raises(gateway.GatewayProtocolError, match="JSON"):
        gateway.parse_gateway_frame(bytes(invalid_json))


def test_sequence_wraps_without_zero() -> None:
    session = _session(sequence=0xFFFF)
    assert ibg_module._next_sequence(session) == 1


def test_async_edge_cases_without_pytest_plugin() -> None:
    """Keep a plugin-independent smoke path for constrained HA containers."""

    async def run() -> None:
        client = IbgClient()
        device = IbgSubDevice(DID, "0" * 32, "Unknown", True)
        with pytest.raises(IbgProtocolError):
            await client.get_subdevice_state(_session(), device)

    asyncio.run(run())
