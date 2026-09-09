"""Tests for the eHub Modbus TCP client."""

from __future__ import annotations

import asyncio
import struct
import threading
from socket import socket

import pytest

from aiolinknlink.ehub import (
    REG_ABSENCE_DELAY,
    REG_HUMIDITY,
    REG_IP,
    REG_MAC,
    REG_PIR,
    REG_TEMPERATURE,
    REG_VERSION,
    EHubClient,
    EHubDevice,
    EHubSession,
)


class _ModbusServer:
    def __init__(self) -> None:
        self.values = {
            REG_MAC: 0xE04B,
            REG_MAC + 1: 0x4100,
            REG_MAC + 2: 0x5CCA,
            REG_IP: 0xC0A8,
            REG_IP + 1: 0x0351,
            REG_VERSION: 62106,
            REG_TEMPERATURE: 2443,
            REG_HUMIDITY: 6120,
            REG_PIR: 1,
            REG_ABSENCE_DELAY: 1,
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
                    elif function == 6:
                        self.values[address] = count_or_value
                        response = pdu
                    elif function == 16:
                        count = count_or_value
                        values = [
                            int.from_bytes(pdu[6 + index * 2 : 8 + index * 2], "big")
                            for index in range(count)
                        ]
                        for index, value in enumerate(values):
                            self.values[address + index] = value
                        response = pdu[:5]
                    else:
                        raise AssertionError(f"unexpected function {function}")
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
async def test_read_state_and_write_operations() -> None:
    server = _ModbusServer()
    try:
        device = EHubDevice("test", "127.0.0.1", server.port)
        client = EHubClient()
        session = EHubSession(device)
        state = await client.read_state(session)
        assert state.mac == "e0:4b:41:00:5c:ca"
        assert state.ip == "192.168.3.81"
        assert state.gateway_version == 62106
        assert state.temperature == 24.43
        assert state.humidity == 61.2
        assert state.occupied is True
        assert state.absence_delay == 1
        updated = await client.set_absence_delay(session, 5)
        assert updated.absence_delay == 5
        await client.send_infrared_raw(session, [100, 200])
        await client.send_rf_raw(session, [300, 400])
    finally:
        server.close()


def test_raw_code_validation() -> None:
    with pytest.raises(ValueError):
        asyncio.run(EHubClient().send_infrared_raw(EHubSession(EHubDevice("x", "127.0.0.1")), [0] * 123))
