"""Asynchronous eMotion Ultra2 local LAN client."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import socket
import time
from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from .models import (
    UltraDevice,
    UltraLocalUDPConfig,
    UltraRadarStatus,
    UltraRadarZRange,
    UltraSession,
)
from .protocol import dna, emotion

_LOGGER = logging.getLogger(__name__)

PROVIDER = "ultra"
DISPLAY_MODEL_ULTRA2 = "eMotion Ultra2"
PID_ULTRA2 = "000000000000000000000000d7ac0000"
TYPE_ULTRA2 = 0xD7AC
TYPE_ULTRA2_LAN = 0xE3AC
TYPE_ULTRA2_RADAR = 0xACDB
DEFAULT_TIMEOUT = 5.0
DEFAULT_AUTH_TIMEOUT = 15.0
DEFAULT_PREFERRED_COMMAND_TIMEOUT = 15.0
MAX_ABSENCE_DELAY = 18 * 60 * 60
MIN_Z_RANGE = -6.0
MAX_Z_RANGE = 6.0


class UltraError(Exception):
    """Base Ultra2 client error."""


class UltraAuthError(UltraError):
    """Ultra authentication failed."""


class UltraConnectionError(UltraError):
    """Ultra2 device could not be reached."""


class UltraProtocolError(UltraError):
    """Ultra2 device returned an invalid response."""


class UltraClient:
    """Asynchronous client for the Ultra2 LAN protocol."""

    def __init__(
        self,
        *,
        default_port: int = dna.DEFAULT_PORT,
        discovery_timeout: float = DEFAULT_TIMEOUT,
        command_timeout: float = DEFAULT_TIMEOUT,
        auth_timeout: float = DEFAULT_AUTH_TIMEOUT,
        preferred_command_timeout: float = DEFAULT_PREFERRED_COMMAND_TIMEOUT,
        broadcast_address: str = "255.255.255.255",
    ) -> None:
        self.default_port = default_port or dna.DEFAULT_PORT
        self.discovery_timeout = discovery_timeout
        self.command_timeout = command_timeout
        self.auth_timeout = auth_timeout
        self.preferred_command_timeout = preferred_command_timeout
        self.broadcast_address = broadcast_address

    async def discover(self) -> list[UltraDevice]:
        """Discover Ultra2 devices on the local network."""
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
        """Discover a specific Ultra2 device and return its reported identity."""
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
            auth_timeout=self.auth_timeout,
            preferred_command_timeout=self.preferred_command_timeout,
            broadcast_address=host,
        )
        for _attempt in range(2):
            for device in await client.discover():
                if device.ip in target_addresses:
                    return device
        raise UltraConnectionError(f"no supported LinknLink device found at {host}")

    async def connect(
        self,
        device: UltraDevice,
        *,
        protocol_mac: str | None = None,
        exchange: dna.PacketExchange | None = None,
    ) -> UltraSession:
        """Connect/authenticate to an Ultra2 device."""
        auth_mac = protocol_mac or device.mac
        session = UltraSession(
            device=device,
            auth_mac=auth_mac,
            auth_status="discovered",
            last_seen=datetime.now(UTC),
        )
        mac = dna.mac_bytes(auth_mac)
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
                    timeout=self.auth_timeout,
                    exchange=exchange,
                )
                session.session_key = dna.extract_session_key(response)
                session.auth_device_type = auth_type
                if device.type_id not in {TYPE_ULTRA2, TYPE_ULTRA2_LAN}:
                    device.type_id = auth_type
                device.model = DISPLAY_MODEL_ULTRA2
                if not device.name:
                    device.name = DISPLAY_MODEL_ULTRA2
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

    async def subscribe_local_udp_push(
        self,
        session: UltraSession,
        port: int,
        timeout: int,
        *,
        try_all: bool = True,
        exchange: dna.PacketExchange | None = None,
    ) -> UltraLocalUDPConfig:
        """Ask the device to push position updates to a local UDP port."""
        if not session.session_key:
            raise UltraAuthError("missing DNA session key")
        payload = await self.send_command(
            session,
            emotion.build_local_udp_upload_command(port, timeout),
            try_all=try_all,
            exchange=exchange,
        )
        try:
            return emotion.parse_local_udp_upload_response(payload)
        except emotion.EmotionError as err:
            raise UltraProtocolError(str(err)) from err

    async def get_radar_status(
        self,
        session: UltraSession,
        *,
        exchange: dna.PacketExchange | None = None,
    ) -> UltraRadarStatus:
        """Read the validated Ultra2 radar configuration fields."""
        radar_did = derive_ultra2_radar_did(session.device.mac)
        try:
            payload = await self.send_command(
                session,
                emotion.build_get_status_frame(radar_did),
                exchange=exchange,
            )
            frame = emotion.parse_subdevice_frame(payload)
            status = emotion.parse_subdevice_json_payload(frame)
        except emotion.EmotionError as err:
            raise UltraProtocolError(str(err)) from err

        response_did = str(status.get("did", ""))
        if response_did.lower() != radar_did:
            raise UltraProtocolError(f"radar status DID mismatch: {response_did or 'missing'}")
        response_status = status.get("status")
        if isinstance(response_status, bool) or response_status != 0:
            raise UltraProtocolError(f"radar status read failed: {response_status!r}")
        sensitivity = _required_int(status, "level_of_sensitivity", valid_values=range(3))
        return UltraRadarStatus(
            did=radar_did,
            sensitivity=sensitivity,
            received_at=datetime.now(UTC),
            trigger_speed=_optional_int(status, "triger_speed", valid_values=range(3)),
            install_mode=_optional_int(status, "install_mode", valid_values=range(2)),
            height=_optional_int(status, "height", minimum=0, maximum=0xFFFF),
            install_direction=_optional_int(
                status,
                "install_direction",
                minimum=0,
                maximum=0xFF,
            ),
            z_range=_optional_z_range(status),
            default_absence_delay=_optional_int(
                status,
                "delaytime",
                minimum=0,
                maximum=0xFFFF,
            ),
            zone_absence_delays=(
                _optional_int(status, "duration1", minimum=0, maximum=0xFFFF),
                _optional_int(status, "duration2", minimum=0, maximum=0xFFFF),
                _optional_int(status, "duration3", minimum=0, maximum=0xFFFF),
                _optional_int(status, "duration4", minimum=0, maximum=0xFFFF),
            ),
        )

    async def set_radar_sensitivity(
        self,
        session: UltraSession,
        sensitivity: int,
        *,
        exchange: dna.PacketExchange | None = None,
    ) -> UltraRadarStatus:
        """Set radar sensitivity and verify it through a device read-back."""
        if isinstance(sensitivity, bool) or sensitivity not in range(3):
            raise ValueError("radar sensitivity must be 0, 1, or 2")
        return await self._set_radar_field(
            session,
            "level_of_sensitivity",
            sensitivity,
            expected=sensitivity,
            read_value=lambda status: status.sensitivity,
            exchange=exchange,
        )

    async def set_radar_trigger_speed(
        self,
        session: UltraSession,
        trigger_speed: int,
        *,
        exchange: dna.PacketExchange | None = None,
    ) -> UltraRadarStatus:
        """Set radar trigger speed and verify it through a device read-back."""
        if isinstance(trigger_speed, bool) or trigger_speed not in range(3):
            raise ValueError("radar trigger speed must be 0, 1, or 2")
        return await self._set_radar_field(
            session,
            "triger_speed",
            trigger_speed,
            expected=trigger_speed,
            read_value=lambda status: status.trigger_speed,
            exchange=exchange,
        )

    async def set_radar_install_mode(
        self,
        session: UltraSession,
        install_mode: int,
        *,
        exchange: dna.PacketExchange | None = None,
    ) -> UltraRadarStatus:
        """Set radar installation mode and verify the device-read value."""
        if isinstance(install_mode, bool) or install_mode not in range(2):
            raise ValueError("radar install mode must be 0 or 1")
        return await self._set_radar_field(
            session,
            "install_mode",
            install_mode,
            expected=install_mode,
            read_value=lambda status: status.install_mode,
            exchange=exchange,
        )

    async def set_radar_height(
        self,
        session: UltraSession,
        height: int,
        *,
        exchange: dna.PacketExchange | None = None,
    ) -> UltraRadarStatus:
        """Set installation height in centimeters and verify the device-read value."""
        _validate_int_range(height, 0, 0xFFFF, "radar height")
        return await self._set_radar_field(
            session,
            "height",
            height,
            expected=height,
            read_value=lambda status: status.height,
            exchange=exchange,
        )

    async def set_radar_install_direction(
        self,
        session: UltraSession,
        install_direction: int,
        *,
        exchange: dna.PacketExchange | None = None,
    ) -> UltraRadarStatus:
        """Set cable orientation and verify the device-read value."""
        if isinstance(install_direction, bool) or install_direction not in range(2):
            raise ValueError("radar install direction must be 0 or 1")
        return await self._set_radar_field(
            session,
            "install_direction",
            install_direction,
            expected=install_direction,
            read_value=lambda status: status.install_direction,
            exchange=exchange,
        )

    async def set_radar_z_range(
        self,
        session: UltraSession,
        minimum: float,
        maximum: float,
        *,
        exchange: dna.PacketExchange | None = None,
    ) -> UltraRadarStatus:
        """Set the Z-axis detection range in meters and verify both limits."""
        minimum = _validate_float_range(minimum, MIN_Z_RANGE, MAX_Z_RANGE, "minimum Z range")
        maximum = _validate_float_range(maximum, MIN_Z_RANGE, MAX_Z_RANGE, "maximum Z range")
        if minimum >= maximum:
            raise ValueError("minimum Z range must be less than maximum Z range")

        expected = UltraRadarZRange(minimum=minimum, maximum=maximum)
        status = await self._set_radar_field(
            session,
            "z_range",
            json.dumps(
                {"min": minimum, "max": maximum},
                separators=(",", ":"),
            ),
            expected=expected,
            read_value=lambda value: value.z_range,
            exchange=exchange,
            values_match=_z_ranges_match,
        )
        return status

    async def set_radar_default_absence_delay(
        self,
        session: UltraSession,
        seconds: int,
        *,
        exchange: dna.PacketExchange | None = None,
    ) -> UltraRadarStatus:
        """Set the default absence delay and verify the device-read value."""
        _validate_int_range(seconds, 0, MAX_ABSENCE_DELAY, "default absence delay")
        return await self._set_radar_field(
            session,
            "delaytime",
            seconds,
            expected=seconds,
            read_value=lambda status: status.default_absence_delay,
            exchange=exchange,
        )

    async def set_radar_zone_absence_delay(
        self,
        session: UltraSession,
        zone: int,
        seconds: int,
        *,
        exchange: dna.PacketExchange | None = None,
    ) -> UltraRadarStatus:
        """Set one zone's absence delay and verify the device-read value."""
        if isinstance(zone, bool) or zone not in range(1, 5):
            raise ValueError("radar zone must be between 1 and 4")
        _validate_int_range(seconds, 0, MAX_ABSENCE_DELAY, "zone absence delay")
        return await self._set_radar_field(
            session,
            f"duration{zone}",
            seconds,
            expected=seconds,
            read_value=lambda status: status.zone_absence_delays[zone - 1],
            exchange=exchange,
        )

    async def _set_radar_field(
        self,
        session: UltraSession,
        field: str,
        value: object,
        *,
        expected: object,
        read_value: Callable[[UltraRadarStatus], object],
        exchange: dna.PacketExchange | None,
        values_match: Callable[[object, object], bool] | None = None,
    ) -> UltraRadarStatus:
        """Write one radar field and require a matching independent read-back."""
        radar_did = derive_ultra2_radar_did(session.device.mac)
        await self.send_command(
            session,
            emotion.build_set_status_frame(radar_did, {field: value}),
            exchange=exchange,
        )
        status = await self.get_radar_status(session, exchange=exchange)
        actual = read_value(status)
        matches = values_match(actual, expected) if values_match else actual == expected
        if not matches:
            raise UltraProtocolError(f"radar {field} read-back mismatch ({actual!r}, expected {expected!r})")
        return status

    async def send_command(
        self,
        session: UltraSession,
        command: bytes,
        *,
        try_all: bool = True,
        exchange: dna.PacketExchange | None = None,
    ) -> bytes:
        """Send an Ultra command over DNA."""
        if not session.session_key:
            raise UltraAuthError("missing DNA session key")
        last_error: Exception | None = None
        candidates = [
            (device_type, message_type)
            for device_type in _command_device_type_candidates(session)
            for message_type in _command_message_type_candidates(session)
        ]
        if not try_all:
            candidates = candidates[:1]
        for device_type, message_type in candidates:
            is_preferred = device_type == session.command_device_type and message_type == session.command_message_type
            header = dna.NetworkHeader(
                device_type=device_type,
                message_type=message_type,
                sequence=_next_command_sequence(session),
                mac=dna.mac_bytes(session.auth_mac or session.device.mac),
            )
            try:
                payload = await dna.send_encrypted(
                    session.device.ip,
                    session.device.port or self.default_port,
                    header,
                    command,
                    session.session_key,
                    timeout=(self.preferred_command_timeout if is_preferred else self.command_timeout),
                    exchange=exchange,
                )
                session.auth_device_type = device_type
                session.command_device_type = device_type
                session.command_message_type = message_type
                return payload
            except (OSError, dna.DNAError) as err:
                last_error = err
        raise UltraError(str(last_error) if last_error else "command failed")

    async def reauthenticate(
        self,
        session: UltraSession,
        *,
        protocol_mac: str | None = None,
        exchange: dna.PacketExchange | None = None,
    ) -> None:
        """Refresh a session key, optionally through a persistent UDP socket."""
        refreshed = await self.connect(
            session.device,
            protocol_mac=protocol_mac or session.auth_mac or session.device.mac,
            exchange=exchange,
        )
        session.session_key = refreshed.session_key
        session.auth_mac = refreshed.auth_mac
        session.auth_device_type = refreshed.auth_device_type
        session.auth_status = refreshed.auth_status
        session.auth_error = refreshed.auth_error
        session.last_auth_at = refreshed.last_auth_at
        session.last_seen = refreshed.last_seen

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
            # UDP connect selects a route without sending traffic. TEST-NET-1
            # avoids implying a dependency on any public network service.
            sock.connect(("192.0.2.1", 80))
            return str(sock.getsockname()[0])
    except OSError:
        return "127.0.0.1"


def _matches_ultra(device: UltraDevice) -> bool:
    if device.pid.lower() == PID_ULTRA2:
        return True
    return device.type_id in {TYPE_ULTRA2, TYPE_ULTRA2_LAN}


def derive_ultra2_protocol_mac(lan_mac: str) -> str:
    """Derive the Ultra2 DNA protocol MAC from its Wi-Fi station MAC."""
    mac = dna.mac_bytes(lan_mac)
    if not mac:
        raise ValueError(f"invalid Ultra2 LAN MAC: {lan_mac}")
    value = (int.from_bytes(mac, "big") + 2) & 0xFFFFFFFFFFFF
    return ":".join(f"{part:02x}" for part in value.to_bytes(6, "big"))


def derive_ultra2_radar_did(lan_mac: str) -> str:
    """Derive the Ultra2 radar peripheral DID from its Wi-Fi station MAC."""
    mac = dna.mac_bytes(lan_mac)
    if not mac:
        raise ValueError(f"invalid Ultra2 LAN MAC: {lan_mac}")
    radar_type = TYPE_ULTRA2_RADAR.to_bytes(4, "little")
    return (mac + radar_type + b"\x00\x00" + radar_type[:3] + b"\x01").hex()


def _required_int(
    payload: dict[str, object],
    key: str,
    *,
    valid_values: Iterable[int] | None = None,
) -> int:
    value = _optional_int(payload, key, valid_values=valid_values)
    if value is None:
        raise UltraProtocolError(f"missing radar {key}")
    return value


def _optional_int(
    payload: dict[str, object],
    key: str,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
    valid_values: Iterable[int] | None = None,
) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise UltraProtocolError(f"invalid radar {key}: {value!r}")
    if valid_values is not None and value not in valid_values:
        raise UltraProtocolError(f"invalid radar {key}: {value!r}")
    if minimum is not None and value < minimum:
        raise UltraProtocolError(f"invalid radar {key}: {value!r}")
    if maximum is not None and value > maximum:
        raise UltraProtocolError(f"invalid radar {key}: {value!r}")
    return value


def _optional_z_range(payload: dict[str, object]) -> UltraRadarZRange | None:
    raw = payload.get("z_range")
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as err:
            raise UltraProtocolError(f"invalid radar z_range: {raw!r}") from err
    if not isinstance(raw, dict):
        raise UltraProtocolError(f"invalid radar z_range: {raw!r}")
    minimum = _status_float(raw.get("min"), "z_range.min")
    maximum = _status_float(raw.get("max"), "z_range.max")
    if minimum >= maximum:
        raise UltraProtocolError(f"invalid radar z_range: minimum {minimum!r} is not less than maximum {maximum!r}")
    return UltraRadarZRange(minimum=minimum, maximum=maximum)


def _status_float(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise UltraProtocolError(f"invalid radar {field}: {value!r}")
    result = float(value)
    if not math.isfinite(result):
        raise UltraProtocolError(f"invalid radar {field}: {value!r}")
    return result


def _validate_int_range(value: int, minimum: int, maximum: int, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")


def _validate_float_range(
    value: float,
    minimum: float,
    maximum: float,
    field: str,
) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return result


def _z_ranges_match(actual: object, expected: object) -> bool:
    if not isinstance(actual, UltraRadarZRange) or not isinstance(expected, UltraRadarZRange):
        return False
    return math.isclose(actual.minimum, expected.minimum, abs_tol=0.01) and math.isclose(
        actual.maximum,
        expected.maximum,
        abs_tol=0.01,
    )


def _model_for_device_type(device_type: int) -> str:
    del device_type
    return DISPLAY_MODEL_ULTRA2


def _auth_device_type_candidates(device_type: int) -> list[int]:
    if device_type in {TYPE_ULTRA2, TYPE_ULTRA2_LAN}:
        values = [device_type, TYPE_ULTRA2, TYPE_ULTRA2_LAN]
    else:
        values = [TYPE_ULTRA2, TYPE_ULTRA2_LAN]
    return _dedupe_ints(values)


def _command_device_type_candidates(session: UltraSession) -> list[int]:
    values = [
        session.command_device_type,
        session.auth_device_type,
        session.device.type_id,
        TYPE_ULTRA2,
        TYPE_ULTRA2_LAN,
    ]
    return _dedupe_ints(value for value in values if value)


def _command_message_type_candidates(session: UltraSession) -> list[int]:
    return _dedupe_ints([session.command_message_type, dna.MESSAGE_TYPE_COMMAND, 0x03E9])


def _next_command_sequence(session: UltraSession) -> int:
    if session.command_sequence <= 0:
        session.command_sequence = time.time_ns() & 0xFFFF
    session.command_sequence = (session.command_sequence + 1) & 0xFFFF
    if session.command_sequence == 0:
        session.command_sequence = 1
    return session.command_sequence


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
