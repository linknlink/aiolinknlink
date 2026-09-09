"""Tests for the local infrared remote protocol."""

from __future__ import annotations

import base64
import struct

import pytest

from aiolinknlink.remote import (
    _parse_response,
    _require_success,
    _validate_ir_code,
)

IR_CODE = base64.b64encode(bytes([38, 0]) + struct.pack("<H", 3) + b"abc").decode()


def test_validate_ir_code_accepts_38khz_frame() -> None:
    _validate_ir_code(IR_CODE)


@pytest.mark.parametrize("value", ["", "not-base64", base64.b64encode(b"\x26").decode()])
def test_validate_ir_code_rejects_invalid_frame(value: str) -> None:
    with pytest.raises(ValueError):
        _validate_ir_code(value)


def test_parse_response_returns_result() -> None:
    assert _parse_response({"jsonrpc": "2.0", "id": 1, "result": 0}, 1) == 0


def test_parse_response_rejects_mismatched_id() -> None:
    with pytest.raises(Exception, match="identity"):
        _parse_response({"jsonrpc": "2.0", "id": 2, "result": 0}, 1)


def test_require_success_rejects_nonzero_result() -> None:
    with pytest.raises(Exception, match="failed"):
        _require_success(-1, "infrared send")
