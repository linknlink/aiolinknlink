"""eMotion Ultra gateway and SubdeviceFrame protocol helpers."""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass
from typing import Any

from . import dna

GATEWAY_CMD_GET_STATE_WITH_PARAMS = 0x24
GATEWAY_CMD_SET_MQTT = 0x25
GATEWAY_CMD_GET_STATE = 0x26
GATEWAY_CMD_SET_LOCAL_UDP_UPLOAD = 20000

SUBDEVICE_FRAME_HEADER = 0x5A5AA5A5
SUBDEVICE_VERSION = 0x0000
SUBDEVICE_FRAME_MIN_LEN = 12

CMD_GET_STATUS = 0x0B01
CMD_SET_STATUS = 0x0B02
CMD_STATUS_RESPONSE = 0x0B03
CMD_SCAN_SUBDEVICES = 0x0B04
CMD_GET_WAITING_LIST = 0x0B08
CMD_ADD_SUBDEVICE = 0x0B0A
CMD_GET_SUBDEVICE_LIST = 0x0B0E
CMD_SUBDEVICE_LIST_RESPONSE = 0x0B0F
CMD_GATEWAY_REPORT_STATUS = 0x0B20
CMD_GATEWAY_REPORT_RESPONSE = 0x0B21


class EmotionError(Exception):
    """Base emotion protocol error."""


@dataclass(slots=True)
class SubdeviceFrame:
    """Parsed SubdeviceFrame."""

    command_type: int
    version: int
    payload: bytes


@dataclass(slots=True)
class GatewayState:
    """Parsed gateway state response."""

    online: bool
    state: str
    attributes: dict[str, Any]
    raw: dict[str, Any] | None = None


@dataclass(slots=True)
class GatewayResponse:
    """Parsed gateway response."""

    command: int
    gateway_state: GatewayState | None = None
    subdevice_frame: SubdeviceFrame | None = None
    raw_payload: bytes = b""


def build_gateway_get_state_command(params: bytes | None = None) -> bytes:
    """Build gateway get-state command."""
    if not params:
        return bytes([0x26, 0, 0, 0, 0x24, 0, 0, 0])
    return bytes([0x24, 0, 0, 0]) + params


def build_gateway_set_mqtt_command(account: str, port: int, password: str, url: str) -> bytes:
    """Build gateway MQTT configuration command."""
    payload = json.dumps(
        {"account": account, "portnumber": port, "password": password, "URL": url},
        separators=(",", ":"),
    ).encode()
    return bytes([0x25, 0, 0, 0]) + payload


def build_local_udp_upload_command(port: int, timeout: int, ip: int | None = None) -> bytes:
    """Build local UDP position-push subscription command."""
    payload: dict[str, Any] = {"port": port, "timeout": timeout}
    if ip is not None:
        payload["ip"] = ip
    body = json.dumps(payload, separators=(",", ":")).encode()
    return struct.pack("<I", GATEWAY_CMD_SET_LOCAL_UDP_UPLOAD) + body


def build_subdevice_frame(command_type: int, payload: Any = None) -> bytes:
    """Build SubdeviceFrame with JSON payload."""
    payload_bytes = _marshal_payload(payload)
    frame = bytearray(SUBDEVICE_FRAME_MIN_LEN + len(payload_bytes))
    struct.pack_into("<I", frame, 0, SUBDEVICE_FRAME_HEADER)
    struct.pack_into("<H", frame, 6, command_type)
    struct.pack_into("<H", frame, 8, len(payload_bytes))
    struct.pack_into("<H", frame, 10, SUBDEVICE_VERSION)
    frame[12:] = payload_bytes
    write_subdevice_checksum(frame)
    return bytes(frame)


def parse_subdevice_frame(data: bytes) -> SubdeviceFrame:
    """Parse a SubdeviceFrame."""
    if len(data) < SUBDEVICE_FRAME_MIN_LEN:
        raise EmotionError(f"subdevice frame too short: {len(data)}")
    if struct.unpack_from("<I", data, 0)[0] != SUBDEVICE_FRAME_HEADER:
        raise EmotionError("invalid subdevice frame header")
    if not verify_subdevice_checksum(data):
        raise EmotionError("invalid subdevice frame checksum")
    payload_len = struct.unpack_from("<H", data, 8)[0]
    payload_end = SUBDEVICE_FRAME_MIN_LEN + payload_len
    if payload_end > len(data):
        raise EmotionError("subdevice payload exceeds frame length")
    return SubdeviceFrame(
        command_type=struct.unpack_from("<H", data, 6)[0],
        version=struct.unpack_from("<H", data, 10)[0],
        payload=bytes(data[SUBDEVICE_FRAME_MIN_LEN:payload_end]),
    )


def calc_subdevice_checksum(frame: bytes | bytearray) -> int:
    """Calculate SubdeviceFrame checksum."""
    return dna.checksum(frame, 4)


def write_subdevice_checksum(frame: bytearray) -> int:
    """Write SubdeviceFrame checksum."""
    return dna.write_checksum_le(frame, 4)


def verify_subdevice_checksum(frame: bytes | bytearray) -> bool:
    """Verify SubdeviceFrame checksum."""
    return dna.verify_checksum_le(frame, 4)


def build_get_status_frame(did: str) -> bytes:
    """Build get-status frame for a subdevice."""
    return build_subdevice_frame(CMD_GET_STATUS, {"did": did, "param1": "", "param2": ""})


def build_set_status_frame(did: str, fields: dict[str, Any]) -> bytes:
    """Build set-status frame for a subdevice."""
    payload = {"did": did}
    payload.update(fields)
    return build_subdevice_frame(CMD_SET_STATUS, payload)


def build_scan_subdevices_frame(pid: str) -> bytes:
    """Build scan-subdevices frame."""
    return build_subdevice_frame(CMD_SCAN_SUBDEVICES, {"pid": pid})


def build_get_waiting_list_frame() -> bytes:
    """Build get waiting list frame."""
    return build_subdevice_frame(CMD_GET_WAITING_LIST, None)


def build_get_subdevice_list_frame() -> bytes:
    """Build get subdevice list frame."""
    return build_subdevice_frame(CMD_GET_SUBDEVICE_LIST, None)


def parse_gateway_state_response(data: bytes) -> GatewayResponse:
    """Parse gateway response payload."""
    if len(data) < 4:
        raise EmotionError(f"gateway response too short: {len(data)}")
    command = struct.unpack_from("<I", data, 0)[0]
    response = GatewayResponse(command=command, raw_payload=bytes(data))
    body = _trim_right_zero(data[4:])
    if command in (GATEWAY_CMD_SET_MQTT, GATEWAY_CMD_GET_STATE) and len(body) >= SUBDEVICE_FRAME_MIN_LEN:
        try:
            response.subdevice_frame = parse_subdevice_frame(body)
            return response
        except EmotionError:
            pass
    if command == GATEWAY_CMD_GET_STATE:
        response.gateway_state = parse_gateway_json_state(body)
        return response
    if command == GATEWAY_CMD_GET_STATE_WITH_PARAMS:
        response.gateway_state = parse_gateway_binary_state(body)
        return response
    raise EmotionError(f"unsupported gateway response command: 0x{command:x}")


def parse_gateway_json_state(data: bytes) -> GatewayState:
    """Parse JSON gateway state."""
    data = _trim_to_last_json_brace(_trim_right_zero(data))
    if not data:
        raise EmotionError("empty gateway JSON state")
    try:
        raw = json.loads(data.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise EmotionError(f"parse gateway JSON state: {err}") from err
    if not isinstance(raw, dict):
        raise EmotionError("gateway JSON state is not an object")
    return _gateway_state_from_map(raw)


def parse_gateway_binary_state(data: bytes) -> GatewayState:
    """Parse compact binary gateway state."""
    attrs: dict[str, Any] = {"raw_hex_len": len(data)}
    if len(data) >= 2:
        attrs["tempsensor"] = data[0] * 100 + data[1]
    if len(data) >= 4:
        attrs["humsensor"] = data[2] * 100 + data[3]
    return GatewayState(online=True, state="online", attributes=attrs)


def parse_subdevice_json_payload(frame: SubdeviceFrame) -> dict[str, Any]:
    """Parse SubdeviceFrame JSON payload."""
    payload = _trim_right_zero(frame.payload)
    if not payload:
        return {}
    try:
        raw = json.loads(payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise EmotionError(f"parse subdevice JSON payload: {err}") from err
    if not isinstance(raw, dict):
        raise EmotionError("subdevice JSON payload is not an object")
    return raw


def parse_local_udp_position_payload(payload: bytes) -> dict[str, Any]:
    """Parse Ultra local UDP position push payload."""
    try:
        data = json.loads(payload.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        repaired = _repair_unescaped_detect_position(payload.decode(errors="ignore"))
        if not repaired:
            raise
        data = json.loads(repaired)
    if not isinstance(data, dict) or not str(data.get("detect_position", "")).strip():
        raise EmotionError("missing detect_position")
    derive_distances_from_position(data, str(data["detect_position"]))
    return data


def derive_distances_from_position(values: dict[str, Any], detect_position: str) -> None:
    """Derive distance and target_distance from detect_position JSON."""
    positions = _parse_target_positions(detect_position)
    if not positions:
        return
    min_horizontal = min(math.hypot(item["x"], item["y"]) for item in positions)
    min_3d = min(math.sqrt(item["x"] ** 2 + item["y"] ** 2 + item["z"] ** 2) for item in positions)
    values["distance"] = round(min_horizontal * 100)
    values["target_distance"] = round(min_3d * 100)
    values["range"] = values["target_distance"]


def _gateway_state_from_map(raw: dict[str, Any]) -> GatewayState:
    attrs = {key: value for key, value in raw.items() if key not in {"lb_online1", "online", "offline"}}
    online_value = _first_value(raw, "lb_online1", "online")
    if online_value is None and "offline" in raw:
        online_value = not _number_equals(raw["offline"], 1)
    if online_value is None and ("tempsensor" in raw or "temp" in raw):
        temp = _first_value(raw, "tempsensor", "temp")
        hum = _first_value(raw, "humsensor", "hum")
        online = not _number_equals(temp, 0) or not _number_equals(hum, 0)
    else:
        online = _value_is_online(online_value) if online_value is not None else True
    ip = _first_string(raw, "dev_ip", "ip", "URL", "url")
    if ip:
        attrs["ip"] = ip
        attrs["dev_ip"] = ip
    rssi = _first_value(raw, "wifi_rssi", "rssi")
    if rssi is not None:
        attrs["wifi_rssi"] = rssi
    return GatewayState(online=online, state="online" if online else "offline", attributes=attrs, raw=raw)


def _marshal_payload(payload: Any) -> bytes:
    if payload is None:
        return b"{}"
    if isinstance(payload, bytes):
        return bytes(payload)
    if isinstance(payload, str):
        return payload.encode()
    return json.dumps(payload, separators=(",", ":")).encode()


def _trim_right_zero(data: bytes) -> bytes:
    return data.rstrip(b"\x00")


def _trim_to_last_json_brace(data: bytes) -> bytes:
    index = data.rfind(b"}")
    return data[: index + 1] if index >= 0 else data


def _first_value(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in raw:
            return raw[key]
    return None


def _first_string(raw: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _value_is_online(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.lower() in {"1", "true", "online"}
    return value is not None


def _number_equals(value: Any, expected: float) -> bool:
    return isinstance(value, (int, float)) and float(value) == expected


def _parse_target_positions(detect_position: str) -> list[dict[str, float]]:
    if not detect_position.strip() or detect_position.strip() == "[]":
        return []
    try:
        raw = json.loads(detect_position)
    except json.JSONDecodeError:
        return []
    if not isinstance(raw, list):
        return []
    out: list[dict[str, float]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        x = _as_float(item.get("x"))
        y = _as_float(item.get("y"))
        z = _as_float(item.get("z"))
        if x == 0 and y == 0 and z == 0:
            continue
        out.append({"x": x, "y": y, "z": z})
    return out


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _repair_unescaped_detect_position(raw: str) -> str:
    key = '"detect_position":"'
    idx = raw.find(key)
    if idx < 0:
        return ""
    start_rel = raw[idx + len(key) :].find("[")
    end = raw.rfind("]")
    if start_rel < 0 or end < 0:
        return ""
    start = idx + len(key) + start_rel
    if end < start:
        return ""
    inner = raw[start : end + 1].replace("\\", "\\\\").replace('"', '\\"')
    return raw[:start] + inner + raw[end + 1 :]
