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
