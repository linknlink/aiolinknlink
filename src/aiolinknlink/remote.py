"""Local TCP JSON-RPC client for LinknLink infrared remotes."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import struct
from contextlib import suppress
from typing import Any

from .devices import DeviceCapability
from .ehub import EHubDevice
from .models import UltraDevice

DEFAULT_REMOTE_PORT = 502
DEFAULT_REMOTE_TIMEOUT = 5.0
DEFAULT_LEARN_TIMEOUT = 30.0
DEFAULT_LEARN_POLL_INTERVAL = 1.0
MAX_RESPONSE_SIZE = 16 * 1024
IR_FRAME_HEADER_SIZE = 4
IR_FREQUENCY_KHZ = 38
MAX_REQUEST_ID = 0x7FFFFFFF


class LinknLinkRemoteError(Exception):
    """Base infrared remote error."""


class LinknLinkRemoteConnectionError(LinknLinkRemoteError):
    """The remote endpoint could not be reached."""


class LinknLinkRemoteProtocolError(LinknLinkRemoteError):
    """The remote returned an invalid JSON-RPC response."""


class LinknLinkRemoteCommandError(LinknLinkRemoteError):
    """The remote rejected a command."""

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class LinknLinkRemoteTimeoutError(LinknLinkRemoteConnectionError):
    """A remote operation timed out."""


class LinknLinkRemoteClient:
    """Serialize JSON-RPC operations over one persistent TCP connection."""

    def __init__(
        self,
        device: UltraDevice | EHubDevice,
        *,
        port: int = DEFAULT_REMOTE_PORT,
        timeout: float = DEFAULT_REMOTE_TIMEOUT,
    ) -> None:
        if isinstance(device, UltraDevice) and DeviceCapability.REMOTE not in device.capabilities:
            raise ValueError("device does not support the local remote protocol")
        if not device.ip.strip():
            raise ValueError("remote device IP address is required")
        if not 1 <= port <= 0xFFFF or isinstance(port, bool):
            raise ValueError("remote port must be between 1 and 65535")
        if timeout <= 0:
            raise ValueError("remote timeout must be greater than zero")
        self.device = device
        self.port = port
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._request_id = 0

    @property
    def connected(self) -> bool:
        """Return whether the TCP connection is open."""
        return self._writer is not None and not self._writer.is_closing()

    async def close(self) -> None:
        """Close the TCP connection."""
        async with self._lock:
            await self._close_locked()

    async def start_learning(self) -> None:
        """Start infrared learning."""
        await self._study(True)

    async def stop_learning(self) -> None:
        """Stop infrared learning."""
        await self._study(False)

    async def read_learned_code(self) -> str | None:
        """Read the most recently learned infrared code."""
        async with self._lock:
            result = await self._request_locked("irdaRead")
        if not isinstance(result, dict) or set(result) != {"data"}:
            raise LinknLinkRemoteProtocolError("invalid infrared read result")
        data = result["data"]
        if not isinstance(data, str):
            raise LinknLinkRemoteProtocolError("invalid infrared read data")
        if data == "NULL":
            return None
        try:
            _validate_ir_code(data)
        except ValueError as err:
            raise LinknLinkRemoteProtocolError(f"invalid learned infrared code: {err}") from err
        return data

    async def send_code(self, data: str) -> None:
        """Send one learned infrared code."""
        _validate_ir_code(data)
        result = await self._request("irdaSend", {"data": data})
        _require_success(result, "infrared send")

    async def learn_code(
        self,
        *,
        timeout: float = DEFAULT_LEARN_TIMEOUT,
        poll_interval: float = DEFAULT_LEARN_POLL_INTERVAL,
    ) -> str:
        """Learn an infrared code and return it as a base64 string."""
        if timeout <= 0 or poll_interval <= 0:
            raise ValueError("learning timeout and poll interval must be greater than zero")
        result = await self._request("irdaStudy", {"start": 1})
        _require_success(result, "start learning")
        primary_error: BaseException | None = None
        try:
            deadline = asyncio.get_running_loop().time() + timeout
            while asyncio.get_running_loop().time() < deadline:
                if code := await self.read_learned_code():
                    return code
                await asyncio.sleep(min(poll_interval, deadline - asyncio.get_running_loop().time()))
            raise LinknLinkRemoteTimeoutError("infrared learning timed out")
        except BaseException as err:
            primary_error = err
            raise
        finally:
            try:
                result = await self._request("irdaStudy", {"start": 0})
                _require_success(result, "stop learning")
            except LinknLinkRemoteError as err:
                if primary_error is None:
                    raise
                primary_error.add_note(f"stopping infrared learning also failed: {err}")

    async def _study(self, start: bool) -> None:
        result = await self._request("irdaStudy", {"start": int(start)})
        _require_success(result, "start learning" if start else "stop learning")

    async def _request(self, method: str, params: dict[str, object] | None = None) -> object:
        async with self._lock:
            return await self._request_locked(method, params)

    async def _request_locked(self, method: str, params: dict[str, object] | None = None) -> object:
        request_id = self._request_id = self._request_id % MAX_REQUEST_ID + 1
        request: dict[str, object] = {"jsonrpc": "2.0", "method": method, "id": request_id}
        if params is not None:
            request["params"] = params
        try:
            async with asyncio.timeout(self.timeout):
                await self._ensure_connected()
                assert self._writer is not None
                self._writer.write(json.dumps(request, separators=(",", ":")).encode())
                await self._writer.drain()
                response = await self._read_response()
        except TimeoutError as err:
            await self._close_locked()
            raise LinknLinkRemoteTimeoutError(f"remote request {method} timed out") from err
        except (OSError, ConnectionError) as err:
            await self._close_locked()
            raise LinknLinkRemoteConnectionError(f"remote request {method} failed") from err
        try:
            return _parse_response(response, request_id)
        except LinknLinkRemoteProtocolError:
            await self._close_locked()
            raise

    async def _ensure_connected(self) -> None:
        if self.connected:
            return
        await self._close_locked()
        try:
            self._reader, self._writer = await asyncio.open_connection(self.device.ip, self.port)
        except (OSError, ConnectionError) as err:
            raise LinknLinkRemoteConnectionError(f"could not connect to {self.device.ip}:{self.port}") from err

    async def _read_response(self) -> dict[str, Any]:
        assert self._reader is not None
        data = bytearray()
        decoder = json.JSONDecoder()
        while len(data) <= MAX_RESPONSE_SIZE:
            chunk = await self._reader.read(min(4096, MAX_RESPONSE_SIZE + 1 - len(data)))
            if not chunk:
                raise LinknLinkRemoteConnectionError("remote closed before responding")
            data.extend(chunk)
            try:
                text = data.decode()
            except UnicodeDecodeError as err:
                raise LinknLinkRemoteProtocolError("remote response is not valid UTF-8") from err
            try:
                value, end = decoder.raw_decode(text.lstrip())
            except json.JSONDecodeError:
                continue
            if text.lstrip()[end:].strip():
                raise LinknLinkRemoteProtocolError("remote response contains trailing data")
            if not isinstance(value, dict):
                raise LinknLinkRemoteProtocolError("remote response is not a JSON object")
            return value
        raise LinknLinkRemoteProtocolError("remote response exceeds size limit")

    async def _close_locked(self) -> None:
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is not None:
            writer.close()
            with suppress(OSError, ConnectionError):
                await writer.wait_closed()


def _parse_response(response: dict[str, Any], request_id: int) -> object:
    response_id = response.get("id")
    if response.get("jsonrpc") != "2.0" or isinstance(response_id, bool) or response_id != request_id:
        raise LinknLinkRemoteProtocolError("invalid JSON-RPC response identity")
    has_result = "result" in response
    has_error = "error" in response
    if has_result == has_error:
        raise LinknLinkRemoteProtocolError("JSON-RPC response must contain one result or error")
    if has_error:
        error = response["error"]
        if not isinstance(error, dict) or not isinstance(error.get("message"), str):
            raise LinknLinkRemoteProtocolError("invalid JSON-RPC error object")
        code = error.get("code")
        if isinstance(code, bool) or (code is not None and not isinstance(code, int)):
            raise LinknLinkRemoteProtocolError("invalid JSON-RPC error code")
        raise LinknLinkRemoteCommandError(error["message"], code=code)
    return response["result"]


def _require_success(result: object, operation: str) -> None:
    if isinstance(result, bool) or not isinstance(result, int):
        raise LinknLinkRemoteProtocolError(f"invalid {operation} result")
    if result != 0:
        raise LinknLinkRemoteCommandError(f"{operation} failed with result {result}")


def _validate_ir_code(data: str) -> None:
    if not data:
        raise ValueError("infrared code must be non-empty")
    try:
        frame = base64.b64decode(data, validate=True)
    except (ValueError, binascii.Error) as err:
        raise ValueError("infrared code must be valid Base64") from err
    if (
        len(frame) < IR_FRAME_HEADER_SIZE
        or frame[0] != IR_FREQUENCY_KHZ
        or struct.unpack_from("<H", frame, 2)[0] != len(frame) - IR_FRAME_HEADER_SIZE
    ):
        raise ValueError("infrared code frame is invalid")
