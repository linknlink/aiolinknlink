"""Tests for DNA KeyValue application frames."""

from __future__ import annotations

import json
import struct

import pytest

from aiolinknlink.protocol import keyvalue


def test_get_and_set_frame_round_trip() -> None:
    get_frame = keyvalue.build_get_status_frame()
    command, payload = keyvalue.parse_frame(get_frame)
    assert command == keyvalue.CMD_GET_STATUS
    assert payload == b""

    set_frame = keyvalue.build_set_status_frame({"delaytime": 60})
    command, payload = keyvalue.parse_frame(set_frame)
    assert command == keyvalue.CMD_SET_STATUS
    assert json.loads(payload) == {"delaytime": 60}


def test_status_response() -> None:
    response = keyvalue.build_frame(
        keyvalue.CMD_STATUS_RESPONSE,
        b'{"tempsensor":235,"humsensor":48,"pir_detected":1}',
    )
    assert keyvalue.parse_status_response(response) == {
        "tempsensor": 235,
        "humsensor": 48,
        "pir_detected": 1,
    }


@pytest.mark.parametrize(
    ("frame", "match"),
    [
        (b"short", "too short"),
        (b"x" * keyvalue.UART_HEADER_SIZE, "header"),
        (
            keyvalue.build_frame(keyvalue.CMD_GET_STATUS, b"x")[:-1] + b"y",
            "checksum",
        ),
    ],
)
def test_invalid_frames(frame: bytes, match: str) -> None:
    with pytest.raises(keyvalue.KeyValueError, match=match):
        keyvalue.parse_frame(frame)


def test_frame_length_version_command_and_json_validation() -> None:
    too_long = bytearray(keyvalue.build_get_status_frame())
    struct.pack_into("<H", too_long, 8, 1)
    with pytest.raises(keyvalue.KeyValueError, match="exceeds"):
        keyvalue.parse_frame(bytes(too_long))

    version = bytearray(keyvalue.build_get_status_frame())
    struct.pack_into("<H", version, 10, 1)
    with pytest.raises(keyvalue.KeyValueError, match="version"):
        keyvalue.parse_frame(bytes(version))

    with pytest.raises(keyvalue.KeyValueError, match="unexpected"):
        keyvalue.parse_frame(
            keyvalue.build_get_status_frame(),
            expected_command=keyvalue.CMD_STATUS_RESPONSE,
        )
    with pytest.raises(keyvalue.KeyValueError, match="JSON"):
        keyvalue.parse_status_response(keyvalue.build_frame(keyvalue.CMD_STATUS_RESPONSE, b"bad"))
    with pytest.raises(keyvalue.KeyValueError, match="not an object"):
        keyvalue.parse_status_response(keyvalue.build_frame(keyvalue.CMD_STATUS_RESPONSE, b"[]"))


def test_builder_validation() -> None:
    with pytest.raises(ValueError, match="16 bits"):
        keyvalue.build_frame(0x10000)
    with pytest.raises(ValueError, match="too large"):
        keyvalue.build_frame(1, b"x" * 0x10000)
