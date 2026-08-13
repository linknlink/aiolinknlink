"""Asynchronous local LAN client for LinknLink iBG gateways."""

from __future__ import annotations

import asyncio
import math
import secrets
import socket
from datetime import UTC, datetime
from typing import Any, TypeGuard

from .client import _discover_dna_devices
from .models import IbgDevice, IbgSession, IbgSubDevice, IbgSubDeviceState
from .protocol import dna, gateway

DISPLAY_MODEL_IBG2_SE = "iBG2 SE"
PID_SR3_SENSOR = "00000000000000000000000005000100"
SUPPORTED_SENSOR_PIDS = frozenset({PID_SR3_SENSOR})
DEFAULT_TIMEOUT = 5.0
DEFAULT_AUTH_TIMEOUT = 10.0
PAGE_SIZE = 10


class IbgError(Exception):
    """Base iBG client error."""


class IbgConnectionError(IbgError):
    """An iBG gateway could not be reached or authenticated."""


class IbgProtocolError(IbgError):
    """An iBG gateway returned an invalid response."""


class IbgClient:
    """Asynchronous client for the iBG compact DNA LAN protocol."""

    def __init__(
        self,
        *,
        default_port: int = dna.DEFAULT_PORT,
        discovery_timeout: float = DEFAULT_TIMEOUT,
        command_timeout: float = DEFAULT_TIMEOUT,
        auth_timeout: float = DEFAULT_AUTH_TIMEOUT,
        broadcast_address: str = "255.255.255.255",
    ) -> None:
        self.default_port = default_port or dna.DEFAULT_PORT
        self.discovery_timeout = discovery_timeout
        self.command_timeout = command_timeout
        self.auth_timeout = auth_timeout
        self.broadcast_address = broadcast_address

    async def discover(self) -> list[IbgDevice]:
        """Discover iBG gateways on the local network."""
        raw_devices = await _discover_dna_devices(
            broadcast_address=self.broadcast_address,
            default_port=self.default_port,
            timeout=self.discovery_timeout,
        )
        devices: list[IbgDevice] = []
        seen: set[str] = set()
        for raw in raw_devices:
            if not raw.name.upper().startswith("IBG"):
                continue
            device = self._device_from_dna(raw)
            if device.id in seen:
                continue
            seen.add(device.id)
            devices.append(device)
        return devices

    async def discover_host(self, host: str) -> IbgDevice:
        """Discover a specific iBG gateway and return its reported identity."""
        host = host.strip()
        if not host:
            raise IbgConnectionError("host is required")
        loop = asyncio.get_running_loop()
        try:
            address_info = await loop.getaddrinfo(
                host,
                self.default_port,
                family=socket.AF_INET,
                type=socket.SOCK_DGRAM,
            )
        except OSError as err:
            raise IbgConnectionError(f"could not resolve host {host}: {err}") from err
        target_addresses = {str(info[4][0]) for info in address_info}
        client = IbgClient(
            default_port=self.default_port,
            discovery_timeout=self.discovery_timeout,
            command_timeout=self.command_timeout,
            auth_timeout=self.auth_timeout,
            broadcast_address=host,
        )
        for _attempt in range(2):
            for device in await client.discover():
                if device.ip in target_addresses:
                    return device
        raise IbgConnectionError(f"no iBG gateway found at {host}")

    async def connect(
        self,
        device: IbgDevice,
        *,
        exchange: dna.PacketExchange | None = None,
    ) -> IbgSession:
        """Authenticate locally and establish an iBG session."""
        mac = dna.mac_bytes(device.mac)
        if not mac:
            raise IbgConnectionError("iBG discovery response did not include a valid MAC")
        sequence = _random_sequence()
        try:
            response = await dna.send_encrypted(
                device.ip,
                device.port or self.default_port,
                dna.NetworkHeader(
                    device_type=device.type_id,
                    message_type=dna.MESSAGE_TYPE_AUTH,
                    sequence=sequence,
                    mac=mac,
                ),
                dna.build_auth_payload(mac, device.type_id, host=device.ip),
                dna.INITIAL_KEY,
                timeout=self.auth_timeout,
                exchange=exchange,
                compact=True,
            )
            session_key = dna.extract_session_key(response)
        except (OSError, dna.DNAError) as err:
            raise IbgConnectionError(str(err)) from err
        now = datetime.now(UTC)
        return IbgSession(
            device=device,
            session_key=session_key,
            command_sequence=sequence,
            last_auth_at=now,
            last_seen=now,
        )

    async def list_subdevices(
        self,
        session: IbgSession,
        *,
        exchange: dna.PacketExchange | None = None,
    ) -> list[IbgSubDevice]:
        """Read the complete paginated iBG subdevice list."""
        devices: list[IbgSubDevice] = []
        seen: set[str] = set()
        index = 0
        total: int | None = None
        while total is None or index < total:
            payload = await self._command(
                session,
                gateway.CMD_GATEWAY_LIST,
                {"count": PAGE_SIZE, "index": index},
                gateway.CMD_GATEWAY_LIST_RESPONSE,
                exchange=exchange,
            )
            _require_success(payload)
            raw_total = payload.get("total")
            entries = payload.get("list")
            if isinstance(raw_total, bool) or not isinstance(raw_total, int) or raw_total < 0:
                raise IbgProtocolError("gateway list response has invalid total")
            if not isinstance(entries, list):
                raise IbgProtocolError("gateway list response has invalid list")
            total = raw_total
            for entry in entries:
                device = _parse_subdevice(entry)
                if device.did in seen:
                    continue
                seen.add(device.did)
                devices.append(device)
            if not entries:
                if index < total:
                    raise IbgProtocolError("gateway list pagination stopped before total")
                break
            index += len(entries)
        return devices

    async def get_subdevice_state(
        self,
        session: IbgSession,
        device: IbgSubDevice,
        *,
        exchange: dna.PacketExchange | None = None,
    ) -> IbgSubDeviceState:
        """Read one supported subdevice and expose only reviewed safe fields."""
        if device.pid.lower() not in SUPPORTED_SENSOR_PIDS:
            raise IbgProtocolError(f"unsupported iBG subdevice PID: {device.pid}")
        payload = await self._command(
            session,
            gateway.CMD_GET_STATUS,
            {"did": device.did},
            gateway.CMD_STATUS_RESPONSE,
            exchange=exchange,
        )
        _require_success(payload)
        returned_did = payload.get("did")
        if returned_did is not None and returned_did != device.did:
            raise IbgProtocolError("subdevice status identity does not match request")
        return IbgSubDeviceState(
            device=device,
            values=normalize_subdevice_state(device.pid, payload),
            received_at=datetime.now(UTC),
        )

    async def read_supported_states(
        self,
        session: IbgSession,
        devices: list[IbgSubDevice],
        *,
        exchange: dna.PacketExchange | None = None,
    ) -> dict[str, IbgSubDeviceState | None]:
        """Read online supported sensors without failing the whole gateway refresh."""
        states: dict[str, IbgSubDeviceState | None] = {}
        for device in devices:
            if device.pid.lower() not in SUPPORTED_SENSOR_PIDS:
                continue
            if not device.online:
                states[device.did] = None
                continue
            try:
                states[device.did] = await self.get_subdevice_state(
                    session,
                    device,
                    exchange=exchange,
                )
            except (IbgError, OSError, dna.DNAError):
                states[device.did] = None
        return states

    async def _command(
        self,
        session: IbgSession,
        command_type: int,
        payload: dict[str, object],
        response_type: int,
        *,
        exchange: dna.PacketExchange | None = None,
    ) -> dict[str, Any]:
        key = session.session_key
        if key is None:
            raise IbgConnectionError("iBG session is not authenticated")
        sequence = _next_sequence(session)
        mac = dna.mac_bytes(session.device.mac)
        try:
            response = await dna.send_encrypted(
                session.device.ip,
                session.device.port or self.default_port,
                dna.NetworkHeader(
                    device_type=session.device.type_id,
                    message_type=dna.MESSAGE_TYPE_COMMAND,
                    sequence=sequence,
                    mac=mac,
                ),
                gateway.build_gateway_frame(command_type, payload),
                key,
                timeout=self.command_timeout,
                exchange=exchange,
                compact=True,
            )
            frame = gateway.parse_gateway_frame(response)
        except gateway.GatewayProtocolError as err:
            raise IbgProtocolError(str(err)) from err
        except (OSError, dna.DNAError) as err:
            raise IbgConnectionError(str(err)) from err
        if frame.command_type != response_type:
            raise IbgProtocolError(
                f"unexpected gateway response command: 0x{frame.command_type:04x}"
            )
        session.last_seen = datetime.now(UTC)
        return frame.payload

    def _device_from_dna(self, raw: dna.DiscoveredDevice) -> IbgDevice:
        return IbgDevice(
            id=(raw.mac or raw.id or raw.ip).replace(":", "").replace("-", "").lower(),
            ip=raw.ip,
            port=raw.port or self.default_port,
            mac=raw.mac,
            type_id=raw.device_type,
            name=raw.name or "iBG",
            model=DISPLAY_MODEL_IBG2_SE,
        )


def normalize_subdevice_state(pid: str, payload: dict[str, Any]) -> dict[str, int | float | bool]:
    """Return reviewed HA-safe fields, excluding all gateway configuration."""
    if pid.lower() != PID_SR3_SENSOR:
        return {}
    values: dict[str, int | float | bool] = {}
    temperature = _number(payload.get("envtemp"))
    if temperature is not None and -1000 <= temperature <= 1000:
        values["temperature"] = temperature / 10
    humidity = _number(payload.get("envhumid"))
    if humidity is not None and 0 <= humidity <= 1000:
        values["humidity"] = humidity / 10
    illuminance = _number(payload.get("envlux"))
    if illuminance is not None and illuminance >= 0:
        values["illuminance"] = illuminance
    battery = _number(payload.get("battery"))
    if battery is not None and 0 <= battery <= 100:
        values["battery"] = round(battery)
    presence = payload.get("pir_detected")
    if isinstance(presence, bool):
        values["occupancy"] = presence
    elif isinstance(presence, int) and presence in {0, 1}:
        values["occupancy"] = bool(presence)
    return values


def _parse_subdevice(value: object) -> IbgSubDevice:
    if not isinstance(value, dict):
        raise IbgProtocolError("gateway list entry must be an object")
    did = value.get("did")
    pid = value.get("pid")
    if not _is_hex_identifier(did) or not _is_hex_identifier(pid):
        raise IbgProtocolError("gateway list entry has invalid did or pid")
    name = value.get("name")
    if not isinstance(name, str) or not name.strip():
        name = f"iBG device {did[-8:]}"
    offline = value.get("offline", 1)
    if isinstance(offline, bool):
        online = not offline
    elif isinstance(offline, int):
        online = offline == 0
    else:
        online = False
    return IbgSubDevice(did=did.lower(), pid=pid.lower(), name=name.strip(), online=online)


def _is_hex_identifier(value: object) -> TypeGuard[str]:
    if not isinstance(value, str) or len(value) != 32:
        return False
    try:
        bytes.fromhex(value)
    except ValueError:
        return False
    return True


def _require_success(payload: dict[str, Any]) -> None:
    status = payload.get("status", 0)
    if isinstance(status, bool) or not isinstance(status, int):
        raise IbgProtocolError("gateway response has invalid status")
    if status != 0:
        raise IbgProtocolError(f"gateway command failed with status {status}")


def _number(value: object) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(float(value)):
        return None
    return value


def _random_sequence() -> int:
    return secrets.randbelow(0xFFFF) + 1


def _next_sequence(session: IbgSession) -> int:
    session.command_sequence = session.command_sequence % 0xFFFF + 1
    return session.command_sequence
