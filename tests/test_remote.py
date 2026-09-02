"""Tests for the local infrared remote JSON-RPC client."""

from __future__ import annotations

import asyncio
import base64
import json
import struct
from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest

from aiolinknlink import (
    TYPE_EHOME_HA,
    TYPE_EREMOTE_HA,
    TYPE_ULTRA2,
    LinknLinkRemoteClient,
    LinknLinkRemoteCommandError,
    LinknLinkRemoteConnectionError,
    LinknLinkRemoteProtocolError,
    LinknLinkRemoteTimeoutError,
    UltraDevice,
)
from aiolinknlink.remote import MAX_REQUEST_ID, _parse_rpc_response, _require_success_result

IR_CODE = base64.b64encode(bytes([38, 0]) + struct.pack("<H", 3) + b"abc").decode()


def _device(device_type: int = TYPE_EHOME_HA) -> UltraDevice:
    return UltraDevice(
        id="020000000110",
        ip="198.51.100.8",
        port=80,
        mac="02:00:00:00:01:10",
        type_id=device_type,
    )


ResponseHandler = Callable[[dict[str, object]], object]


class _Reader:
    def __init__(self) -> None:
        self.chunks: asyncio.Queue[bytes] = asyncio.Queue()

    async def read(self, size: int) -> bytes:
        chunk = await self.chunks.get()
        if len(chunk) <= size:
            return chunk
        await self.chunks.put(chunk[size:])
        return chunk[:size]


class _Writer:
    def __init__(self, reader: _Reader, handler: ResponseHandler) -> None:
        self.reader = reader
        self.handler = handler
        self.requests: list[dict[str, object]] = []
        self.closing = False
        self.wait_closed_calls = 0

    def write(self, data: bytes) -> None:
        request = json.loads(data)
        self.requests.append(request)
        response = self.handler(request)
        if response is None:
            return
        chunks = response if isinstance(response, list) else [response]
        for chunk in chunks:
            if isinstance(chunk, bytes):
                self.reader.chunks.put_nowait(chunk)
            else:
                self.reader.chunks.put_nowait(json.dumps(chunk, separators=(",", ":")).encode())

    async def drain(self) -> None:
        return None

    def is_closing(self) -> bool:
        return self.closing

    def close(self) -> None:
        self.closing = True

    async def wait_closed(self) -> None:
        self.wait_closed_calls += 1


def _success(request: dict[str, object], result: object = 0) -> dict[str, object]:
    return {"jsonrpc": "2.0", "result": result, "id": request["id"]}


def _install_connection(
    monkeypatch: pytest.MonkeyPatch,
    handler: ResponseHandler,
) -> tuple[_Writer, AsyncMock]:
    reader = _Reader()
    writer = _Writer(reader, handler)
    connect = AsyncMock(return_value=(reader, writer))
    monkeypatch.setattr(asyncio, "open_connection", connect)
    return writer, connect


@pytest.mark.parametrize("device_type", [TYPE_EHOME_HA, TYPE_EREMOTE_HA])
async def test_remote_operations_use_one_connection_and_monotonic_ids(
    monkeypatch: pytest.MonkeyPatch,
    device_type: int,
) -> None:
    def handler(request: dict[str, object]) -> object:
        if request["method"] == "irdaRead":
            response = json.dumps(_success(request, {"data": IR_CODE}), separators=(",", ":")).encode()
            return [response[:7], response[7:]]
        return _success(request)

    writer, connect = _install_connection(monkeypatch, handler)
    client = LinknLinkRemoteClient(_device(device_type))

    await client.start_learning()
    assert await client.read_learned_code() == IR_CODE
    await client.send_code(IR_CODE)
    await client.stop_learning()

    assert client.connected is True
    assert connect.await_count == 1
    assert [request["id"] for request in writer.requests] == [1, 2, 3, 4]
    assert writer.requests == [
        {"jsonrpc": "2.0", "method": "irdaStudy", "id": 1, "params": {"start": 1}},
        {"jsonrpc": "2.0", "method": "irdaRead", "id": 2},
        {"jsonrpc": "2.0", "method": "irdaSend", "id": 3, "params": {"data": IR_CODE}},
        {"jsonrpc": "2.0", "method": "irdaStudy", "id": 4, "params": {"start": 0}},
    ]

    await client.close()
    assert client.connected is False
    assert writer.wait_closed_calls == 1
    await client.close()


async def test_read_returns_none_until_code_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_connection(monkeypatch, lambda request: _success(request, {"data": "NULL"}))

    assert await LinknLinkRemoteClient(_device()).read_learned_code() is None


async def test_learn_code_polls_and_always_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    reads = iter(["NULL", IR_CODE])

    def handler(request: dict[str, object]) -> object:
        if request["method"] == "irdaRead":
            return _success(request, {"data": next(reads)})
        return _success(request)

    writer, _ = _install_connection(monkeypatch, handler)
    client = LinknLinkRemoteClient(_device())

    assert await client.learn_code(timeout=1, poll_interval=0.001) == IR_CODE
    assert [request["method"] for request in writer.requests] == [
        "irdaStudy",
        "irdaRead",
        "irdaRead",
        "irdaStudy",
    ]
    assert writer.requests[-1]["params"] == {"start": 0}


async def test_learn_timeout_still_stops_device(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: dict[str, object]) -> object:
        if request["method"] == "irdaRead":
            return _success(request, {"data": "NULL"})
        return _success(request)

    writer, _ = _install_connection(monkeypatch, handler)
    client = LinknLinkRemoteClient(_device())

    with pytest.raises(LinknLinkRemoteTimeoutError, match="learning timed out"):
        await client.learn_code(timeout=0.005, poll_interval=0.005)

    assert writer.requests[-1]["method"] == "irdaStudy"
    assert writer.requests[-1]["params"] == {"start": 0}


async def test_stop_failure_does_not_hide_learning_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: dict[str, object]) -> object:
        if request["method"] == "irdaRead":
            return _success(request, {"data": 1})
        if request.get("params") == {"start": 0}:
            return _success(request, -1)
        return _success(request)

    _install_connection(monkeypatch, handler)

    with pytest.raises(LinknLinkRemoteProtocolError, match="read data") as error:
        await LinknLinkRemoteClient(_device()).learn_code()

    assert any("stopping infrared learning also failed" in note for note in error.value.__notes__)


async def test_request_timeout_closes_connection(monkeypatch: pytest.MonkeyPatch) -> None:
    writer, _ = _install_connection(monkeypatch, lambda request: None)
    client = LinknLinkRemoteClient(_device(), timeout=0.01)

    with pytest.raises(LinknLinkRemoteTimeoutError, match="irdaRead timed out"):
        await client.read_learned_code()

    assert writer.closing is True
    assert client.connected is False


async def test_connect_failure_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        asyncio,
        "open_connection",
        AsyncMock(side_effect=OSError("unreachable")),
    )

    with pytest.raises(LinknLinkRemoteConnectionError, match="could not connect"):
        await LinknLinkRemoteClient(_device()).start_learning()


async def test_closed_writer_reconnects(monkeypatch: pytest.MonkeyPatch) -> None:
    pairs = []
    for _ in range(2):
        reader = _Reader()
        writer = _Writer(reader, lambda request: _success(request))
        pairs.append((reader, writer))
    connect = AsyncMock(side_effect=pairs)
    monkeypatch.setattr(asyncio, "open_connection", connect)
    client = LinknLinkRemoteClient(_device())

    await client.start_learning()
    pairs[0][1].closing = True
    await client.stop_learning()

    assert connect.await_count == 2
    assert pairs[0][1].wait_closed_calls == 1


@pytest.mark.parametrize(
    ("response", "message"),
    [
        ({"jsonrpc": "1.0", "result": 0, "id": 1}, "version"),
        ({"jsonrpc": "2.0", "result": 0, "id": 2}, "ID mismatch"),
        ({"jsonrpc": "2.0", "id": 1}, "exactly one"),
        ({"jsonrpc": "2.0", "result": 0, "error": {}, "id": 1}, "exactly one"),
        ({"jsonrpc": "2.0", "error": "bad", "id": 1}, "error object"),
        ({"jsonrpc": "2.0", "error": {"code": True, "message": "bad"}, "id": 1}, "error fields"),
    ],
)
async def test_invalid_rpc_response_closes_connection(
    monkeypatch: pytest.MonkeyPatch,
    response: dict[str, object],
    message: str,
) -> None:
    writer, _ = _install_connection(monkeypatch, lambda request: response)
    client = LinknLinkRemoteClient(_device())

    with pytest.raises(LinknLinkRemoteProtocolError, match=message):
        await client.start_learning()

    assert writer.closing is True


async def test_rpc_error_preserves_code(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: dict[str, object]) -> object:
        return {
            "jsonrpc": "2.0",
            "error": {"code": -32602, "message": "Invalid params"},
            "id": request["id"],
        }

    writer, _ = _install_connection(monkeypatch, handler)
    client = LinknLinkRemoteClient(_device())

    with pytest.raises(LinknLinkRemoteCommandError, match="Invalid params") as error:
        await client.start_learning()

    assert error.value.code == -32602
    assert writer.closing is False


@pytest.mark.parametrize(
    ("result", "message"),
    [(True, "invalid"), ("0", "invalid"), (-1, "failed with result")],
)
async def test_command_result_is_validated(
    monkeypatch: pytest.MonkeyPatch,
    result: object,
    message: str,
) -> None:
    _install_connection(monkeypatch, lambda request: _success(request, result))

    with pytest.raises((LinknLinkRemoteProtocolError, LinknLinkRemoteCommandError), match=message):
        await LinknLinkRemoteClient(_device()).start_learning()


@pytest.mark.parametrize(
    ("result", "message"),
    [
        ([], "read result"),
        ({"other": IR_CODE}, "read result"),
        ({"data": 1}, "read data"),
        ({"data": "invalid"}, "invalid learned infrared code"),
    ],
)
async def test_read_result_is_validated(
    monkeypatch: pytest.MonkeyPatch,
    result: object,
    message: str,
) -> None:
    _install_connection(monkeypatch, lambda request: _success(request, result))

    with pytest.raises(LinknLinkRemoteProtocolError, match=message):
        await LinknLinkRemoteClient(_device()).read_learned_code()


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (b"[]", "not a JSON object"),
        (b'{"jsonrpc":"2.0","result":0,"id":1} trailing', "trailing data"),
        (b"\xff", "valid UTF-8"),
    ],
)
async def test_stream_response_validation(
    monkeypatch: pytest.MonkeyPatch,
    response: bytes,
    message: str,
) -> None:
    _install_connection(monkeypatch, lambda request: response)

    with pytest.raises(LinknLinkRemoteProtocolError, match=message):
        await LinknLinkRemoteClient(_device(), timeout=0.05).start_learning()


async def test_response_size_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_connection(monkeypatch, lambda request: b"{" + b" " * 100)

    with pytest.raises(LinknLinkRemoteProtocolError, match="size limit"):
        await LinknLinkRemoteClient(_device(), max_response_size=16).start_learning()


async def test_eof_before_response(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_connection(monkeypatch, lambda request: b"")

    with pytest.raises(LinknLinkRemoteConnectionError, match="closed the connection"):
        await LinknLinkRemoteClient(_device()).start_learning()


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("", "non-empty"),
        ("not-base64", "valid Base64"),
        (base64.b64encode(b"\x26").decode(), "too short"),
        (base64.b64encode(b"\x28\x00\x00\x00").decode(), "38 kHz"),
        (base64.b64encode(b"\x26\x00\x02\x00x").decode(), "length"),
    ],
)
async def test_send_rejects_invalid_ir_frames(code: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        await LinknLinkRemoteClient(_device()).send_code(code)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"port": 0}, "port"),
        ({"timeout": 0}, "timeout"),
        ({"max_response_size": 0}, "response size"),
    ],
)
def test_constructor_validation(kwargs: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        LinknLinkRemoteClient(_device(), **kwargs)


def test_constructor_rejects_non_remote_and_missing_ip() -> None:
    with pytest.raises(ValueError, match="does not support"):
        LinknLinkRemoteClient(_device(TYPE_ULTRA2))
    device = _device()
    device.ip = ""
    with pytest.raises(ValueError, match="IP address"):
        LinknLinkRemoteClient(device)


@pytest.mark.parametrize(
    ("timeout", "poll_interval", "message"),
    [(0, 1, "learn timeout"), (1, 0, "poll interval")],
)
async def test_learn_argument_validation(
    timeout: float,
    poll_interval: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        await LinknLinkRemoteClient(_device()).learn_code(
            timeout=timeout,
            poll_interval=poll_interval,
        )


def test_request_id_wraps() -> None:
    client = LinknLinkRemoteClient(_device())
    client._request_id = MAX_REQUEST_ID

    assert client._next_request_id() == 1


def test_rpc_validation_helpers() -> None:
    with pytest.raises(LinknLinkRemoteProtocolError, match="error fields"):
        _parse_rpc_response(
            {"jsonrpc": "2.0", "error": {"code": -1, "message": 1}, "id": 1},
            1,
        )
    with pytest.raises(LinknLinkRemoteProtocolError, match="invalid test result"):
        _require_success_result(None, "test")
