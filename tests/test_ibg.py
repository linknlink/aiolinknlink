"""Tests for the iBG local LAN client."""

from __future__ import annotations

import json
import struct

import pytest

from aiolinknlink import IbgClient, IbgDevice, IbgProtocolError, IbgSession, IbgSubDevice
from aiolinknlink.ibg import PID_SR3_SENSOR, normalize_subdevice_state
from aiolinknlink.protocol import dna, gateway

GATEWAY = IbgDevice(
    id="001122334455",
    ip="192.168.1.10",
    port=80,
    mac="00:11:22:33:44:55",
    type_id=0x2B71,
)
SESSION_KEY = b"0123456789abcdef"
SENSOR_DID = "00112233445566778899aabbccddeeff"


def _response_packet(request: bytes, key: bytes, response: bytes) -> bytes:
    header, _body = dna.parse_blc_packet(request)
    return dna.build_blc_packet(header, dna.build_blc_encrypted_payload(response, key))


def _entry(index: int, *, pid: str = PID_SR3_SENSOR, offline: int = 0) -> dict[str, object]:
    return {
        "did": f"{index:032x}",
        "pid": pid,
        "name": f"Sensor {index}",
        "offline": offline,
    }


async def test_connect_uses_compact_auth_and_extracts_session_key() -> None:
    seen_request = False

    async def exchange(
        _ip: str,
        _port: int,
        packet: bytes,
        _timeout: float,
        _accept: dna.PacketAcceptor | None,
    ) -> bytes:
        nonlocal seen_request
        seen_request = True
        header, body = dna.parse_blc_packet(packet)
        assert header.message_type == dna.MESSAGE_TYPE_AUTH
        assert header.device_type == GATEWAY.type_id
        _aes, auth = dna.parse_blc_encrypted_payload(body, dna.INITIAL_KEY)
        assert auth[4:28] == dna.mac_bytes(GATEWAY.mac) * 4
        return _response_packet(packet, dna.INITIAL_KEY, struct.pack("<I", 1) + SESSION_KEY)

    session = await IbgClient().connect(GATEWAY, exchange=exchange)

    assert seen_request
    assert session.session_key == SESSION_KEY
    assert session.last_auth_at is not None
    assert SESSION_KEY.hex() not in repr(session)


async def test_paginated_subdevice_list_and_safe_state() -> None:
    pages = [_entry(index) for index in range(21)]
    status_payload = {
        "status": 0,
        "did": SENSOR_DID,
        "pid": PID_SR3_SENSOR,
        "envtemp": 257,
        "envhumid": 524,
        "envlux": 126,
        "battery": 100,
        "pir_detected": 1,
        "password": "must-not-be-exposed",
        "network_key": "must-not-be-exposed",
    }

    async def exchange(
        _ip: str,
        _port: int,
        packet: bytes,
        _timeout: float,
        _accept: dna.PacketAcceptor | None,
    ) -> bytes:
        _header, body = dna.parse_blc_packet(packet)
        _aes, plain = dna.parse_blc_encrypted_payload(body, SESSION_KEY)
        frame = gateway.parse_gateway_frame(plain)
        if frame.command_type == gateway.CMD_GATEWAY_LIST:
            index = int(frame.payload["index"])
            response = gateway.build_gateway_frame(
                gateway.CMD_GATEWAY_LIST_RESPONSE,
                {"status": 0, "index": index, "total": len(pages), "list": pages[index : index + 10]},
            )
        else:
            assert frame.command_type == gateway.CMD_GET_STATUS
            response = gateway.build_gateway_frame(gateway.CMD_STATUS_RESPONSE, status_payload)
        return _response_packet(packet, SESSION_KEY, response)

    client = IbgClient()
    session = IbgSession(device=GATEWAY, session_key=SESSION_KEY, command_sequence=10)
    devices = await client.list_subdevices(session, exchange=exchange)
    assert len(devices) == 21

    requested = IbgSubDevice(SENSOR_DID, PID_SR3_SENSOR, "Room sensor", True)
    state = await client.get_subdevice_state(session, requested, exchange=exchange)
    assert state.values == {
        "temperature": 25.7,
        "humidity": 52.4,
        "illuminance": 126,
        "battery": 100,
        "occupancy": True,
    }
    assert "password" not in state.values
    assert "network_key" not in state.values


def test_state_normalization_rejects_unreviewed_and_invalid_values() -> None:
    assert normalize_subdevice_state("0" * 32, {"envtemp": 230}) == {}
    values = normalize_subdevice_state(
        PID_SR3_SENSOR,
        {
            "envtemp": True,
            "envhumid": 1001,
            "envlux": -1,
            "battery": 101,
            "pir_detected": 3,
            "mqtt_password": "secret",
        },
    )
    assert values == {}


async def test_unsupported_pid_is_not_queried() -> None:
    device = IbgSubDevice(SENSOR_DID, "0" * 32, "Unknown", True)
    with pytest.raises(IbgProtocolError, match="unsupported"):
        await IbgClient().get_subdevice_state(
            IbgSession(device=GATEWAY, session_key=SESSION_KEY),
            device,
        )


def test_gateway_frame_round_trip_and_validation() -> None:
    frame = gateway.build_gateway_frame(gateway.CMD_GATEWAY_LIST, {"count": 10, "index": 0})
    parsed = gateway.parse_gateway_frame(frame)
    assert parsed.command_type == gateway.CMD_GATEWAY_LIST
    assert parsed.payload == {"count": 10, "index": 0}

    corrupt = bytearray(frame)
    corrupt[-1] ^= 1
    with pytest.raises(gateway.GatewayProtocolError, match="checksum"):
        gateway.parse_gateway_frame(bytes(corrupt))

    non_object = json.dumps([1, 2]).encode()
    raw = bytearray(struct.pack("<IHHHH", gateway.UART_MAGIC, 0, 1, len(non_object), 1) + non_object)
    dna.write_checksum_le(raw, 4)
    with pytest.raises(gateway.GatewayProtocolError, match="object"):
        gateway.parse_gateway_frame(bytes(raw))
