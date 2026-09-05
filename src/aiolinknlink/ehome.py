"""Local Modbus TCP client for LinknLink eHome/EHUB devices."""

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
REG_TEMPERATURE = 300
REG_HUMIDITY = 316
REG_PRESENCE = 802
REG_ABSENCE_DELAY = 832


class EHomeError(Exception):
    """Base eHome error."""


class EHomeConnectionError(EHomeError):
    """The eHome TCP endpoint could not be reached."""


class EHomeProtocolError(EHomeError):
    """The eHome Modbus response was invalid."""


@dataclass(slots=True)
class EHomeDevice:
    """An eHome device reachable over Modbus TCP."""

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
    """Validated eHome sensor state."""

    temperature: float
    humidity: float
    occupied: bool
    absence_delay: int
    received_at: datetime


class EHomeClient:
    """Read and configure eHome through its documented Modbus map."""

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self.timeout = timeout

    async def discover_host(self, host: str, *, port: int = DEFAULT_PORT) -> EHomeDevice:
        """Probe a host and return an eHome device when Modbus responds."""
        host = host.strip()
        if not host:
            raise EHomeConnectionError("host is required")
        device = EHomeDevice(id=host, ip=host, port=port)
        session = await self.connect(device)
        await self.read_state(session)
        return device

    async def connect(self, device: EHomeDevice) -> EHomeSession:
        """Create a logical Modbus session after checking TCP reachability."""
        try:
            await asyncio.to_thread(_probe_tcp, device.ip, device.port, self.timeout)
        except (OSError, TimeoutError) as err:
            raise EHomeConnectionError(f"could not connect to {device.ip}:{device.port}") from err
        return EHomeSession(device=device, last_seen=datetime.now(UTC))

    async def close(self, session: EHomeSession) -> None:
        """Close is a no-op because requests use short-lived connections."""
        del session

    async def read_state(self, session: EHomeSession) -> EHomeState:
        """Read temperature, humidity, occupancy, and absence delay."""
        transaction_ids = [self._next_transaction(session) for _ in range(4)]
        values = await asyncio.to_thread(
            _modbus_read_registers,
            session.device.ip,
            session.device.port,
            transaction_ids,
            (REG_TEMPERATURE, REG_HUMIDITY, REG_PRESENCE, REG_ABSENCE_DELAY),
            self.timeout,
        )
        session.last_seen = datetime.now(UTC)
        return EHomeState(
            temperature=values[0] / 100,
            humidity=values[1] / 100,
            occupied=bool(values[2]),
            absence_delay=values[3],
            received_at=datetime.now(UTC),
        )

    async def read_register(self, session: EHomeSession, address: int) -> int:
        """Read one register using Modbus function 03."""
        return await asyncio.to_thread(
            _modbus_read_register,
            session.device.ip,
            session.device.port,
            self._next_transaction(session),
            address,
            self.timeout,
        )

    async def set_absence_delay(self, session: EHomeSession, seconds: int) -> EHomeState:
        """Write the absence delay register and return confirmed state."""
        if isinstance(seconds, bool) or not 0 <= seconds <= 0xFFFF:
            raise ValueError("absence delay must be between 0 and 65535 seconds")
        transaction_ids = [self._next_transaction(session) for _ in range(5)]
        values = await asyncio.to_thread(
            _modbus_write_and_read_state,
            session.device.ip,
            session.device.port,
            transaction_ids,
            seconds,
            self.timeout,
        )
        state = EHomeState(
            temperature=values[0] / 100,
            humidity=values[1] / 100,
            occupied=bool(values[2]),
            absence_delay=values[3],
            received_at=datetime.now(UTC),
        )
        session.last_seen = state.received_at
        if state.absence_delay != seconds:
            raise EHomeProtocolError(f"absence delay read-back mismatch ({state.absence_delay}, expected {seconds})")
        return state

    @staticmethod
    def _next_transaction(session: EHomeSession) -> int:
        session.transaction_id = session.transaction_id % 0xFFFF + 1
        return session.transaction_id


def _probe_tcp(host: str, port: int, timeout: float) -> None:
    with socket.create_connection((host, port), timeout=timeout):
        return


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


def _modbus_read_register(host: str, port: int, transaction_id: int, address: int, timeout: float) -> int:
    if not 0 <= address <= 0xFFFF:
        raise ValueError("register address is out of range")
    response = _request(host, port, transaction_id, struct.pack(">BHH", 3, address, 1), timeout)
    if len(response) != 4 or response[0] != 3 or response[1] != 2:
        raise EHomeProtocolError("invalid Modbus read response")
    return struct.unpack(">H", response[2:4])[0]


def _modbus_read_registers(
    host: str,
    port: int,
    transaction_ids: list[int],
    addresses: tuple[int, ...],
    timeout: float,
) -> list[int]:
    responses = _exchange(
        host,
        port,
        [
            (transaction_id, struct.pack(">BHH", 3, address, 1))
            for transaction_id, address in zip(transaction_ids, addresses, strict=True)
        ],
        timeout,
    )
    values: list[int] = []
    for response in responses:
        if len(response) != 4 or response[0] != 3 or response[1] != 2:
            raise EHomeProtocolError("invalid Modbus read response")
        values.append(struct.unpack(">H", response[2:4])[0])
    return values


def _modbus_write_and_read_state(
    host: str,
    port: int,
    transaction_ids: list[int],
    value: int,
    timeout: float,
) -> list[int]:
    addresses = (REG_TEMPERATURE, REG_HUMIDITY, REG_PRESENCE, REG_ABSENCE_DELAY)
    requests = [(transaction_ids[0], struct.pack(">BHH", 6, REG_ABSENCE_DELAY, value))]
    requests.extend(
        (transaction_id, struct.pack(">BHH", 3, address, 1))
        for transaction_id, address in zip(transaction_ids[1:], addresses, strict=True)
    )
    responses = _exchange(host, port, requests, timeout)
    response = responses[0]
    if len(response) != 5 or response != struct.pack(">BHH", 6, REG_ABSENCE_DELAY, value):
        raise EHomeProtocolError("invalid Modbus write response")
    values: list[int] = []
    for response in responses[1:]:
        if len(response) != 4 or response[0] != 3 or response[1] != 2:
            raise EHomeProtocolError("invalid Modbus read response")
        values.append(struct.unpack(">H", response[2:4])[0])
    return values


def _recv_exact(sock: socket.socket, size: int, timeout: float) -> bytes:
    sock.settimeout(timeout)
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise EHomeConnectionError("eHome closed the Modbus connection")
        data.extend(chunk)
    return bytes(data)
