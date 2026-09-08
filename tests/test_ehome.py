"""Tests for the eHome Modbus TCP client."""

from __future__ import annotations

import asyncio
import struct
import threading
from socket import socket

import pytest

from aiolinknlink.ehome import (
    REG_ABSENCE_DELAY,
    REG_SR3_BASE,
    EHomeClient,
    EHomeDevice,
    EHomeSession,
)


class _ModbusServer:
    def __init__(self) -> None:
        self.values = {
            29: 61350,
            REG_SR3_BASE: 235,
            REG_SR3_BASE + 1: 501,
            REG_SR3_BASE + 2: 120,
            REG_SR3_BASE + 3: 97,
            REG_SR3_BASE + 4: 1,
            REG_SR3_BASE + 5: 2,
            3000: 1,
            4000: 120,
            4001: 10,
            4002: 20,
            4003: 30,
            4004: 40,
            4005: 1,
            5000: 1,
            REG_ABSENCE_DELAY: 60,
            6000: 2350,
            6001: 5010,
            6002: 3000,
            6003: 1000,
            6004: 8000,
            6005: 2000,
            6006: 0,
            6007: 0,
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
                while not self.stop:
                    try:
                        header = _recv(conn, 7)
                    except OSError:
                        break
                    transaction, _, length, unit = struct.unpack(">HHHB", header)
                    assert unit == 1
                    pdu = _recv(conn, length - 1)
                    function = pdu[0]
                    address = struct.unpack(">H", pdu[1:3])[0]
                    count_or_value = struct.unpack(">H", pdu[3:5])[0]
                    if function == 3:
                        values = [self.values.get(address + index, 0) for index in range(count_or_value)]
                        response = bytes([3, count_or_value * 2]) + b"".join(
                            struct.pack(">H", value) for value in values
                        )
                    else:
                        self.values[address] = count_or_value
                        response = pdu
                    conn.sendall(struct.pack(">HHHB", transaction, 0, len(response) + 1, 1) + response)


def _recv(conn, size: int) -> bytes:
    value = bytearray()
    while len(value) < size:
        chunk = conn.recv(size - len(value))
        if not chunk:
            raise OSError("closed")
        value.extend(chunk)
    return bytes(value)


@pytest.mark.asyncio
async def test_read_state_and_write_confirmation() -> None:
    server = _ModbusServer()
    try:
        device = EHomeDevice("test", "127.0.0.1", server.port)
        client = EHomeClient()
        session = EHomeSession(device)
        state = await client.read_state(session)
        assert state.gateway_version == 61350
        assert state.sr3_temperature == 23.5
        assert state.sr3_humidity == 50.1
        assert state.sr3_occupied is True
        assert state.absence_delay == 60
        updated = await client.set_absence_delay(session, 120)
        assert updated.absence_delay == 120
    finally:
        server.close()


def test_invalid_write_value() -> None:
    with pytest.raises(ValueError):
        asyncio.run(EHomeClient().set_absence_delay(EHomeSession(EHomeDevice("x", "127.0.0.1")), -1))
