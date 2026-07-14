"""Asynchronous eMotion Ultra local LAN client."""

from __future__ import annotations

import asyncio
import logging
import socket
import time
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from .models import UltraDevice, UltraSession, UltraState, UltraSubDeviceState
from .protocol import dna, emotion

_LOGGER = logging.getLogger(__name__)

PROVIDER = "ultra"
DISPLAY_MODEL_ULTRA = "eMotion Ultra"
DISPLAY_MODEL_ULTRA2 = "eMotion Ultra2"
PID_ULTRA = "0000000000000000000000009cac0000"
PID_ULTRA2 = "000000000000000000000000d7ac0000"
TYPE_ULTRA = 0x9CAC
TYPE_ULTRA2 = 0xD7AC
TYPE_ULTRA2_LAN = 0xE3AC
DEFAULT_TIMEOUT = 5.0


class UltraError(Exception):
    """Base Ultra client error."""


class UltraAuthError(UltraError):
    """Ultra authentication failed."""


class UltraConnectionError(UltraError):
    """Ultra device could not be reached."""


class UltraProtocolError(UltraError):
    """Ultra device returned an invalid response."""


class UltraClient:
    """Asynchronous client for the Ultra LAN protocol."""

    def __init__(
        self,
        *,
        default_port: int = dna.DEFAULT_PORT,
        discovery_timeout: float = DEFAULT_TIMEOUT,
        command_timeout: float = DEFAULT_TIMEOUT,
        broadcast_address: str = "255.255.255.255",
    ) -> None:
        self.default_port = default_port or dna.DEFAULT_PORT
        self.discovery_timeout = discovery_timeout
        self.command_timeout = command_timeout
        self.broadcast_address = broadcast_address

    async def discover(self) -> list[UltraDevice]:
        """Discover Ultra devices on the local network."""
        raw_devices = await _discover_dna_devices(
            broadcast_address=self.broadcast_address,
            default_port=self.default_port,
            timeout=self.discovery_timeout,
        )
        devices: list[UltraDevice] = []
        seen: set[str] = set()
        for raw in raw_devices:
            device = self._device_from_dna(raw)
            if not _matches_ultra(device):
                continue
            if device.id in seen:
                continue
            seen.add(device.id)
            devices.append(device)
        return devices

    async def discover_host(self, host: str) -> UltraDevice:
        """Discover a specific Ultra device and return its reported identity."""
        host = host.strip()
        if not host:
            raise UltraConnectionError("host is required")

        loop = asyncio.get_running_loop()
        try:
            address_info = await loop.getaddrinfo(
                host,
                self.default_port,
                family=socket.AF_INET,
                type=socket.SOCK_DGRAM,
            )
        except OSError as err:
            raise UltraConnectionError(f"could not resolve host {host}: {err}") from err

        target_addresses = {str(info[4][0]) for info in address_info}
        client = UltraClient(
            default_port=self.default_port,
            discovery_timeout=self.discovery_timeout,
            command_timeout=self.command_timeout,
            broadcast_address=host,
        )
        for _attempt in range(2):
            for device in await client.discover():
                if device.ip in target_addresses:
                    return device
        raise UltraConnectionError(f"no supported LinknLink device found at {host}")

    async def connect(self, device: UltraDevice) -> UltraSession:
        """Connect/authenticate to an Ultra device."""
        session = UltraSession(device=device, auth_status="discovered", last_seen=datetime.now(UTC))
        mac = dna.mac_bytes(device.mac)
        if not mac:
            session.auth_status = "skipped"
            session.auth_error = "missing mac"
            raise UltraAuthError(session.auth_error)
        last_error: Exception | None = None
        for auth_type in _auth_device_type_candidates(device.type_id):
            try:
                payload = dna.build_auth_payload(mac, auth_type, host=device.ip)
                response = await dna.send_encrypted(
                    device.ip,
                    device.port or self.default_port,
                    dna.NetworkHeader(
                        device_type=auth_type,
                        message_type=dna.MESSAGE_TYPE_AUTH,
                        mac=mac,
                    ),
                    payload,
                    dna.INITIAL_KEY,
                    timeout=self.command_timeout,
                )
                session.session_key = dna.extract_session_key(response)
                session.auth_device_type = auth_type
                device.type_id = auth_type
                device.model = _model_for_device_type(auth_type)
                if not device.name or device.name == DISPLAY_MODEL_ULTRA:
                    device.name = device.model
                session.auth_status = "ok"
                session.auth_error = ""
                session.last_auth_at = datetime.now(UTC)
                session.last_seen = datetime.now(UTC)
                return session
            except (OSError, dna.DNAError) as err:
                last_error = err
        session.auth_status = "failed"
        session.auth_error = str(last_error) if last_error else "authentication failed"
        raise UltraConnectionError(session.auth_error) from last_error

    async def refresh(self, session: UltraSession) -> UltraState:
        """Refresh device state."""
        now = datetime.now(UTC)
        state = UltraState(
            device_id=session.device.id,
            online=True,
            values={"state": "online"},
            children={},
            raw={},
            updated_at=now,
        )
        if not session.session_key:
            await self._reauthenticate(session)

        try:
            payload = await self.send_command(session, emotion.build_gateway_get_state_command())
        except (OSError, dna.DNAError, UltraError) as err:
            session.session_key = None
            raise UltraConnectionError(str(err)) from err

        try:
            self._apply_gateway_payload(session, state, payload)
        except emotion.EmotionError as err:
            raise UltraProtocolError(str(err)) from err

        await self._refresh_subdevices(session, state)
        session.last_seen = datetime.now(UTC)
        return state

    async def control(self, session: UltraSession, entity_attr: str, sub_did: str, payload: dict[str, Any]) -> None:
        """Control a writable entity."""
        if not sub_did:
            raise UltraError("control entity missing sub DID")
        if not session.session_key:
            await self._reauthenticate(session)
        fields = _command_fields(entity_attr, payload)
        if not fields:
            raise UltraError("control payload is empty")
        frame = emotion.build_set_status_frame(sub_did, fields)
        await self.send_command(session, frame)

    async def subscribe_local_udp_push(self, session: UltraSession, port: int, timeout: int) -> None:
        """Ask the device to push position updates to a local UDP port."""
        if not session.session_key:
            raise UltraAuthError("missing DNA session key")
        await self.send_command(session, emotion.build_local_udp_upload_command(port, timeout))

    async def send_command(self, session: UltraSession, command: bytes) -> bytes:
        """Send an Ultra command over DNA."""
        if not session.session_key:
            raise UltraAuthError("missing DNA session key")
        last_error: Exception | None = None
        for device_type in _command_device_type_candidates(session):
            for message_type in _command_message_type_candidates():
                header = dna.NetworkHeader(
                    device_type=device_type,
                    message_type=message_type,
                    sequence=_next_command_sequence(session),
                    mac=dna.mac_bytes(session.device.mac),
                )
                try:
                    payload = await dna.send_encrypted(
                        session.device.ip,
                        session.device.port or self.default_port,
                        header,
                        command,
                        session.session_key,
                        timeout=self.command_timeout,
                    )
                    session.auth_device_type = device_type
                    return payload
                except (OSError, dna.DNAError) as err:
                    last_error = err
        raise UltraError(str(last_error) if last_error else "command failed")

    async def _reauthenticate(self, session: UltraSession) -> None:
        refreshed = await self.connect(session.device)
        session.session_key = refreshed.session_key
        session.auth_device_type = refreshed.auth_device_type
        session.auth_status = refreshed.auth_status
        session.auth_error = refreshed.auth_error
        session.last_auth_at = refreshed.last_auth_at
        session.last_seen = refreshed.last_seen

    def _apply_gateway_payload(self, session: UltraSession, state: UltraState, payload: bytes) -> None:
        response = emotion.parse_gateway_state_response(payload)
        state.raw["gateway_payload_len"] = len(payload)
        state.raw["gateway_command"] = response.command
        if response.gateway_state is not None:
            state.online = response.gateway_state.online
            state.values["state"] = response.gateway_state.state
            state.values.update(response.gateway_state.attributes)
        if response.subdevice_frame is not None:
            state.raw["subdevice_command"] = response.subdevice_frame.command_type
            sub_payload = emotion.parse_subdevice_json_payload(response.subdevice_frame)
            state.raw["subdevice_payload"] = sub_payload
            for child in _subdevice_states_from_payload(sub_payload, state.updated_at):
                _merge_child_state(state.children, child)

    async def _refresh_subdevices(self, session: UltraSession, state: UltraState) -> None:
        try:
            payload = await self.send_command(session, emotion.build_get_subdevice_list_frame())
            state.raw["subdevice_list_payload_len"] = len(payload)
            list_payload = _parse_subdevice_command_response(payload)
        except (dna.DNAError, emotion.EmotionError, UltraError) as err:
            state.raw["subdevice_list_error"] = str(err)
            return
        for child in _subdevice_states_from_payload(list_payload, state.updated_at):
            _merge_child_state(state.children, child)
        for did in list(state.children):
            try:
                payload = await self.send_command(session, emotion.build_get_status_frame(did))
                status_payload = _parse_subdevice_command_response(payload)
            except (dna.DNAError, emotion.EmotionError, UltraError) as err:
                state.raw[f"subdevice_status_error_{did}"] = str(err)
                continue
            for child in _subdevice_states_from_payload(status_payload, state.updated_at):
                _merge_child_state(state.children, child)

    def _device_from_dna(self, raw: dna.DiscoveredDevice) -> UltraDevice:
        device_id = _entity_id_device_segment(raw.mac or raw.id or raw.ip)
        model = _model_for_device_type(raw.device_type)
        name = raw.name or model
        return UltraDevice(
            id=device_id,
            mac=raw.mac,
            ip=raw.ip,
            port=raw.port or self.default_port,
            type_id=raw.device_type,
            name=name,
            model=model,
            raw={"message_type": raw.message_type, "raw_len": len(raw.raw)},
        )


async def _discover_dna_devices(
    *, broadcast_address: str, default_port: int, timeout: float
) -> list[dna.DiscoveredDevice]:
    """Broadcast DNA discovery packets and collect responses asynchronously."""
    if timeout <= 0:
        raise ValueError("timeout must be greater than zero")

    devices: list[dna.DiscoveredDevice] = []
    seen: set[str] = set()
    targets = _discovery_targets(broadcast_address, default_port)
    if not targets:
        targets = [(broadcast_address, default_port, _outbound_ipv4())]

    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("0.0.0.0", 0))
        bound_port = sock.getsockname()[1]
        now = datetime.now(UTC)
        for target_ip, target_port, local_ip in targets:
            for builder in (dna.build_discovery_packet, dna.build_short_discovery_packet):
                try:
                    packet = builder(local_ip, bound_port, now)
                except (ValueError, dna.DNAError):
                    continue
                try:
                    await loop.sock_sendto(sock, packet, (target_ip, target_port))
                except OSError as err:
                    _LOGGER.debug("Discovery send failed target=%s:%s err=%s", target_ip, target_port, err)
        deadline = loop.time() + timeout
        while (remaining := deadline - loop.time()) > 0:
            try:
                data, remote = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, 4096),
                    timeout=remaining,
                )
            except TimeoutError:
                break
            try:
                device = dna.parse_discovery_device_response(data, remote[0], remote[1], default_port)
            except dna.DNAError:
                continue
            if device.id in seen:
                continue
            seen.add(device.id)
            devices.append(device)
    finally:
        sock.close()
    return devices


def _discovery_targets(broadcast_address: str, default_port: int) -> list[tuple[str, int, str]]:
    targets: list[tuple[str, int, str]] = []
    local_ip = _outbound_ipv4()
    targets.append((broadcast_address, default_port, local_ip))
    # Python stdlib does not expose interface broadcast addresses portably.
    # Add common private LAN directed broadcasts; harmless duplicates are removed below.
    if local_ip.count(".") == 3:
        parts = local_ip.split(".")
        targets.append((".".join([parts[0], parts[1], parts[2], "255"]), default_port, local_ip))
    seen: set[tuple[str, int, str]] = set()
    out: list[tuple[str, int, str]] = []
    for target in targets:
        if target in seen:
            continue
        seen.add(target)
        out.append(target)
    return out


def _outbound_ipv4() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"


def _matches_ultra(device: UltraDevice) -> bool:
    if device.pid.lower() in {PID_ULTRA, PID_ULTRA2}:
        return True
    if device.type_id in {TYPE_ULTRA, TYPE_ULTRA2, TYPE_ULTRA2_LAN}:
        return True
    return "ultra" in device.name.lower()


def _model_for_device_type(device_type: int) -> str:
    if device_type in {TYPE_ULTRA2, TYPE_ULTRA2_LAN}:
        return DISPLAY_MODEL_ULTRA2
    return DISPLAY_MODEL_ULTRA


def _auth_device_type_candidates(device_type: int) -> list[int]:
    if device_type in {TYPE_ULTRA, TYPE_ULTRA2, TYPE_ULTRA2_LAN}:
        values = [device_type, TYPE_ULTRA2, TYPE_ULTRA2_LAN, TYPE_ULTRA]
    else:
        values = [TYPE_ULTRA2, TYPE_ULTRA2_LAN, TYPE_ULTRA]
    return _dedupe_ints(values)


def _command_device_type_candidates(session: UltraSession) -> list[int]:
    values = [
        session.auth_device_type,
        session.device.type_id,
        TYPE_ULTRA2,
        TYPE_ULTRA2_LAN,
        TYPE_ULTRA,
    ]
    return _dedupe_ints(value for value in values if value)


def _command_message_type_candidates() -> list[int]:
    return [0x03E9, dna.MESSAGE_TYPE_COMMAND]


def _next_command_sequence(session: UltraSession) -> int:
    if session.command_sequence <= 0:
        session.command_sequence = time.time_ns() & 0xFFFF
    session.command_sequence = (session.command_sequence + 1) & 0xFFFF
    if session.command_sequence == 0:
        session.command_sequence = 1
    return session.command_sequence


def _parse_subdevice_command_response(payload: bytes) -> dict[str, Any]:
    try:
        frame = emotion.parse_subdevice_frame(payload)
        return emotion.parse_subdevice_json_payload(frame)
    except emotion.EmotionError as err:
        response = emotion.parse_gateway_state_response(payload)
        if response.subdevice_frame is None:
            raise UltraError("response does not contain subdevice frame") from err
        return emotion.parse_subdevice_json_payload(response.subdevice_frame)


def _subdevice_states_from_payload(payload: dict[str, Any], now: datetime | None) -> list[UltraSubDeviceState]:
    children: list[UltraSubDeviceState] = []
    for key in ("list", "subdevices", "device_list", "devices"):
        value = payload.get(key)
        if isinstance(value, list):
            children.extend(_subdevice_states_from_list(value, now))
    child = _subdevice_state_from_payload(payload, now)
    if child is not None:
        children.append(child)
    for key, value in payload.items():
        if isinstance(value, dict):
            item = dict(value)
            item.setdefault("did", key)
            child = _subdevice_state_from_payload(item, now)
            if child is not None:
                children.append(child)
    out: dict[str, UltraSubDeviceState] = {}
    for child in children:
        _merge_child_state(out, child)
    return list(out.values())


def _subdevice_states_from_list(items: list[Any], now: datetime | None) -> list[UltraSubDeviceState]:
    children: list[UltraSubDeviceState] = []
    for item in items:
        if isinstance(item, dict):
            child = _subdevice_state_from_payload(item, now)
            if child is not None:
                children.append(child)
    return children


def _subdevice_state_from_payload(payload: dict[str, Any], now: datetime | None) -> UltraSubDeviceState | None:
    did = _string_field(payload, "did", "DID", "device_id", "sub_did")
    if not did:
        return None
    fields = {key: value for key, value in payload.items() if key not in _SUBDEVICE_META_KEYS}
    return UltraSubDeviceState(
        did=did,
        pid=_string_field(payload, "pid", "PID"),
        name=_string_field(payload, "name", "Name"),
        type=_string_field(payload, "type", "category", "model"),
        fields=fields,
        raw=dict(payload),
        updated_at=now,
    )


def _merge_child_state(children: dict[str, UltraSubDeviceState], next_child: UltraSubDeviceState) -> None:
    current = children.get(next_child.did)
    if current is None:
        children[next_child.did] = next_child
        return
    if next_child.pid:
        current.pid = next_child.pid
    if next_child.name:
        current.name = next_child.name
    if next_child.type:
        current.type = next_child.type
    current.fields.update(next_child.fields)
    current.raw.update(next_child.raw)
    current.updated_at = next_child.updated_at


def _command_fields(entity_attr: str, payload: dict[str, Any]) -> dict[str, Any]:
    attr = entity_attr or "value"
    if not payload:
        return {}
    if len(payload) == 1:
        if attr in payload:
            return {attr: payload[attr]}
        if "state" in payload and attr != "state":
            return {attr: _normalize_control_value(payload["state"])}
        if "value" in payload and attr != "value":
            return {attr: payload["value"]}
    return {key: _normalize_control_value(value) for key, value in payload.items() if key != "did"}


def _normalize_control_value(value: Any) -> Any:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"on", "true", "1", "open", "开启"}:
            return True
        if lowered in {"off", "false", "0", "close", "closed", "关闭"}:
            return False
    return value


def _string_field(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        if key not in payload:
            continue
        value = payload[key]
        if value is None:
            continue
        return str(value).strip()
    return ""


def _entity_id_device_segment(value: str) -> str:
    compact = value.lower().replace(":", "").replace("-", "").replace("_", "").replace(" ", "")
    return compact or value


def _dedupe_ints(values: Iterable[int]) -> list[int]:
    out: list[int] = []
    seen: set[int] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


_SUBDEVICE_META_KEYS = {
    "did",
    "DID",
    "device_id",
    "sub_did",
    "pid",
    "PID",
    "name",
    "Name",
    "type",
    "category",
    "model",
    "list",
    "subdevices",
    "device_list",
    "devices",
}
