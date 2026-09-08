"""Asynchronous Modbus TCP client for LinknLink eHome (433) gateways."""

from __future__ import annotations

import asyncio
import socket
import struct
import time
from dataclasses import dataclass
from datetime import UTC, datetime

DEFAULT_PORT = 502
DEFAULT_TIMEOUT = 5.0
PID_EHOME = "0000000000000000000000005f2b0000"
DISPLAY_MODEL_EHOME = "eHome"
REG_VERSION = 29
REG_SR3_BASE = 1000
REG_VIRTUAL_KEY_BASE = 3000
REG_VIRTUAL_LIGHT_BASE = 4000
REG_VIRTUAL_HUMAN_BASE = 5000
REG_VIRTUAL_SENSOR_BASE = 6000
REG_ABSENCE_DELAY = REG_VIRTUAL_HUMAN_BASE + 1


class EHomeError(Exception):
    """Base eHome error."""


class EHomeConnectionError(EHomeError):
    """The eHome TCP endpoint could not be reached."""


class EHomeProtocolError(EHomeError):
    """The eHome Modbus response was invalid."""


@dataclass(slots=True)
class EHomeDevice:
    """An eHome 433 gateway reachable over Modbus TCP."""

    id: str
    ip: str
    port: int = DEFAULT_PORT
    pid: str = PID_EHOME
    name: str = DISPLAY_MODEL_EHOME
    model: str = DISPLAY_MODEL_EHOME


@dataclass(slots=True)
class EHomeSession:
    """An active eHome Modbus TCP session."""

    device: EHomeDevice
    transaction_id: int = 0
    last_seen: datetime | None = None


@dataclass(frozen=True, slots=True)
class EHomeState:
    """Latest eHome gateway and generated virtual-device state."""

    gateway_version: int | None
    sr3_temperature: float | None
    sr3_humidity: float | None
    sr3_illuminance: int | None
    sr3_battery: int | None
    sr3_occupied: bool | None
    sr3_keypressed: int | None
    virtual_keypressed: int | None
    virtual_illuminance: int | None
    virtual_light_level_1: int | None
    virtual_light_level_2: int | None
    virtual_light_level_3: int | None
    virtual_light_level_4: int | None
    virtual_light_status: int | None
    virtual_occupied: bool | None
    absence_delay: int | None
    virtual_temperature: float | None
    virtual_humidity: float | None
    max_temperature: float | None
    min_temperature: float | None
    max_humidity: float | None
    min_humidity: float | None
    temperature_alarm: int | None
    humidity_alarm: int | None
    received_at: datetime


class EHomeClient:
    """Read and configure an eHome gateway through its Modbus map."""

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self.timeout = timeout

    async def discover_host(self, host: str, *, port: int = DEFAULT_PORT) -> EHomeDevice:
        """Probe a host and return an eHome gateway when Modbus responds."""
        host = host.strip()
        if not host:
            raise EHomeConnectionError("host is required")
        device = EHomeDevice(id=host, ip=host, port=port)
        session = await self.connect(device)
        await self.read_state(session)
        return device

    async def connect(self, device: EHomeDevice) -> EHomeSession:
        """Create a logical session.

        eHome firmware keeps the previous Modbus client slot briefly after a
        disconnect, so opening a probe socket here would race the real
        transaction.  The first read is the connectivity check.
        """
        return EHomeSession(device=device, last_seen=datetime.now(UTC))

    async def close(self, session: EHomeSession) -> None:
        """Close is a no-op because each transaction owns its socket."""
        del session

    async def read_state(self, session: EHomeSession) -> EHomeState:
        """Read gateway, SR3 and generated virtual-device registers."""
        requests = (
            (REG_VERSION, 1),
            (REG_SR3_BASE, 6),
            (REG_VIRTUAL_KEY_BASE, 1),
            (REG_VIRTUAL_LIGHT_BASE, 6),
            (REG_VIRTUAL_HUMAN_BASE, 2),
            (REG_VIRTUAL_SENSOR_BASE, 8),
        )
        transaction_ids = [self._next_transaction(session) for _ in requests]
        values = await asyncio.to_thread(
            _modbus_read_blocks,
            session.device.ip,
            session.device.port,
            transaction_ids,
            requests,
            self.timeout,
        )
        session.last_seen = datetime.now(UTC)
        sr3, light, human, sensor = values[1], values[3], values[4], values[5]
        return EHomeState(
            gateway_version=values[0][0],
            sr3_temperature=_signed(sr3[0]) / 10,
            sr3_humidity=sr3[1] / 10,
            sr3_illuminance=sr3[2],
            sr3_battery=sr3[3],
            sr3_occupied=bool(sr3[4]),
            sr3_keypressed=sr3[5],
            virtual_keypressed=values[2][0],
            virtual_illuminance=light[0],
            virtual_light_level_1=light[1],
            virtual_light_level_2=light[2],
            virtual_light_level_3=light[3],
            virtual_light_level_4=light[4],
            virtual_light_status=light[5],
            virtual_occupied=bool(human[0]),
            absence_delay=human[1],
            virtual_temperature=_signed(sensor[0]) / 100,
            virtual_humidity=sensor[1] / 100,
            max_temperature=_signed(sensor[2]) / 100,
            min_temperature=_signed(sensor[3]) / 100,
            max_humidity=sensor[4] / 100,
            min_humidity=sensor[5] / 100,
            temperature_alarm=sensor[6],
            humidity_alarm=sensor[7],
            received_at=datetime.now(UTC),
        )

    async def set_absence_delay(self, session: EHomeSession, minutes: int) -> EHomeState:
        """Write the generated virtual-human delay, measured in minutes."""
        if isinstance(minutes, bool) or not 0 <= minutes <= 0xFFFF:
            raise ValueError("absence delay must be between 0 and 65535 minutes")
        await asyncio.to_thread(
            _modbus_write_register,
            session.device.ip,
            session.device.port,
            self._next_transaction(session),
            REG_ABSENCE_DELAY,
            minutes,
            self.timeout,
        )
        state = await self.read_state(session)
        if state.absence_delay != minutes:
            raise EHomeProtocolError(f"absence delay read-back mismatch ({state.absence_delay}, expected {minutes})")
        return state

    @staticmethod
    def _next_transaction(session: EHomeSession) -> int:
        session.transaction_id = session.transaction_id % 0xFFFF + 1
        return session.transaction_id


def _signed(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def _probe_tcp(host: str, port: int, timeout: float) -> None:
    with socket.create_connection((host, port), timeout=timeout):
        return


def _modbus_read_blocks(
    host: str,
    port: int,
    transaction_ids: list[int],
    requests: tuple[tuple[int, int], ...],
    timeout: float,
) -> list[list[int]]:
    responses = _exchange(
        host,
        port,
        [
            (transaction_id, struct.pack(">BHH", 3, address, count))
            for transaction_id, (address, count) in zip(transaction_ids, requests, strict=True)
        ],
        timeout,
    )
    values: list[list[int]] = []
    for response, (_, count) in zip(responses, requests, strict=True):
        if len(response) != 2 + count * 2 or response[0] != 3 or response[1] != count * 2:
            raise EHomeProtocolError("invalid Modbus read response")
        values.append([struct.unpack(">H", response[index : index + 2])[0] for index in range(2, len(response), 2)])
    return values


def _modbus_write_register(host: str, port: int, transaction_id: int, address: int, value: int, timeout: float) -> None:
    pdu = struct.pack(">BHH", 6, address, value)
    if _request(host, port, transaction_id, pdu, timeout) != pdu:
        raise EHomeProtocolError("invalid Modbus write response")


def _request(host: str, port: int, transaction_id: int, pdu: bytes, timeout: float) -> bytes:
    return _exchange(host, port, [(transaction_id, pdu)], timeout)[0]


def _exchange(host: str, port: int, requests: list[tuple[int, bytes]], timeout: float) -> list[bytes]:
    deadline = time.monotonic() + timeout
    sock = _connect_with_retry(host, port, deadline)
    responses: list[bytes] = []
    try:
        for transaction_id, pdu in requests:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EHomeConnectionError(f"Modbus request to {host}:{port} timed out")
            packet = struct.pack(">HHH", transaction_id, 0, len(pdu) + 1) + bytes([1]) + pdu
            sock.sendall(packet)
            header = _recv_exact(sock, 7, remaining)
            rx_transaction, protocol, length, unit = struct.unpack(">HHHB", header)
            if rx_transaction != transaction_id or protocol != 0 or unit != 1 or length < 2:
                raise EHomeProtocolError("invalid Modbus TCP header")
            responses.append(_recv_exact(sock, length - 1, remaining))
    except EHomeError:
        raise
    except (OSError, TimeoutError) as err:
        raise EHomeConnectionError(f"Modbus request to {host}:{port} failed") from err
    finally:
        sock.close()
    return responses


def _connect_with_retry(host: str, port: int, deadline: float) -> socket.socket:
    last_error: OSError | None = None
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            return socket.create_connection((host, port), timeout=remaining)
        except OSError as err:
            last_error = err
            time.sleep(min(0.4, max(0.0, deadline - time.monotonic())))
    raise EHomeConnectionError(f"could not connect to {host}:{port}") from last_error


def _recv_exact(sock: socket.socket, size: int, timeout: float) -> bytes:
    sock.settimeout(timeout)
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise EHomeConnectionError("eHome closed the Modbus connection")
        data.extend(chunk)
    return bytes(data)
