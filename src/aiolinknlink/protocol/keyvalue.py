"""DNA KeyValue application-frame helpers."""

from __future__ import annotations

import json
import struct
from typing import Any

UART_MAGIC = 0x5A5AA5A5
UART_HEADER_SIZE = 12
CHECKSUM_SEED = 0xBEAF
CMD_GET_STATUS = 0x0B01
CMD_SET_STATUS = 0x0B02
CMD_STATUS_RESPONSE = 0x0B03


class KeyValueError(Exception):
    """Invalid DNA KeyValue application frame."""


def build_get_status_frame() -> bytes:
    """Build a status query with an empty body."""
    return build_frame(CMD_GET_STATUS)


def build_set_status_frame(values: dict[str, Any]) -> bytes:
    """Build a compact JSON status update."""
    return build_frame(CMD_SET_STATUS, json.dumps(values, separators=(",", ":")).encode())


def build_frame(command: int, payload: bytes = b"") -> bytes:
    """Build a checksummed firmware UART frame."""
    if not 0 <= command <= 0xFFFF:
        raise ValueError("command must fit in 16 bits")
    if len(payload) > 0xFFFF:
        raise ValueError("payload is too large")
    frame = bytearray(UART_HEADER_SIZE + len(payload))
    struct.pack_into("<IHHHH", frame, 0, UART_MAGIC, 0, command, len(payload), 0)
    frame[UART_HEADER_SIZE:] = payload
    struct.pack_into("<H", frame, 4, checksum(frame))
    return bytes(frame)


def parse_frame(data: bytes, *, expected_command: int | None = None) -> tuple[int, bytes]:
    """Validate and return a firmware UART command and payload."""
    if len(data) < UART_HEADER_SIZE:
        raise KeyValueError(f"KeyValue frame too short: {len(data)}")
    magic, received_checksum, command, payload_length, version = struct.unpack_from("<IHHHH", data)
    if magic != UART_MAGIC:
        raise KeyValueError("invalid KeyValue frame header")
    if version != 0:
        raise KeyValueError(f"unsupported KeyValue frame version: {version}")
    frame_length = UART_HEADER_SIZE + payload_length
    if frame_length > len(data):
        raise KeyValueError("KeyValue payload exceeds frame length")
    if checksum(data[:frame_length], clear_checksum=True) != received_checksum:
        raise KeyValueError("invalid KeyValue frame checksum")
    if expected_command is not None and command != expected_command:
        raise KeyValueError(f"unexpected KeyValue response command: 0x{command:04x}")
    return command, bytes(data[UART_HEADER_SIZE:frame_length])


def parse_status_response(data: bytes) -> dict[str, Any]:
    """Parse a status response JSON object."""
    _, payload = parse_frame(data, expected_command=CMD_STATUS_RESPONSE)
    try:
        value = json.loads(payload.rstrip(b"\x00").decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise KeyValueError(f"invalid KeyValue JSON response: {err}") from err
    if not isinstance(value, dict):
        raise KeyValueError("KeyValue response is not an object")
    return value


def checksum(data: bytes | bytearray, *, clear_checksum: bool = False) -> int:
    """Calculate the firmware's additive frame checksum."""
    total = CHECKSUM_SEED
    for index, value in enumerate(data):
        if clear_checksum and index in {4, 5}:
            continue
        total = (total + value) & 0xFFFF
    return total
