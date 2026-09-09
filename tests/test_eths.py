"""Tests for the eTHS Modbus TCP client."""

from __future__ import annotations

import asyncio
import struct
import threading
from socket import socket

import pytest

from aiolinknlink.eths import (
    EthsClient,
    EthsDevice,
    EthsSession,
    REG_HUMIDITY,
    REG_TEMPERATURE,
    REG_THRESHOLDS,
    REG_VERSION,
)


class _ModbusServer:
    def __init__(self) -> None:
        self.values = {
            REG_VERSION: 62215,
            REG_TEMPERATURE: 2479,
            REG_HUMIDITY: 5919,
            REG_THRESHOLDS: 12500,
            REG_THRESHOLDS + 1: 61536,
            REG_THRESHOLDS + 2: 10000,
            REG_THRESHOLDS + 3: 0,
            REG_THRESHOLDS + 4: 2,
            REG_THRESHOLDS + 5: 0,
            REG_THRESHOLDS + 6: 60,
        }
        self.sock = socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.closed = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return self.sock.getsockname()[1]

    def close(self) -> None:
        self.closed = True
        self.sock.close()
        self.thread.join(timeout=1)

    def _run(self) -> None:
        while not self.closed:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                while not self.closed:
                    try:
                        header = _recv(conn, 7)
                    except OSError:
                        break
                    transaction, _, length, unit = struct.unpack(">HHHB", header)
                    if unit != 1:
                        break
                    pdu = _recv(conn, length - 1)
                    function = pdu[0]
                    address = struct.unpack(">H", pdu[1:3])[0]
                    count = struct.unpack(">H", pdu[3:5])[0]
                    if function == 3:
                        body = bytes([3, count * 2]) + b"".join(
                            struct.pack(">H", self.values.get(address + index, 0))
                            for index in range(count)
                        )
                    elif function == 6:
                        self.values[address] = count
                        body = pdu
                    else:
                        break
                    conn.sendall(struct.pack(">HHHB", transaction, 0, len(body) + 1, 1) + body)


def _recv(conn, size: int) -> bytes:
    value = bytearray()
    while len(value) < size:
        chunk = conn.recv(size - len(value))
        if not chunk:
            raise OSError("closed")
        value.extend(chunk)
    return bytes(value)


@pytest.mark.asyncio
async def test_read_state_normalizes_scaled_values() -> None:
    server = _ModbusServer()
    try:
        device = EthsDevice("test", "127.0.0.1", server.port)
        client = EthsClient()
        state = await client.read_state(EthsSession(device))
        assert state.gateway_version == 62215
        assert state.temperature == 24.79
        assert state.humidity == 59.19
        assert state.max_temperature == 125
        assert state.min_temperature == -40
        assert state.absence_delay == 60
    finally:
        server.close()


def test_invalid_delay_is_rejected() -> None:
    with pytest.raises(ValueError):
        asyncio.run(EthsClient().set_absence_delay(EthsSession(EthsDevice("x", "127.0.0.1")), 0))
