"""Tests for the eHome Modbus TCP client."""

from __future__ import annotations

import asyncio
import struct
import threading
from socket import socket

import pytest

from aiolinknlink.ehome import (
    REG_ABSENCE_DELAY,
    REG_HUMIDITY,
    REG_PRESENCE,
    REG_TEMPERATURE,
    EHomeClient,
    EHomeDevice,
    EHomeSession,
    EHomeState,
)


class _ModbusServer:
    def __init__(self) -> None:
        self.values = {
            REG_TEMPERATURE: 2350,
            REG_HUMIDITY: 5010,
            REG_PRESENCE: 1,
            REG_ABSENCE_DELAY: 60,
        }
        self.sock = socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.stop = False
        self.thread.start()

    @property
    def port(self) -> int:
        return self.sock.getsockname()[1]

    def close(self) -> None:
        self.stop = True
        self.sock.close()
        self.thread.join(timeout=1)

    def _run(self) -> None:
        while not self.stop:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                header = _recv(conn, 7)
                transaction, _, length, unit = struct.unpack(">HHHB", header)
                assert unit == 1
                pdu = _recv(conn, length - 1)
                function, address, value = struct.unpack(">BHH", pdu)
                if function == 3:
                    response = struct.pack(">BBH", 3, 2, self.values[address])
                else:
                    self.values[address] = value
                    response = pdu
                conn.sendall(struct.pack(">HHHB", transaction, 0, len(response) + 1, 1) + response)


def _recv(conn: socket, size: int) -> bytes:
    value = bytearray()
    while len(value) < size:
        value.extend(conn.recv(size - len(value)))
    return bytes(value)


@pytest.mark.asyncio
async def test_read_state_and_write_confirmation() -> None:
    server = _ModbusServer()
    try:
        device = EHomeDevice("test", "127.0.0.1", server.port)
        client = EHomeClient()
        session = EHomeSession(device)
        state = await client.read_state(session)
        assert isinstance(state, EHomeState)
        assert state.temperature == 23.5
        assert state.humidity == 50.1
        assert state.occupied is True
        assert state.absence_delay == 60
        updated = await client.set_absence_delay(session, 120)
        assert updated.absence_delay == 120
    finally:
        server.close()


def test_invalid_write_value() -> None:
    with pytest.raises(ValueError):
        asyncio.run(EHomeClient().set_absence_delay(EHomeSession(EHomeDevice("x", "127.0.0.1")), -1))
