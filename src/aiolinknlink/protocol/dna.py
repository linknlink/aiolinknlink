"""LinknLink DNA LAN protocol primitives and asynchronous transport."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

DISCOVERY_PACKET_SIZE = 0x38
HEADER_SIZE = 0x38
BLC_NETWORK_HEADER_SIZE = 0x30
SHORT_DISCOVERY_SIZE = 0x30
AES_HEADER_SIZE = 8
CHECKSUM_INIT = 0xBEAF
DEFAULT_PORT = 80

MESSAGE_TYPE_DISCOVERY_REQUEST = 0x06
MESSAGE_TYPE_DISCOVERY_RESPONSE = 0x07
MESSAGE_TYPE_AUTH = 0x65
MESSAGE_TYPE_COMMAND = 0x6A

AUTH_PAIR_INFO_SIZE = 0x64
TERMINAL_TYPE_IOT = 2

INITIAL_KEY = bytes([0x09, 0x76, 0x28, 0x34, 0x3F, 0xE9, 0x9E, 0x23, 0x76, 0x5C, 0x15, 0x13, 0xAC, 0xCF, 0x8B, 0x02])
INITIAL_IV = bytes([0x56, 0x2E, 0x17, 0x99, 0x6D, 0x09, 0x3D, 0x28, 0xDD, 0xB3, 0xBA, 0x69, 0x5A, 0x2E, 0x6F, 0x58])

MAGIC = bytes([0x5A, 0xA5, 0xAA, 0x55, 0x5A, 0xA5, 0xAA, 0x55])
PACKET_CHECKSUM_OFFSET = 0x20
DISCOVERY_CHECKSUM_OFFSET = 0x20


class DNAError(Exception):
    """Base DNA protocol error."""


class LegacyShortResponseError(DNAError):
    """Device returned a short legacy error frame."""

    def __init__(self, status: int, device_type: int, message_type: int) -> None:
        super().__init__(
            f"legacy DNA short response without encrypted payload: "
            f"status=0x{status:04x} device_type=0x{device_type:04x} message_type=0x{message_type:04x}"
        )
        self.status = status
        self.device_type = device_type
        self.message_type = message_type


@dataclass(slots=True)
class NetworkHeader:
    """DNA network packet header."""

    other: bytes = field(default_factory=lambda: b"\x00" * 8)
    serv: bytes = field(default_factory=lambda: b"\x00" * 8)
    checksum: int = 0
    status: int = 0
    device_type: int = 0
    message_type: int = 0
    sequence: int = 0
    mac: bytes = field(default_factory=lambda: b"\x00" * 6)
    device_id: bytes = field(default_factory=lambda: b"\x00" * 4)
    payload_checksum: int = 0

    def marshal(self) -> bytes:
        """Serialize a full 0x38-byte DNA header."""
        buf = bytearray(HEADER_SIZE)
        buf[0:8] = MAGIC
        buf[0x08:0x10] = _fixed_bytes(self.other, 8)
        buf[0x10:0x18] = _fixed_bytes(self.serv, 8)
        struct.pack_into("<H", buf, 0x20, self.checksum & 0xFFFF)
        struct.pack_into("<H", buf, 0x22, self.status & 0xFFFF)
        struct.pack_into("<H", buf, 0x24, self.device_type & 0xFFFF)
        struct.pack_into("<H", buf, 0x26, self.message_type & 0xFFFF)
        struct.pack_into("<H", buf, 0x28, self.sequence & 0xFFFF)
        buf[0x2A:0x30] = _fixed_bytes(self.mac, 6)
        buf[0x30:0x34] = _fixed_bytes(self.device_id, 4)
        struct.pack_into("<H", buf, 0x34, self.payload_checksum & 0xFFFF)
        return bytes(buf)


@dataclass(slots=True)
class AESHeader:
    """AES payload header inside DNA packets."""

    terminal_id: int
    payload_checksum: int
    reserved: int


@dataclass(slots=True)
class DiscoveredDevice:
    """Device returned by DNA discovery."""

    id: str
    ip: str
    port: int
    mac: str = ""
    device_type: int = 0
    message_type: int = 0
    name: str = ""
    raw: bytes = b""


def checksum(data: bytes | bytearray, checksum_offset: int = -1) -> int:
    """Calculate DNA checksum."""
    total = CHECKSUM_INIT
    for index, value in enumerate(data):
        if checksum_offset >= 0 and index in (checksum_offset, checksum_offset + 1):
            continue
        total += value
    return total & 0xFFFF


def write_checksum_le(data: bytearray, checksum_offset: int) -> int:
    """Write checksum into a mutable buffer."""
    value = checksum(data, checksum_offset)
    struct.pack_into("<H", data, checksum_offset, value)
    return value


def verify_checksum_le(data: bytes | bytearray, checksum_offset: int) -> bool:
    """Verify checksum at offset."""
    if checksum_offset < 0 or checksum_offset + 2 > len(data):
        return False
    expected = int(struct.unpack_from("<H", data, checksum_offset)[0])
    return expected == checksum(data, checksum_offset)


def payload_checksum(payload: bytes) -> int:
    """Checksum used by AES payload header."""
    return checksum(payload, -1)


def build_discovery_packet(local_ip: str, local_port: int, now: datetime | None = None) -> bytes:
    """Build a full DNA discovery request."""
    now = now or datetime.now()
    ip = ipaddress.ip_address(local_ip)
    if ip.version != 4:
        raise DNAError(f"local_ip must be IPv4: {local_ip}")
    if local_port < 0 or local_port > 0xFFFF:
        raise DNAError(f"invalid local port: {local_port}")

    ip_bytes = ip.packed
    packet = bytearray(DISCOVERY_PACKET_SIZE)
    packet[0:8] = MAGIC
    struct.pack_into("<I", packet, 0x08, int(now.timestamp()))
    struct.pack_into("<H", packet, 0x0C, now.year)
    packet[0x0E] = now.month
    packet[0x0F] = now.day
    packet[0x10] = now.hour
    packet[0x11] = now.minute
    packet[0x12] = now.second
    packet[0x13] = now.weekday()
    packet[0x18] = ip_bytes[3]
    packet[0x19] = ip_bytes[2]
    packet[0x1A] = ip_bytes[1]
    packet[0x1B] = ip_bytes[0]
    struct.pack_into("<H", packet, 0x1C, local_port)
    struct.pack_into("<H", packet, 0x26, MESSAGE_TYPE_DISCOVERY_REQUEST)
    write_checksum_le(packet, DISCOVERY_CHECKSUM_OFFSET)
    return bytes(packet)


def build_short_discovery_packet(local_ip: str, local_port: int, now: datetime | None = None) -> bytes:
    """Build a short 0x30-byte discovery packet variant."""
    packet = bytearray(build_discovery_packet(local_ip, local_port, now)[:SHORT_DISCOVERY_SIZE])
    write_checksum_le(packet, DISCOVERY_CHECKSUM_OFFSET)
    return bytes(packet)


def parse_network_header(data: bytes) -> NetworkHeader:
    """Parse a full DNA header."""
    if len(data) < HEADER_SIZE:
        raise DNAError(f"packet too short for header: {len(data)}")
    if data[0:8] != MAGIC:
        raise DNAError("invalid packet magic")
    return NetworkHeader(
        other=bytes(data[0x08:0x10]),
        serv=bytes(data[0x10:0x18]),
        checksum=struct.unpack_from("<H", data, 0x20)[0],
        status=struct.unpack_from("<H", data, 0x22)[0],
        device_type=struct.unpack_from("<H", data, 0x24)[0],
        message_type=struct.unpack_from("<H", data, 0x26)[0],
        sequence=struct.unpack_from("<H", data, 0x28)[0],
        mac=bytes(data[0x2A:0x30]),
        device_id=bytes(data[0x30:0x34]),
        payload_checksum=struct.unpack_from("<H", data, 0x34)[0],
    )


def build_packet(header: NetworkHeader, encrypted_payload: bytes) -> bytes:
    """Build a legacy full-header DNA packet."""
    packet = bytearray(header.marshal() + encrypted_payload)
    write_checksum_le(packet, PACKET_CHECKSUM_OFFSET)
    return bytes(packet)


def parse_packet(data: bytes) -> tuple[NetworkHeader, bytes]:
    """Parse a legacy full-header DNA packet."""
    header = parse_network_header(data)
    if not verify_checksum_le(data, PACKET_CHECKSUM_OFFSET):
        raise DNAError("invalid packet checksum")
    return header, bytes(data[HEADER_SIZE:])


def build_blc_packet(header: NetworkHeader, payload: bytes) -> bytes:
    """Build a compact BLC DNA packet."""
    packet = bytearray(BLC_NETWORK_HEADER_SIZE + len(payload))
    packet[0:8] = MAGIC
    packet[0x08:0x10] = _fixed_bytes(header.other, 8)
    packet[0x18:0x20] = _fixed_bytes(header.serv, 8)
    struct.pack_into("<H", packet, 0x20, header.checksum & 0xFFFF)
    struct.pack_into("<H", packet, 0x22, header.status & 0xFFFF)
    struct.pack_into("<H", packet, 0x24, header.device_type & 0xFFFF)
    struct.pack_into("<H", packet, 0x26, header.message_type & 0xFFFF)
    struct.pack_into("<H", packet, 0x28, header.sequence & 0xFFFF)
    packet[0x2A:0x30] = _fixed_bytes(header.mac, 6)
    packet[BLC_NETWORK_HEADER_SIZE:] = payload
    write_checksum_le(packet, PACKET_CHECKSUM_OFFSET)
    return bytes(packet)


def parse_blc_packet(data: bytes) -> tuple[NetworkHeader, bytes]:
    """Parse a compact BLC DNA packet."""
    if len(data) < BLC_NETWORK_HEADER_SIZE:
        raise DNAError(f"BLC packet too short for header: {len(data)}")
    if data[0:8] != MAGIC:
        raise DNAError("invalid BLC packet magic")
    if not verify_checksum_le(data, PACKET_CHECKSUM_OFFSET):
        raise DNAError("invalid BLC packet checksum")
    header = NetworkHeader(
        other=bytes(data[0x08:0x10]),
        serv=bytes(data[0x18:0x20]),
        checksum=struct.unpack_from("<H", data, 0x20)[0],
        status=struct.unpack_from("<H", data, 0x22)[0],
        device_type=struct.unpack_from("<H", data, 0x24)[0],
        message_type=struct.unpack_from("<H", data, 0x26)[0],
        sequence=struct.unpack_from("<H", data, 0x28)[0],
        mac=bytes(data[0x2A:0x30]),
    )
    return header, bytes(data[BLC_NETWORK_HEADER_SIZE:])


def build_aes_payload(payload: bytes) -> bytes:
    """Build unencrypted AES payload body."""
    return struct.pack("<IHH", 1, payload_checksum(payload), 0) + payload


def parse_aes_payload(data: bytes) -> tuple[AESHeader, bytes]:
    """Parse an unencrypted AES payload body."""
    if len(data) < AES_HEADER_SIZE:
        raise DNAError(f"AES payload too short: {len(data)}")
    terminal_id, checksum_value, reserved = struct.unpack_from("<IHH", data, 0)
    payload = bytes(data[AES_HEADER_SIZE:])
    if checksum_value != payload_checksum(payload):
        raise DNAError("invalid AES payload checksum")
    return AESHeader(terminal_id, checksum_value, reserved), payload


def build_blc_encrypted_payload(payload: bytes, key: bytes) -> bytes:
    """Build encrypted BLC payload."""
    encrypted = encrypt_aes_cbc_zero_padding(payload, key, INITIAL_IV)
    return struct.pack("<IHH", 1, payload_checksum(payload), 0) + encrypted


def parse_blc_encrypted_payload(data: bytes, key: bytes) -> tuple[AESHeader, bytes]:
    """Parse encrypted BLC payload."""
    if len(data) < AES_HEADER_SIZE:
        raise DNAError(f"AES payload too short: {len(data)}")
    terminal_id, checksum_value, reserved = struct.unpack_from("<IHH", data, 0)
    plain = decrypt_aes_cbc_no_padding(data[AES_HEADER_SIZE:], key, INITIAL_IV)
    payload = plain.rstrip(b"\x00")
    if checksum_value != payload_checksum(payload):
        raise DNAError("invalid AES payload checksum")
    return AESHeader(terminal_id, checksum_value, reserved), payload


def encrypt_aes_cbc_pkcs7(plain: bytes, key: bytes, iv: bytes) -> bytes:
    """Encrypt with AES-CBC and PKCS7 padding."""
    return _crypt_cbc(_pkcs7_pad(plain, 16), key, iv, encrypt=True)


def decrypt_aes_cbc_pkcs7(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    """Decrypt with AES-CBC and PKCS7 padding."""
    return _pkcs7_unpad(_crypt_cbc(ciphertext, key, iv, encrypt=False), 16)


def encrypt_aes_cbc_zero_padding(plain: bytes, key: bytes, iv: bytes) -> bytes:
    """Encrypt with AES-CBC and zero padding."""
    padded_len = len(plain)
    if padded_len < 16:
        padded_len = 16
    elif padded_len % 16:
        padded_len += 16 - padded_len % 16
    return _crypt_cbc(plain + b"\x00" * (padded_len - len(plain)), key, iv, encrypt=True)


def decrypt_aes_cbc_no_padding(ciphertext: bytes, key: bytes, iv: bytes) -> bytes:
    """Decrypt with AES-CBC without removing padding."""
    return _crypt_cbc(ciphertext, key, iv, encrypt=False)


def build_auth_payload(
    mac: bytes,
    device_type: int,
    host: str = "",
    gateway_server: str = "",
    heartbeat_server: str = "",
    terminal_name: str = "linknlink-ha",
) -> bytes:
    """Build DNA auth payload."""
    if len(mac) != 6:
        raise DNAError(f"invalid mac length: {len(mac)}")
    if host:
        ipaddress.ip_address(host)
    payload = bytearray(AUTH_PAIR_INFO_SIZE)
    for index in range(24):
        payload[4 + index] = mac[index % len(mac)]
    struct.pack_into("<H", payload, 28, TERMINAL_TYPE_IOT)
    struct.pack_into("<H", payload, 30, 0)
    payload[32:48] = b"1" * 16
    payload[48:72] = terminal_name.encode()[:24].ljust(24, b"\x00")
    struct.pack_into("<I", payload, 80, 0)
    payload[84:100] = calculate_authcode(mac, device_type, host)

    server_info: dict[str, str] = {}
    if gateway_server:
        server_info["http"] = gateway_server
    if heartbeat_server:
        server_info["tcp"] = heartbeat_server
    if not server_info:
        return bytes(payload)
    return bytes(payload) + json.dumps(server_info, separators=(",", ":")).encode() + b"\x00"


def calculate_authcode(mac: bytes, device_type: int, host: str = "") -> bytes:
    """Calculate authcode used in the pairing payload."""
    del host
    authcode = bytearray(16)
    if len(mac) != 6:
        return bytes(authcode)
    for index in range(6):
        authcode[index] = mac[5 - index]
    authcode[6] = device_type & 0xFF
    authcode[7] = (device_type >> 8) & 0xFF
    for index in range(8, 16):
        authcode[index] = mac[index % 6] ^ (device_type & 0xFF)
    return bytes(authcode)


def extract_session_key(payload: bytes) -> bytes:
    """Extract session AES key from auth response payload."""
    if len(payload) < 0x14:
        raise DNAError(f"auth response too short for session key: {len(payload)}")
    return bytes(payload[0x04:0x14])


def parse_discovery_device_response(
    data: bytes,
    remote_ip: str,
    remote_port: int,
    default_port: int = DEFAULT_PORT,
) -> DiscoveredDevice:
    """Parse a DNA discovery response into a device object."""
    if len(data) < SHORT_DISCOVERY_SIZE:
        raise DNAError(f"discovery response too short: {len(data)}")
    if data[0] not in (0x5A, 0x55):
        raise DNAError("invalid discovery response magic")

    device = DiscoveredDevice(id=remote_ip, ip=remote_ip, port=remote_port or default_port, raw=bytes(data))
    legacy = _parse_legacy_discovery_device(data, remote_ip, remote_port, default_port)
    if legacy is not None:
        return legacy

    if len(data) >= HEADER_SIZE:
        try:
            header = parse_network_header(data)
        except DNAError:
            header = None
        if header is not None:
            device.device_type = header.device_type
            device.message_type = header.message_type
            device.mac = format_mac(header.mac)
            if device.mac:
                device.id = device.mac
    else:
        device.device_type = struct.unpack_from("<H", data, 0x24)[0]
        device.message_type = struct.unpack_from("<H", data, 0x26)[0]
        device.mac = format_mac(data[0x2A:0x30])
        if device.mac:
            device.id = device.mac
    if len(data) > HEADER_SIZE:
        device.name = _parse_discovery_name(data[HEADER_SIZE:])
    return device


def format_mac(mac: bytes) -> str:
    """Format six bytes as a colon-separated MAC address."""
    if len(mac) != 6 or mac == b"\x00" * 6:
        return ""
    return ":".join(f"{part:02x}" for part in mac)


def mac_bytes(value: str) -> bytes:
    """Parse a MAC string."""
    compact = value.replace(":", "").replace("-", "").strip().lower()
    if len(compact) != 12:
        return b""
    try:
        return bytes.fromhex(compact)
    except ValueError:
        return b""


async def send_encrypted(
    target_ip: str,
    target_port: int,
    header: NetworkHeader,
    payload: bytes,
    key: bytes,
    timeout: float = 5,
    accept: Callable[[bytes], bool] | None = None,
) -> bytes:
    """Asynchronously send an encrypted DNA command and decrypt the response."""
    if key == INITIAL_KEY:
        return await _send_legacy_encrypted(target_ip, target_port, header, payload, key, timeout, accept)
    return await _send_blc_encrypted(target_ip, target_port, header, payload, key, timeout, accept)


def response_summary(data: bytes) -> str:
    """Return a short diagnostic string for a DNA response."""
    prefix = data[:32].hex()
    summary = f"len={len(data)} prefix={prefix}"
    if len(data) >= 0x28:
        summary += (
            f" fields={{status=0x{struct.unpack_from('<H', data, 0x22)[0]:04x} "
            f"device_type=0x{struct.unpack_from('<H', data, 0x24)[0]:04x} "
            f"message_type=0x{struct.unpack_from('<H', data, 0x26)[0]:04x}}}"
        )
    return summary


async def _send_legacy_encrypted(
    target_ip: str,
    target_port: int,
    header: NetworkHeader,
    payload: bytes,
    key: bytes,
    timeout: float,
    accept: Callable[[bytes], bool] | None,
) -> bytes:
    header.payload_checksum = payload_checksum(payload)
    encrypted = encrypt_aes_cbc_pkcs7(build_aes_payload(payload), key, INITIAL_IV)
    packet = build_packet(header, encrypted)
    response = await _send_packet(target_ip, target_port, packet, timeout, accept)
    _raise_legacy_short_response(response)
    _, encrypted_payload = parse_packet(response)
    try:
        decrypted = decrypt_aes_cbc_pkcs7(encrypted_payload, key, INITIAL_IV)
        _, response_payload = parse_aes_payload(decrypted)
        return response_payload
    except DNAError as err:
        try:
            fallback = decrypt_aes_cbc_no_padding(encrypted_payload, key, INITIAL_IV)
            try:
                _, payload_from_fallback = parse_aes_payload(fallback)
                return payload_from_fallback
            except DNAError:
                trimmed = fallback.rstrip(b"\x00")
                if len(trimmed) >= 0x14:
                    return trimmed
        except DNAError:
            pass
        raise DNAError(f"{err}; response_summary={response_summary(response)}") from err


async def _send_blc_encrypted(
    target_ip: str,
    target_port: int,
    header: NetworkHeader,
    payload: bytes,
    key: bytes,
    timeout: float,
    accept: Callable[[bytes], bool] | None,
) -> bytes:
    header.payload_checksum = payload_checksum(payload)
    encrypted_payload = build_blc_encrypted_payload(payload, key)
    packet = build_blc_packet(header, encrypted_payload)
    response = await _send_packet(target_ip, target_port, packet, timeout, accept)
    _raise_legacy_short_response(response)
    _, response_body = parse_blc_packet(response)
    try:
        _, response_payload = parse_blc_encrypted_payload(response_body, key)
    except DNAError as err:
        raise DNAError(f"{err}; response_summary={response_summary(response)}") from err
    return response_payload


async def _send_packet(
    target_ip: str,
    target_port: int,
    packet: bytes,
    timeout: float,
    accept: Callable[[bytes], bool] | None,
) -> bytes:
    if timeout <= 0:
        raise DNAError("timeout must be greater than zero")

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    try:
        await loop.sock_sendto(sock, packet, (target_ip, target_port))
        while (remaining := deadline - loop.time()) > 0:
            try:
                candidate, remote = await asyncio.wait_for(
                    loop.sock_recvfrom(sock, 8192),
                    timeout=remaining,
                )
            except TimeoutError:
                break
            if remote[0] != target_ip:
                continue
            if accept is not None and not accept(candidate):
                continue
            return candidate
    finally:
        sock.close()
    raise DNAError(f"timeout waiting for DNA response from {target_ip}:{target_port}")


def _raise_legacy_short_response(data: bytes) -> None:
    if len(data) == HEADER_SIZE and len(data) >= 0x28 and data[0:2] == b"\x5a\xa5":
        raise LegacyShortResponseError(
            struct.unpack_from("<H", data, 0x22)[0],
            struct.unpack_from("<H", data, 0x24)[0],
            struct.unpack_from("<H", data, 0x26)[0],
        )


def _parse_legacy_discovery_device(
    data: bytes, remote_ip: str, remote_port: int, default_port: int
) -> DiscoveredDevice | None:
    if len(data) < 0x40 or data[0:8] != MAGIC:
        return None
    if struct.unpack_from("<H", data, 0x26)[0] != MESSAGE_TYPE_DISCOVERY_RESPONSE:
        return None
    device_type = struct.unpack_from(">H", data, 0x34)[0]
    if device_type == 0:
        return None
    mac = bytes(reversed(data[0x3A:0x40]))
    name = _parse_discovery_name(data[0x40:])
    formatted = format_mac(mac)
    return DiscoveredDevice(
        id=formatted or remote_ip,
        ip=remote_ip,
        port=remote_port or default_port,
        mac=formatted,
        device_type=device_type,
        message_type=MESSAGE_TYPE_DISCOVERY_RESPONSE,
        name=name,
        raw=bytes(data),
    )


def _parse_discovery_name(extra: bytes) -> str:
    out = bytearray()
    for value in extra:
        if value == 0:
            break
        if 0x20 <= value <= 0x7E:
            out.append(value)
    return out.decode(errors="ignore")


def _crypt_cbc(data: bytes, key: bytes, iv: bytes, *, encrypt: bool) -> bytes:
    if len(key) != 16:
        raise DNAError(f"invalid AES key length: {len(key)}")
    if len(iv) != 16:
        raise DNAError(f"invalid AES IV length: {len(iv)}")
    if len(data) == 0 or len(data) % 16 != 0:
        raise DNAError(f"invalid AES block data length: {len(data)}")
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    ctx = cipher.encryptor() if encrypt else cipher.decryptor()
    return ctx.update(data) + ctx.finalize()


def _pkcs7_pad(data: bytes, block_size: int) -> bytes:
    padding = block_size - len(data) % block_size
    return data + bytes([padding]) * padding


def _pkcs7_unpad(data: bytes, block_size: int) -> bytes:
    if len(data) == 0 or len(data) % block_size:
        raise DNAError("invalid pkcs7 data length")
    padding = data[-1]
    if padding == 0 or padding > block_size or padding > len(data):
        raise DNAError("invalid pkcs7 padding")
    if data[-padding:] != bytes([padding]) * padding:
        raise DNAError("invalid pkcs7 padding bytes")
    return data[:-padding]


def _fixed_bytes(value: bytes, length: int) -> bytes:
    return bytes(value[:length]).ljust(length, b"\x00")
