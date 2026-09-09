"""Asynchronous Modbus TCP client for LinknLink eTHS sensors."""

from __future__ import annotations

import asyncio
import socket
import struct
import time
from dataclasses import dataclass
from datetime import UTC, datetime

DEFAULT_PORT = 502
DEFAULT_TIMEOUT = 5.0
PID_ETHS = "0000000000000000000000007cac0000"
DISPLAY_MODEL_ETHS = "eTHS"
REG_VERSION = 29
REG_TEMPERATURE = 300
REG_HUMIDITY = 316
REG_THRESHOLDS = 826
REG_THRESHOLD_COUNT = 7


class EthsError(Exception):
    """Base eTHS error."""


class EthsConnectionError(EthsError):
    """The eTHS TCP endpoint could not be reached."""


class EthsProtocolError(EthsError):
    """The eTHS Modbus response was invalid."""


@dataclass(slots=True)
class EthsDevice:
    """An eTHS sensor reachable over Modbus TCP."""

    id: str
    ip: str
    port: int = DEFAULT_PORT
    pid: str = PID_ETHS
    name: str = DISPLAY_MODEL_ETHS
    model: str = DISPLAY_MODEL_ETHS


@dataclass(slots=True)
class EthsSession:
    """An active eTHS Modbus session."""

    device: EthsDevice
    transaction_id: int = 0
    last_seen: datetime | None = None


@dataclass(frozen=True, slots=True)
class EthsState:
    """Latest eTHS environmental and alarm state."""

    gateway_version: int
    temperature: float
    humidity: float
    max_temperature: float
    min_temperature: float
    max_humidity: float
    min_humidity: float
    temperature_alarm: int
    humidity_alarm: int
    absence_delay: int
    received_at: datetime


class EthsClient:
    """Read and configure eTHS through its Modbus TCP register map."""

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self.timeout = timeout

    async def discover_host(self, host: str, *, port: int = DEFAULT_PORT) -> EthsDevice:
        """Probe a host and return an eTHS device when its map is plausible."""
        host = host.strip()
        if not host:
            raise EthsConnectionError("host is required")
        device = EthsDevice(id=host, ip=host, port=port)
        session = await self.connect(device)
        await self.read_state(session)
        return device

    async def connect(self, device: EthsDevice) -> EthsSession:
        """Create a logical session; each read owns one socket."""
        return EthsSession(device=device, last_seen=datetime.now(UTC))

    async def close(self, session: EthsSession) -> None:
        """Close is a no-op because each transaction owns its socket."""
        del session

    async def read_state(self, session: EthsSession) -> EthsState:
        """Read all supported eTHS fields over one TCP connection."""
        requests = (
            (REG_VERSION, 1),
            (REG_TEMPERATURE, 1),
            (REG_HUMIDITY, 1),
            (REG_THRESHOLDS, REG_THRESHOLD_COUNT),
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
        version = values[0][0]
        temperature = _signed(values[1][0]) / 100
        humidity = values[2][0] / 100
        thresholds = values[3]
        if not -40 <= temperature <= 125 or not 0 <= humidity <= 100:
            raise EthsProtocolError("eTHS temperature or humidity is outside valid range")
        session.last_seen = datetime.now(UTC)
        return EthsState(
            gateway_version=version,
            temperature=temperature,
            humidity=humidity,
            max_temperature=_signed(thresholds[0]) / 100,
            min_temperature=_signed(thresholds[1]) / 100,
            max_humidity=thresholds[2] / 100,
            min_humidity=thresholds[3] / 100,
            temperature_alarm=thresholds[4],
            humidity_alarm=thresholds[5],
            absence_delay=thresholds[6],
            received_at=datetime.now(UTC),
        )

    async def set_temperature_limits(
        self,
        session: EthsSession,
        *,
        maximum: float | None = None,
        minimum: float | None = None,
    ) -> EthsState:
        """Write temperature limits in hundredths of a degree and verify."""
        writes: list[tuple[int, int]] = []
        if maximum is not None:
            writes.append((826, _scaled_signed(maximum, -40, 125)))
        if minimum is not None:
            writes.append((827, _scaled_signed(minimum, -40, 125)))
        await self._write_registers(session, writes)
        state = await self.read_state(session)
        if maximum is not None and state.max_temperature != maximum:
            raise EthsProtocolError("maximum temperature read-back mismatch")
        if minimum is not None and state.min_temperature != minimum:
            raise EthsProtocolError("minimum temperature read-back mismatch")
        return state

    async def set_humidity_limits(
        self,
        session: EthsSession,
        *,
        maximum: float | None = None,
        minimum: float | None = None,
    ) -> EthsState:
        """Write humidity limits in hundredths of a percent and verify."""
        writes: list[tuple[int, int]] = []
        if maximum is not None:
            writes.append((828, _scaled_unsigned(maximum, 0, 100)))
        if minimum is not None:
            writes.append((829, _scaled_unsigned(minimum, 0, 100)))
        await self._write_registers(session, writes)
        state = await self.read_state(session)
        if maximum is not None and state.max_humidity != maximum:
            raise EthsProtocolError("maximum humidity read-back mismatch")
        if minimum is not None and state.min_humidity != minimum:
            raise EthsProtocolError("minimum humidity read-back mismatch")
        return state

    async def set_absence_delay(self, session: EthsSession, seconds: int) -> EthsState:
        """Write the eTHS delay register and verify."""
        if isinstance(seconds, bool) or not 1 <= seconds <= 0xFFFF:
            raise ValueError("absence delay must be between 1 and 65535 seconds")
        await self._write_registers(session, [(832, seconds)])
        state = await self.read_state(session)
        if state.absence_delay != seconds:
            raise EthsProtocolError("absence delay read-back mismatch")
        return state

    async def _write_registers(self, session: EthsSession, writes: list[tuple[int, int]]) -> None:
        for address, value in writes:
            await asyncio.to_thread(
                _modbus_write_register,
                session.device.ip,
                session.device.port,
                self._next_transaction(session),
                address,
                value,
                self.timeout,
            )

    @staticmethod
    def _next_transaction(session: EthsSession) -> int:
        session.transaction_id = session.transaction_id % 0xFFFF + 1
        return session.transaction_id


def _signed(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def _scaled_signed(value: float, minimum: float, maximum: float) -> int:
    if not minimum <= value <= maximum:
        raise ValueError(f"value must be between {minimum} and {maximum}")
    scaled = round(value * 100)
    return scaled & 0xFFFF


def _scaled_unsigned(value: float, minimum: float, maximum: float) -> int:
    if not minimum <= value <= maximum:
        raise ValueError(f"value must be between {minimum} and {maximum}")
    return round(value * 100)


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
            raise EthsProtocolError("invalid Modbus read response")
        values.append([struct.unpack(">H", response[index : index + 2])[0] for index in range(2, len(response), 2)])
    return values


def _modbus_write_register(host: str, port: int, transaction_id: int, address: int, value: int, timeout: float) -> None:
    pdu = struct.pack(">BHH", 6, address, value)
    if _exchange(host, port, [(transaction_id, pdu)], timeout)[0] != pdu:
        raise EthsProtocolError("invalid Modbus write response")


def _exchange(host: str, port: int, requests: list[tuple[int, bytes]], timeout: float) -> list[bytes]:
    deadline = time.monotonic() + timeout
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except (OSError, TimeoutError) as err:
        raise EthsConnectionError(f"could not connect to {host}:{port}") from err
    responses: list[bytes] = []
    try:
        for transaction_id, pdu in requests:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EthsConnectionError(f"Modbus request to {host}:{port} timed out")
            packet = struct.pack(">HHH", transaction_id, 0, len(pdu) + 1) + bytes([1]) + pdu
            sock.settimeout(remaining)
            sock.sendall(packet)
            header = _recv_exact(sock, 7)
            rx_transaction, protocol, length, unit = struct.unpack(">HHHB", header)
            if rx_transaction != transaction_id or protocol != 0 or unit != 1 or length < 2:
                raise EthsProtocolError("invalid Modbus TCP header")
            responses.append(_recv_exact(sock, length - 1))
    except EthsError:
        raise
    except (OSError, TimeoutError) as err:
        raise EthsConnectionError(f"Modbus request to {host}:{port} failed") from err
    finally:
        sock.close()
    return responses


def _recv_exact(sock: socket.socket, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise EthsConnectionError("eTHS closed the Modbus connection")
        data.extend(chunk)
    return bytes(data)
