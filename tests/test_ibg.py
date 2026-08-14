"""Tests for the iBG local LAN client."""

from __future__ import annotations

import json
import struct

import pytest

from aiolinknlink import (
    IbgClient,
    IbgConnectionError,
    IbgDevice,
    IbgProtocolError,
    IbgSession,
    IbgSubDevice,
)
from aiolinknlink.ibg import PID_BOX7_CONTROLLER, PID_SR3_SENSOR, normalize_subdevice_state
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
BOX7_DID = "00112233445566778899aabbccddeef0"


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


async def test_connect_accepts_previously_paired_local_key_without_auth_exchange() -> None:
    async def exchange(*_args: object) -> bytes:
        raise AssertionError("locked gateway must not be paired again")

    session = await IbgClient().connect(GATEWAY, local_key=SESSION_KEY, exchange=exchange)

    assert session.session_key == SESSION_KEY
    assert session.last_auth_at is not None

    with pytest.raises(IbgConnectionError, match="16 bytes"):
        await IbgClient().connect(GATEWAY, local_key=b"short")


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
        "keypressed": 2,
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
        "keypressed": 2,
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
            "keypressed": True,
            "mqtt_password": "secret",
        },
    )
    assert values == {}


def test_state_normalization_accepts_confirmed_key_values_only() -> None:
    assert normalize_subdevice_state(PID_SR3_SENSOR, {"keypressed": 1}) == {"keypressed": 1}
    assert normalize_subdevice_state(PID_SR3_SENSOR, {"keypressed": 2}) == {"keypressed": 2}
    assert normalize_subdevice_state(PID_SR3_SENSOR, {"keypressed": 0}) == {}


def test_box7_state_normalization_uses_reviewed_fields_and_scales() -> None:
    values = normalize_subdevice_state(
        PID_BOX7_CONTROLLER,
        {
            "pwr1": 1,
            "pwr2": 0,
            "pwr3": True,
            "pwr4": 2,
            "power": 12345,
            "totalconsum": 98765,
            "envtemp1": -5,
            "envtemp2": 128,
            "envtemp3": 129,
            "Aphasevolt": 2315,
            "Bphasevolt": True,
            "Cphasevolt": 2200,
            "Aphasecurrent": 1234,
            "Bphasecurrent": 999999,
            "Cphasecurrent": -1,
            "alarm_state": 1,
            "tempdif1": 8,
            "password": "must-not-be-exposed",
        },
    )

    assert values == {
        "pwr1": True,
        "pwr2": False,
        "pwr3": True,
        "power": 1234.5,
        "totalconsum": 987.65,
        "envtemp1": -5.0,
        "envtemp2": 128.0,
        "Aphasevolt": 231.5,
        "Cphasevolt": 220.0,
        "Aphasecurrent": 1.234,
        "Bphasecurrent": 999.999,
    }


async def test_box7_set_state_sends_only_reviewed_boolean_control() -> None:
    device = IbgSubDevice(BOX7_DID, PID_BOX7_CONTROLLER, "BOX7", True)

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
        assert frame.command_type == gateway.CMD_SET_STATUS
        assert frame.payload == {"did": BOX7_DID, "pwr3": 1}
        return _response_packet(
            packet,
            SESSION_KEY,
            gateway.build_gateway_frame(
                gateway.CMD_STATUS_RESPONSE,
                {
                    "did": BOX7_DID,
                    "pid": PID_BOX7_CONTROLLER,
                    "pwr1": 0,
                    "pwr3": 1,
                    "power": 120,
                },
            ),
        )

    state = await IbgClient().set_subdevice_state(
        IbgSession(device=GATEWAY, session_key=SESSION_KEY),
        device,
        {"pwr3": True},
        exchange=exchange,
    )

    assert state.values == {"pwr1": False, "pwr3": True, "power": 12.0}


@pytest.mark.parametrize(
    ("device", "changes", "message"),
    [
        (IbgSubDevice(BOX7_DID, PID_SR3_SENSOR, "Sensor", True), {"pwr1": True}, "unsupported writable"),
        (IbgSubDevice(BOX7_DID, PID_BOX7_CONTROLLER, "BOX7", False), {"pwr1": True}, "offline"),
        (IbgSubDevice(BOX7_DID, PID_BOX7_CONTROLLER, "BOX7", True), {}, "at least one"),
        (IbgSubDevice(BOX7_DID, PID_BOX7_CONTROLLER, "BOX7", True), {"alarm_state": True}, "field"),
        (IbgSubDevice(BOX7_DID, PID_BOX7_CONTROLLER, "BOX7", True), {"pwr1": 1}, "boolean"),
    ],
)
async def test_box7_set_state_rejects_unsafe_requests(
    device: IbgSubDevice,
    changes: dict[str, bool],
    message: str,
) -> None:
    with pytest.raises((IbgConnectionError, IbgProtocolError), match=message):
        await IbgClient().set_subdevice_state(
            IbgSession(device=GATEWAY, session_key=SESSION_KEY),
            device,
            changes,
        )


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
