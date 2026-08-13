"""iBG gateway UART command framing carried inside DNA packets."""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from . import dna

UART_MAGIC = 0x5A5AA5A5
UART_HEADER_SIZE = 12
UART_VERSION = 1

CMD_GET_STATUS = 0x0B01
CMD_SET_STATUS = 0x0B02
CMD_STATUS_RESPONSE = 0x0B03
CMD_GATEWAY_LIST = 0x0B0E
CMD_GATEWAY_LIST_RESPONSE = 0x0B0F


class GatewayProtocolError(dna.DNAError):
    """The iBG gateway returned an invalid UART command frame."""


@dataclass(frozen=True, slots=True)
class GatewayFrame:
    """Decoded iBG UART command frame."""

    command_type: int
    version: int
    payload: dict[str, Any]


def build_gateway_frame(command_type: int, payload: Mapping[str, object]) -> bytes:
    """Build a checksummed iBG UART JSON command frame."""
    if command_type < 0 or command_type > 0xFFFF:
        raise GatewayProtocolError(f"invalid command type: {command_type}")
    encoded = json.dumps(dict(payload), separators=(",", ":"), ensure_ascii=False).encode()
    if len(encoded) > 0xFFFF:
        raise GatewayProtocolError(f"gateway JSON payload too large: {len(encoded)}")
    frame = bytearray(struct.pack("<IHHHH", UART_MAGIC, 0, command_type, len(encoded), UART_VERSION) + encoded)
    dna.write_checksum_le(frame, 4)
    return bytes(frame)


def parse_gateway_frame(data: bytes) -> GatewayFrame:
    """Decode and verify an iBG UART JSON command frame."""
    if len(data) < UART_HEADER_SIZE:
        raise GatewayProtocolError(f"gateway frame too short: {len(data)}")
    magic, _checksum, command_type, payload_length, version = struct.unpack_from("<IHHHH", data, 0)
    if magic != UART_MAGIC:
        raise GatewayProtocolError("invalid gateway frame magic")
    expected_length = UART_HEADER_SIZE + payload_length
    if len(data) != expected_length:
        raise GatewayProtocolError(f"invalid gateway frame length: expected {expected_length}, got {len(data)}")
    if not dna.verify_checksum_le(data, 4):
        raise GatewayProtocolError("invalid gateway frame checksum")
    try:
        payload = json.loads(data[UART_HEADER_SIZE:].decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise GatewayProtocolError("invalid gateway JSON payload") from err
    if not isinstance(payload, dict):
        raise GatewayProtocolError("gateway JSON payload must be an object")
    return GatewayFrame(command_type=command_type, version=version, payload=payload)
