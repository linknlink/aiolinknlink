"""Tests for the persistent iBG local push transport."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from aiolinknlink import IbgClient, IbgDevice, IbgSession, IbgSubDevice
from aiolinknlink.ibg import PID_SR3_SENSOR
from aiolinknlink.ibg_push import (
    CMD_STATUS_RESPONSE_ACK,
    MESSAGE_TYPE_COMMAND_RESPONSE,
    MESSAGE_TYPE_HEARTBEAT_RESPONSE,
    IbgPushProtocol,
    IbgPushSubscription,
    _is_valid_heartbeat_response,
)
from aiolinknlink.protocol import dna, gateway

GATEWAY = IbgDevice(
    id="001122334455",
    ip="192.168.1.10",
    port=80,
    mac="00:11:22:33:44:55",
    type_id=0x2B71,
)
SESSION_KEY = b"0123456789abcdef"
SUBDEVICE = IbgSubDevice(
    did="00112233445566778899aabbccddeeff",
    pid=PID_SR3_SENSOR,
    name="Room sensor",
    online=True,
)


class FakeTransport:
    """Small datagram transport double that records packets."""

    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((bytes(data), addr))

    def get_extra_info(self, name: str) -> Any:
        return ("0.0.0.0", 54321) if name == "sockname" else None

    def close(self) -> None:
        return


def _packet(message_type: int, sequence: int, frame: bytes) -> bytes:
    header = dna.NetworkHeader(
        device_type=GATEWAY.type_id,
        message_type=message_type,
        sequence=sequence,
        mac=dna.mac_bytes(GATEWAY.mac),
        payload_checksum=dna.payload_checksum(frame),
    )
    return dna.build_blc_packet(header, dna.build_blc_encrypted_payload(frame, SESSION_KEY))


async def test_protocol_decodes_push_and_sends_ack() -> None:
    states = []
    protocol = IbgPushProtocol(
        asyncio.get_running_loop(),
        IbgSession(GATEWAY, SESSION_KEY),
        states.append,
        {SUBDEVICE.did: SUBDEVICE},
    )
    transport = FakeTransport()
    protocol.connection_made(transport)  # type: ignore[arg-type]
    push = gateway.build_gateway_frame(
        gateway.CMD_STATUS_RESPONSE,
        {
            "did": SUBDEVICE.did,
            "pid": SUBDEVICE.pid,
            "envtemp": 251,
            "keypressed": 1,
        },
    )

    protocol.datagram_received(
        _packet(MESSAGE_TYPE_COMMAND_RESPONSE, 77, push),
        (GATEWAY.ip, GATEWAY.port),
    )

    assert states[0].values == {"temperature": 25.1, "keypressed": 1}
    assert states[0].device is SUBDEVICE
    assert len(transport.sent) == 1
    ack_packet, ack_addr = transport.sent[0]
    ack_header, ack_body = dna.parse_blc_packet(ack_packet)
    _aes, ack_plain = dna.parse_blc_encrypted_payload(ack_body, SESSION_KEY)
    ack = gateway.parse_gateway_frame(ack_plain)
    assert ack_header.sequence == 77
    assert ack_header.message_type == dna.MESSAGE_TYPE_COMMAND
    assert ack.command_type == CMD_STATUS_RESPONSE_ACK
    assert ack_addr == (GATEWAY.ip, GATEWAY.port)


async def test_protocol_ignores_untrusted_or_mismatched_pushes() -> None:
    states = []
    protocol = IbgPushProtocol(
        asyncio.get_running_loop(),
        IbgSession(GATEWAY, SESSION_KEY),
        states.append,
        {SUBDEVICE.did: SUBDEVICE},
    )
    transport = FakeTransport()
    protocol.connection_made(transport)  # type: ignore[arg-type]
    wrong_pid = gateway.build_gateway_frame(
        gateway.CMD_STATUS_RESPONSE,
        {"did": SUBDEVICE.did, "pid": "0" * 32, "keypressed": 1},
    )
    packet = _packet(MESSAGE_TYPE_COMMAND_RESPONSE, 1, wrong_pid)

    protocol.datagram_received(packet, ("192.168.1.99", 80))
    protocol.datagram_received(packet, (GATEWAY.ip, 80))
    protocol.datagram_received(b"invalid", (GATEWAY.ip, 80))

    assert states == []
    assert len(transport.sent) == 1  # Valid authenticated status frames are ACKed before identity filtering.


async def test_protocol_exchange_routes_only_matching_response() -> None:
    protocol = IbgPushProtocol(
        asyncio.get_running_loop(),
        IbgSession(GATEWAY, SESSION_KEY),
        lambda _state: None,
        {},
    )
    transport = FakeTransport()
    protocol.connection_made(transport)  # type: ignore[arg-type]
    response = _packet(
        MESSAGE_TYPE_HEARTBEAT_RESPONSE,
        9,
        gateway.build_gateway_frame(0, {}),
    )
    task = asyncio.create_task(
        protocol.exchange(GATEWAY.ip, GATEWAY.port, b"request", 1, lambda data: data == response)
    )
    await asyncio.sleep(0)
    protocol.datagram_received(b"other", (GATEWAY.ip, 80))
    protocol.datagram_received(response, (GATEWAY.ip, 80))

    assert await task == response
    assert transport.sent == [(b"request", (GATEWAY.ip, GATEWAY.port))]


async def test_subscription_heartbeat_uses_persistent_socket() -> None:
    session = IbgSession(GATEWAY, SESSION_KEY, command_sequence=8)
    subscription = IbgPushSubscription(IbgClient(), session, [SUBDEVICE], lambda _state: None)

    async def exchange(
        _ip: str,
        _port: int,
        packet: bytes,
        _timeout: float,
        accept: dna.PacketAcceptor | None,
    ) -> bytes:
        header, body = dna.parse_blc_packet(packet)
        _aes, plain = dna.parse_blc_encrypted_payload(body, SESSION_KEY)
        frame = gateway.parse_gateway_frame(plain)
        assert frame.command_type == 0
        response = _packet(
            MESSAGE_TYPE_HEARTBEAT_RESPONSE,
            header.sequence,
            gateway.build_gateway_frame(0, {}),
        )
        assert accept is not None and accept(response)
        return response

    protocol = IbgPushProtocol(
        asyncio.get_running_loop(),
        session,
        lambda _state: None,
        {SUBDEVICE.did: SUBDEVICE},
    )
    protocol.exchange = exchange  # type: ignore[method-assign]
    subscription._protocol = protocol

    await subscription._heartbeat()

    assert session.command_sequence == 9
    assert session.last_seen is not None


async def test_subscription_reauthentication_reuses_locked_gateway_key() -> None:
    client = IbgClient()
    client.connect = AsyncMock(  # type: ignore[method-assign]
        return_value=IbgSession(GATEWAY, SESSION_KEY)
    )
    subscription = IbgPushSubscription(
        client,
        IbgSession(GATEWAY, SESSION_KEY),
        [SUBDEVICE],
        lambda _state: None,
        local_key=SESSION_KEY,
    )
    protocol = IbgPushProtocol(
        asyncio.get_running_loop(),
        subscription.session,
        lambda _state: None,
        {SUBDEVICE.did: SUBDEVICE},
    )
    subscription._protocol = protocol

    await subscription._reauthenticate()

    client.connect.assert_awaited_once_with(
        GATEWAY,
        local_key=SESSION_KEY,
        exchange=protocol.exchange,
    )


def test_heartbeat_accepts_firmware_length_including_zero_padding() -> None:
    response = bytearray(gateway.build_gateway_frame(0, {}))
    response.extend(b"\x00\x00")
    response[8:10] = (4).to_bytes(2, "little")
    dna.write_checksum_le(response, 4)
    assert _is_valid_heartbeat_response(bytes(response[:-2]))
    assert not _is_valid_heartbeat_response(bytes(response[:-3]))
    response[6:8] = (1).to_bytes(2, "little")
    assert not _is_valid_heartbeat_response(bytes(response))


async def test_subscription_start_stop_and_validation() -> None:
    with pytest.raises(ValueError, match="heartbeat_interval"):
        IbgPushSubscription(
            IbgClient(), IbgSession(GATEWAY, SESSION_KEY), [], lambda _state: None, heartbeat_interval=0
        )
    with pytest.raises(ValueError, match="retry_interval"):
        IbgPushSubscription(IbgClient(), IbgSession(GATEWAY, SESSION_KEY), [], lambda _state: None, retry_interval=0)

    subscription = IbgPushSubscription(
        IbgClient(command_timeout=0.05),
        IbgSession(GATEWAY, SESSION_KEY),
        [SUBDEVICE],
        lambda _state: None,
        heartbeat_interval=60,
    )
    await subscription.start()
    assert subscription.local_port > 0
    subscription.update_devices([])
    replacement = IbgSession(GATEWAY, SESSION_KEY)
    subscription.update_session(replacement)
    assert subscription.session is replacement
    await subscription.stop()
    assert not subscription.subscribed
    assert subscription.local_port == 0
