"""Tests for the asynchronous DNA UDP transport."""

from __future__ import annotations

import asyncio

import pytest

from aiolinknlink.protocol import dna


class _DNAServer(asyncio.DatagramProtocol):
    def __init__(self, key: bytes) -> None:
        self.key = key
        self.transport: asyncio.DatagramTransport | None = None
        self.request = b""

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        header, encrypted = dna.parse_blc_packet(data)
        _, self.request = dna.parse_blc_encrypted_payload(encrypted, self.key)
        response_payload = dna.build_blc_encrypted_payload(b"pong", self.key)
        assert self.transport is not None
        self.transport.sendto(dna.build_blc_packet(header, response_payload), addr)


class _StaleResponseDNAServer(_DNAServer):
    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        header, encrypted = dna.parse_blc_packet(data)
        _, self.request = dna.parse_blc_encrypted_payload(encrypted, self.key)
        assert self.transport is not None
        stale_sequence = header.sequence - 1 if header.sequence > 1 else header.sequence + 1
        stale_header = dna.NetworkHeader(
            device_type=header.device_type,
            message_type=header.message_type,
            sequence=stale_sequence,
            mac=header.mac,
        )
        self.transport.sendto(
            dna.build_blc_packet(
                stale_header,
                dna.build_blc_encrypted_payload(b"stale", self.key),
            ),
            addr,
        )
        self.transport.sendto(
            dna.build_blc_packet(
                header,
                dna.build_blc_encrypted_payload(b"pong", self.key),
            ),
            addr,
        )


async def test_send_encrypted_uses_async_udp() -> None:
    loop = asyncio.get_running_loop()
    key = b"0123456789abcdef"
    server = _DNAServer(key)
    transport, _ = await loop.create_datagram_endpoint(
        lambda: server,
        local_addr=("127.0.0.1", 0),
    )
    try:
        port = transport.get_extra_info("sockname")[1]
        response = await dna.send_encrypted(
            "127.0.0.1",
            port,
            dna.NetworkHeader(message_type=dna.MESSAGE_TYPE_COMMAND),
            b"ping",
            key,
            timeout=1,
        )
    finally:
        transport.close()

    assert server.request == b"ping"
    assert response == b"pong"


async def test_send_encrypted_times_out() -> None:
    with pytest.raises(dna.DNAError, match="timeout"):
        await dna.send_encrypted(
            "127.0.0.1",
            9,
            dna.NetworkHeader(message_type=dna.MESSAGE_TYPE_COMMAND),
            b"ping",
            b"0123456789abcdef",
            timeout=0.01,
        )


async def test_send_encrypted_ignores_stale_sequence() -> None:
    loop = asyncio.get_running_loop()
    key = b"0123456789abcdef"
    server = _StaleResponseDNAServer(key)
    transport, _ = await loop.create_datagram_endpoint(
        lambda: server,
        local_addr=("127.0.0.1", 0),
    )
    try:
        port = transport.get_extra_info("sockname")[1]
        response = await dna.send_encrypted(
            "127.0.0.1",
            port,
            dna.NetworkHeader(
                message_type=dna.MESSAGE_TYPE_COMMAND,
                sequence=42,
            ),
            b"ping",
            key,
            timeout=1,
        )
    finally:
        transport.close()

    assert response == b"pong"
