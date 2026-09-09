"""Asynchronous Modbus TCP client for LinknLink eHub gateways."""

from __future__ import annotations

import asyncio
import socket
import struct
import time
from dataclasses import dataclass
from datetime import UTC, datetime

DEFAULT_PORT = 502
DEFAULT_TIMEOUT = 5.0
PID_EHUB = "0000000000000000000000000b520000"
DISPLAY_MODEL_EHUB = "eHub"

REG_MAC = 0
REG_IP = 8
REG_FACTORY_RESET = 12
REG_VERSION = 29
REG_TEMPERATURE = 300
REG_HUMIDITY = 316
REG_PIR = 802
REG_ABSENCE_DELAY = 832
REG_IR_SEND_TICK = 2001
REG_RF_SEND_TICK = 2003
REG_IR_SEND = 2005
REG_RF_SEND = 2128
MAX_RAW_CODE_WORDS = 122
RAW_CODE_REGISTER_COUNT = MAX_RAW_CODE_WORDS + 1


class EHubError(Exception):
    """Base eHub error."""


class EHubConnectionError(EHubError):
    """The eHub TCP endpoint could not be reached."""


class EHubProtocolError(EHubError):
    """The eHub Modbus response was invalid."""


@dataclass(slots=True)
class EHubDevice:
    """An eHub gateway reachable over Modbus TCP."""

    id: str
    ip: str
    port: int = DEFAULT_PORT
    pid: str = PID_EHUB
    name: str = DISPLAY_MODEL_EHUB
    model: str = DISPLAY_MODEL_EHUB


@dataclass(slots=True)
class EHubSession:
    """An active eHub Modbus session."""

    device: EHubDevice
    transaction_id: int = 0
    last_seen: datetime | None = None


@dataclass(frozen=True, slots=True)
class EHubState:
    """Latest eHub host state."""

    mac: str
    ip: str
    gateway_version: int
    temperature: float
    humidity: float
    occupied: bool
    absence_delay: int
    ir_send_tick: int
    rf_send_tick: int
    received_at: datetime


class EHubClient:
    """Read and control an eHub through its Modbus TCP register map."""

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be greater than zero")
        self.timeout = timeout

    async def discover_host(self, host: str, *, port: int = DEFAULT_PORT) -> EHubDevice:
        """Probe a host and return an eHub device when its map is valid."""
        host = host.strip()
        if not host:
            raise EHubConnectionError("host is required")
        device = EHubDevice(id=host, ip=host, port=port)
        session = await self.connect(device)
        await self.read_state(session)
        return device

    async def connect(self, device: EHubDevice) -> EHubSession:
        """Create a logical session; each operation owns one TCP socket."""
        return EHubSession(device=device, last_seen=datetime.now(UTC))

    async def close(self, session: EHubSession) -> None:
        """Close is a no-op because each transaction owns its socket."""
        del session

    async def read_state(self, session: EHubSession) -> EHubState:
        """Read the supported eHub host state."""
        requests = (
            (REG_MAC, 3),
            (REG_IP, 2),
            (REG_VERSION, 1),
            (REG_TEMPERATURE, 1),
            (REG_HUMIDITY, 1),
            (REG_PIR, 1),
            (REG_ABSENCE_DELAY, 1),
            (REG_IR_SEND_TICK, 2),
            (REG_RF_SEND_TICK, 2),
        )
        values = await asyncio.to_thread(
            _modbus_read_blocks,
            session.device.ip,
            session.device.port,
            [self._next_transaction(session) for _ in requests],
            requests,
            self.timeout,
        )
        temperature = _signed(values[3][0]) / 100
        humidity = values[4][0] / 100
        if not -40 <= temperature <= 125:
            raise EHubProtocolError("eHub temperature is outside valid range")
        if not 0 <= humidity <= 100:
            raise EHubProtocolError("eHub humidity is outside valid range")
        session.last_seen = datetime.now(UTC)
        return EHubState(
            mac=_decode_mac(values[0]),
            ip=_decode_ip(values[1]),
            gateway_version=values[2][0],
            temperature=temperature,
            humidity=humidity,
            occupied=bool(values[5][0]),
            absence_delay=values[6][0],
            ir_send_tick=_combine_u32(values[7]),
            rf_send_tick=_combine_u32(values[8]),
            received_at=datetime.now(UTC),
        )

    async def set_absence_delay(self, session: EHubSession, minutes: int) -> EHubState:
        """Set the no-person delay in minutes and verify the read-back."""
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
            raise EHubProtocolError("absence delay read-back mismatch")
        return state

    async def send_infrared_raw(self, session: EHubSession, raw: list[int] | tuple[int, ...]) -> None:
        """Send raw infrared pulse words through the Modbus data area."""
        await self._send_raw(session, REG_IR_SEND, raw)

    async def send_rf_raw(self, session: EHubSession, raw: list[int] | tuple[int, ...]) -> None:
        """Send raw 433 MHz pulse words through the Modbus data area."""
        await self._send_raw(session, REG_RF_SEND, raw)

    async def _send_raw(
        self,
        session: EHubSession,
        address: int,
        raw: list[int] | tuple[int, ...],
    ) -> None:
        words = _validate_raw_words(raw)
        await asyncio.to_thread(
            _modbus_write_registers,
            session.device.ip,
            session.device.port,
            self._next_transaction(session),
            address,
            [len(words), *words],
            self.timeout,
        )
        session.last_seen = datetime.now(UTC)

    @staticmethod
    def _next_transaction(session: EHubSession) -> int:
        session.transaction_id = session.transaction_id % 0xFFFF + 1
        return session.transaction_id


def _validate_raw_words(raw: list[int] | tuple[int, ...]) -> list[int]:
    if len(raw) > MAX_RAW_CODE_WORDS:
        raise ValueError(f"raw code must contain at most {MAX_RAW_CODE_WORDS} words")
    words = list(raw)
    if any(isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 0xFFFF for value in words):
        raise ValueError("raw code words must be unsigned 16-bit integers")
    return words


def _signed(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def _combine_u32(values: list[int]) -> int:
    if len(values) != 2:
        raise EHubProtocolError("invalid 32-bit register value")
    return (values[0] << 16) | values[1]


def _decode_mac(values: list[int]) -> str:
    if len(values) != 3:
        raise EHubProtocolError("invalid MAC register value")
    raw = b"".join(value.to_bytes(2, "big") for value in values)
    return ":".join(f"{byte:02x}" for byte in raw)


def _decode_ip(values: list[int]) -> str:
    if len(values) != 2:
        raise EHubProtocolError("invalid IP register value")
    raw = b"".join(value.to_bytes(2, "big") for value in values)
    return ".".join(str(byte) for byte in raw)


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
            raise EHubProtocolError("invalid Modbus read response")
        values.append([struct.unpack(">H", response[index : index + 2])[0] for index in range(2, len(response), 2)])
    return values


def _modbus_write_register(
    host: str,
    port: int,
    transaction_id: int,
    address: int,
    value: int,
    timeout: float,
) -> None:
    pdu = struct.pack(">BHH", 6, address, value)
    if _exchange(host, port, [(transaction_id, pdu)], timeout)[0] != pdu:
        raise EHubProtocolError("invalid Modbus write response")


def _modbus_write_registers(
    host: str,
    port: int,
    transaction_id: int,
    address: int,
    values: list[int],
    timeout: float,
) -> None:
    if not 1 <= len(values) <= RAW_CODE_REGISTER_COUNT:
        raise ValueError("invalid Modbus register write length")
    pdu = struct.pack(">BHHB", 16, address, len(values), len(values) * 2) + b"".join(
        struct.pack(">H", value) for value in values
    )
    response = _exchange(host, port, [(transaction_id, pdu)], timeout)[0]
    if response != struct.pack(">BHH", 16, address, len(values)):
        raise EHubProtocolError("invalid Modbus multiple-write response")


def _exchange(host: str, port: int, requests: list[tuple[int, bytes]], timeout: float) -> list[bytes]:
    deadline = time.monotonic() + timeout
    sock = _connect_with_retry(host, port, deadline)
    responses: list[bytes] = []
    try:
        for transaction_id, pdu in requests:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise EHubConnectionError(f"Modbus request to {host}:{port} timed out")
            packet = struct.pack(">HHH", transaction_id, 0, len(pdu) + 1) + bytes([1]) + pdu
            sock.sendall(packet)
            header = _recv_exact(sock, 7, remaining)
            rx_transaction, protocol, length, unit = struct.unpack(">HHHB", header)
            if rx_transaction != transaction_id or protocol != 0 or unit != 1 or length < 2:
                raise EHubProtocolError("invalid Modbus TCP header")
            response = _recv_exact(sock, length - 1, remaining)
            if response and response[0] & 0x80:
                raise EHubProtocolError(f"Modbus exception response: {response.hex()}")
            responses.append(response)
    except EHubError:
        raise
    except (OSError, TimeoutError) as err:
        raise EHubConnectionError(f"Modbus request to {host}:{port} failed") from err
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
    raise EHubConnectionError(f"could not connect to {host}:{port}") from last_error


def _recv_exact(sock: socket.socket, size: int, timeout: float) -> bytes:
    sock.settimeout(timeout)
    data = bytearray()
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            raise EHubConnectionError("eHub closed the Modbus connection")
        data.extend(chunk)
    return bytes(data)
