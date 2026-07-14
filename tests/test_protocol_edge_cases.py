"""Edge-case tests for DNA and eMotion protocol helpers."""

from __future__ import annotations

import json
import struct
from datetime import UTC, datetime

import pytest

from aiolinknlink.protocol import dna, emotion

KEY = b"0123456789abcdef"
IV = b"fedcba9876543210"


def corrupt(data: bytes, index: int = -1) -> bytes:
    """Return a copy with one byte changed."""
    value = bytearray(data)
    value[index] ^= 0x01
    return bytes(value)


def test_network_packet_round_trip_and_validation() -> None:
    header = dna.NetworkHeader(
        other=b"other",
        serv=b"service",
        status=3,
        device_type=0xD7AC,
        message_type=dna.MESSAGE_TYPE_COMMAND,
        sequence=42,
        mac=bytes.fromhex("e04b410244c7"),
        device_id=b"dev1",
        payload_checksum=123,
    )
    packet = dna.build_packet(header, b"ciphertext")
    parsed, payload = dna.parse_packet(packet)

    assert parsed.status == 3
    assert parsed.device_type == 0xD7AC
    assert parsed.sequence == 42
    assert parsed.mac == header.mac
    assert payload == b"ciphertext"
    assert dna.verify_checksum_le(packet, dna.PACKET_CHECKSUM_OFFSET)
    assert not dna.verify_checksum_le(packet, -1)
    assert not dna.verify_checksum_le(packet, len(packet))

    with pytest.raises(dna.DNAError, match="too short"):
        dna.parse_network_header(b"short")
    with pytest.raises(dna.DNAError, match="magic"):
        dna.parse_network_header(b"x" * dna.HEADER_SIZE)
    with pytest.raises(dna.DNAError, match="checksum"):
        dna.parse_packet(corrupt(packet))


def test_blc_packet_validation() -> None:
    packet = dna.build_blc_packet(
        dna.NetworkHeader(
            device_type=0xD7AC,
            message_type=dna.MESSAGE_TYPE_COMMAND,
            sequence=7,
            mac=bytes.fromhex("e04b410244c7"),
        ),
        b"payload",
    )
    header, payload = dna.parse_blc_packet(packet)
    assert header.sequence == 7
    assert payload == b"payload"

    with pytest.raises(dna.DNAError, match="too short"):
        dna.parse_blc_packet(b"short")
    with pytest.raises(dna.DNAError, match="magic"):
        dna.parse_blc_packet(b"x" * dna.BLC_NETWORK_HEADER_SIZE)
    with pytest.raises(dna.DNAError, match="checksum"):
        dna.parse_blc_packet(corrupt(packet))


def test_discovery_packet_variants_and_validation() -> None:
    now = datetime(2026, 7, 15, 1, 2, 3, tzinfo=UTC)
    short = dna.build_short_discovery_packet("192.168.3.85", 25825, now)
    assert len(short) == dna.SHORT_DISCOVERY_SIZE
    assert dna.verify_checksum_le(short, dna.DISCOVERY_CHECKSUM_OFFSET)

    with pytest.raises(dna.DNAError, match="IPv4"):
        dna.build_discovery_packet("::1", 80, now)
    with pytest.raises(dna.DNAError, match="local port"):
        dna.build_discovery_packet("127.0.0.1", 65536, now)


def test_aes_payloads_and_padding_validation() -> None:
    aes = dna.build_aes_payload(b"payload")
    header, payload = dna.parse_aes_payload(aes)
    assert header.terminal_id == 1
    assert payload == b"payload"

    with pytest.raises(dna.DNAError, match="too short"):
        dna.parse_aes_payload(b"short")
    with pytest.raises(dna.DNAError, match="checksum"):
        dna.parse_aes_payload(corrupt(aes))

    encrypted = dna.encrypt_aes_cbc_pkcs7(b"payload", KEY, IV)
    assert dna.decrypt_aes_cbc_pkcs7(encrypted, KEY, IV) == b"payload"
    zero_encrypted = dna.encrypt_aes_cbc_zero_padding(b"", KEY, IV)
    assert dna.decrypt_aes_cbc_no_padding(zero_encrypted, KEY, IV) == b"\x00" * 16

    with pytest.raises(dna.DNAError, match="key length"):
        dna.encrypt_aes_cbc_pkcs7(b"payload", b"bad", IV)
    with pytest.raises(dna.DNAError, match="IV length"):
        dna.encrypt_aes_cbc_pkcs7(b"payload", KEY, b"bad")
    with pytest.raises(dna.DNAError, match="block data length"):
        dna.decrypt_aes_cbc_no_padding(b"bad", KEY, IV)
    with pytest.raises(dna.DNAError, match="data length"):
        dna.decrypt_aes_cbc_pkcs7(b"", KEY, IV)

    with pytest.raises(dna.DNAError, match="padding"):
        dna.decrypt_aes_cbc_pkcs7(dna.encrypt_aes_cbc_zero_padding(b"not-pkcs7", KEY, IV), KEY, IV)


def test_auth_payload_variants_and_mac_helpers() -> None:
    mac = bytes.fromhex("e04b410244c7")
    payload = dna.build_auth_payload(
        mac,
        0xD7AC,
        host="192.168.3.159",
        gateway_server="http://gateway",
        heartbeat_server="tcp://heartbeat",
    )
    server_info = json.loads(payload[dna.AUTH_PAIR_INFO_SIZE :].rstrip(b"\x00"))
    assert server_info == {"http": "http://gateway", "tcp": "tcp://heartbeat"}
    assert dna.format_mac(mac) == "e0:4b:41:02:44:c7"
    assert dna.format_mac(b"bad") == ""
    assert dna.format_mac(b"\x00" * 6) == ""
    assert dna.mac_bytes("E0-4B-41-02-44-C7") == mac
    assert dna.mac_bytes("bad") == b""
    assert dna.mac_bytes("zz:zz:zz:zz:zz:zz") == b""
    assert dna.calculate_authcode(b"bad", 0xD7AC) == b"\x00" * 16

    with pytest.raises(dna.DNAError, match="mac length"):
        dna.build_auth_payload(b"bad", 0xD7AC)
    with pytest.raises(ValueError):
        dna.build_auth_payload(mac, 0xD7AC, host="not-an-ip")
    with pytest.raises(dna.DNAError, match="session key"):
        dna.extract_session_key(b"short")


def test_discovery_response_variants() -> None:
    full = bytearray(0x48)
    full[:8] = dna.MAGIC
    struct.pack_into("<H", full, 0x26, dna.MESSAGE_TYPE_DISCOVERY_RESPONSE)
    struct.pack_into(">H", full, 0x34, 0xD7AC)
    full[0x3A:0x40] = bytes(reversed(bytes.fromhex("e04b410244c7")))
    full[0x40:] = b"Ultra2\x00"
    device = dna.parse_discovery_device_response(bytes(full), "192.168.3.159", 0)
    assert device.id == "e0:4b:41:02:44:c7"
    assert device.port == dna.DEFAULT_PORT
    assert device.device_type == 0xD7AC
    assert device.name == "Ultra2"

    header = dna.NetworkHeader(
        device_type=0xD7AC,
        message_type=dna.MESSAGE_TYPE_DISCOVERY_RESPONSE,
        mac=bytes.fromhex("e04b410244c7"),
    )
    packet = bytearray(header.marshal())
    packet.extend(b"Name!\x00")
    parsed = dna.parse_discovery_device_response(bytes(packet), "127.0.0.1", 80)
    assert parsed.mac == "e0:4b:41:02:44:c7"
    assert parsed.name == "Name!"

    with pytest.raises(dna.DNAError, match="too short"):
        dna.parse_discovery_device_response(b"short", "127.0.0.1", 80)
    with pytest.raises(dna.DNAError, match="magic"):
        dna.parse_discovery_device_response(b"x" * dna.SHORT_DISCOVERY_SIZE, "127.0.0.1", 80)


def test_response_summary_and_short_response() -> None:
    short = dna.NetworkHeader(
        status=2,
        device_type=0xD7AC,
        message_type=dna.MESSAGE_TYPE_COMMAND,
    ).marshal()
    summary = dna.response_summary(short)
    assert "status=0x0002" in summary
    with pytest.raises(dna.ShortResponseError) as err:
        dna._raise_short_response(short)
    assert err.value.status == 2
    assert err.value.device_type == 0xD7AC


async def test_full_header_encrypted_exchange() -> None:
    async def exchange(
        target_ip: str,
        target_port: int,
        packet: bytes,
        timeout: float,
        accept: dna.PacketAcceptor | None,
    ) -> bytes:
        assert (target_ip, target_port, timeout) == ("127.0.0.1", 80, 1)
        request_header, _ = dna.parse_packet(packet)
        response_body = dna.encrypt_aes_cbc_pkcs7(dna.build_aes_payload(b"response"), dna.INITIAL_KEY, dna.INITIAL_IV)
        response = dna.build_packet(request_header, response_body)
        assert accept is None or accept(response)
        return response

    response = await dna.send_encrypted(
        "127.0.0.1",
        80,
        dna.NetworkHeader(message_type=dna.MESSAGE_TYPE_AUTH),
        b"request",
        dna.INITIAL_KEY,
        timeout=1,
        exchange=exchange,
    )
    assert response == b"response"


async def test_full_header_fallback_and_errors() -> None:
    fallback_plain = b"0123456789abcdef0123456789abcdef"

    async def fallback_exchange(*_args: object) -> bytes:
        body = dna.encrypt_aes_cbc_zero_padding(fallback_plain, dna.INITIAL_KEY, dna.INITIAL_IV)
        return dna.build_packet(dna.NetworkHeader(), body)

    response = await dna.send_encrypted(
        "127.0.0.1",
        80,
        dna.NetworkHeader(),
        b"request",
        dna.INITIAL_KEY,
        exchange=fallback_exchange,
    )
    assert response == fallback_plain

    async def bad_exchange(*_args: object) -> bytes:
        body = dna.encrypt_aes_cbc_zero_padding(b"bad", dna.INITIAL_KEY, dna.INITIAL_IV)
        return dna.build_packet(dna.NetworkHeader(), body)

    with pytest.raises(dna.DNAError, match="response_summary"):
        await dna.send_encrypted(
            "127.0.0.1",
            80,
            dna.NetworkHeader(),
            b"request",
            dna.INITIAL_KEY,
            exchange=bad_exchange,
        )


async def test_send_packet_validation_and_custom_exchange() -> None:
    with pytest.raises(dna.DNAError, match="greater than zero"):
        await dna._send_packet("127.0.0.1", 80, b"packet", 0, None, None)

    exchange_calls = 0

    async def exchange(*_args: object) -> bytes:
        nonlocal exchange_calls
        exchange_calls += 1
        return b"response"

    assert await dna._send_packet("127.0.0.1", 80, b"packet", 1, None, exchange) == b"response"
    assert exchange_calls == 1


def test_emotion_command_builders() -> None:
    assert emotion.build_gateway_get_state_command()[:4] == struct.pack("<I", 0x26)
    assert emotion.build_gateway_get_state_command(b"params") == struct.pack("<I", 0x24) + b"params"
    upload = emotion.build_local_udp_upload_command(25825, 60, ip=123)
    assert json.loads(upload[4:]) == {"port": 25825, "timeout": 60, "ip": 123}

    for frame in (
        emotion.build_scan_subdevices_frame("pid"),
        emotion.build_get_waiting_list_frame(),
        emotion.build_get_subdevice_list_frame(),
        emotion.build_set_status_frame("did", {"value": 1}),
    ):
        assert emotion.verify_subdevice_checksum(frame)


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        (b"bad", "too short"),
        (struct.pack("<I", 1) + b"{}", "unexpected"),
        (struct.pack("<I", 20000) + b"bad", "invalid"),
        (struct.pack("<I", 20000) + b"[]", "not an object"),
        (struct.pack("<I", 20000) + b'{"ip":1,"port":true,"timeout":1}', "invalid port"),
        (struct.pack("<I", 20000) + b'{"ip":1,"port":"bad","timeout":1}', "invalid port"),
        (struct.pack("<I", 20000) + b'{"ip":1,"port":0,"timeout":1}', "outside valid range"),
    ],
)
def test_local_udp_upload_response_errors(payload: bytes, match: str) -> None:
    with pytest.raises(emotion.EmotionError, match=match):
        emotion.parse_local_udp_upload_response(payload)


def test_subdevice_frame_payload_types_and_errors() -> None:
    assert emotion.parse_subdevice_frame(emotion.build_subdevice_frame(1, b"raw")).payload == b"raw"
    assert emotion.parse_subdevice_frame(emotion.build_subdevice_frame(1, "text")).payload == b"text"
    assert (
        emotion.parse_subdevice_json_payload(emotion.parse_subdevice_frame(emotion.build_subdevice_frame(1, None)))
        == {}
    )

    valid = emotion.build_subdevice_frame(1, {"ok": True})
    with pytest.raises(emotion.EmotionError, match="too short"):
        emotion.parse_subdevice_frame(b"short")
    with pytest.raises(emotion.EmotionError, match="header"):
        emotion.parse_subdevice_frame(b"x" * 12)
    with pytest.raises(emotion.EmotionError, match="checksum"):
        emotion.parse_subdevice_frame(corrupt(valid))
    too_long = bytearray(valid)
    struct.pack_into("<H", too_long, 8, 0xFFFF)
    emotion.write_subdevice_checksum(too_long)
    with pytest.raises(emotion.EmotionError, match="exceeds"):
        emotion.parse_subdevice_frame(bytes(too_long))


def test_gateway_response_variants_and_errors() -> None:
    subdevice = emotion.build_subdevice_frame(1, {"ok": True})
    parsed = emotion.parse_gateway_state_response(struct.pack("<I", 0x26) + subdevice)
    assert parsed.subdevice_frame is not None

    binary = emotion.parse_gateway_state_response(struct.pack("<I", 0x24) + b"\x01\x02\x03\x04")
    assert binary.gateway_state is not None
    assert binary.gateway_state.attributes["tempsensor"] == 102
    assert binary.gateway_state.attributes["humsensor"] == 304
    assert emotion.parse_gateway_binary_state(b"\x01").attributes == {"raw_hex_len": 1}

    with pytest.raises(emotion.EmotionError, match="too short"):
        emotion.parse_gateway_state_response(b"bad")
    with pytest.raises(emotion.EmotionError, match="unsupported"):
        emotion.parse_gateway_state_response(struct.pack("<I", 999))


@pytest.mark.parametrize(
    "payload",
    [b"", b"not-json", b"[]"],
)
def test_gateway_json_state_errors(payload: bytes) -> None:
    with pytest.raises(emotion.EmotionError):
        emotion.parse_gateway_json_state(payload)


@pytest.mark.parametrize(
    ("payload", "online"),
    [
        ({"offline": 1}, False),
        ({"tempsensor": 0, "humsensor": 0}, False),
        ({"tempsensor": 1, "humsensor": 0}, True),
        ({"online": "online", "dev_ip": "192.168.1.2", "wifi_rssi": -50}, True),
        ({"online": None}, True),
    ],
)
def test_gateway_json_state_semantics(payload: dict[str, object], online: bool) -> None:
    state = emotion.parse_gateway_json_state(json.dumps(payload).encode() + b"garbage")
    assert state.online is online
    if "dev_ip" in payload:
        assert state.attributes["ip"] == payload["dev_ip"]


def test_subdevice_json_payload_errors() -> None:
    assert emotion.parse_subdevice_json_payload(emotion.SubdeviceFrame(1, 0, b"")) == {}
    for payload in (b"bad", b"[]"):
        with pytest.raises(emotion.EmotionError):
            emotion.parse_subdevice_json_payload(emotion.SubdeviceFrame(1, 0, payload))


def test_position_payload_repair_and_empty_positions() -> None:
    unescaped = b'{"detect_position":"[{"x":1,"y":2,"z":3}]"}'
    parsed = emotion.parse_local_udp_position_payload(unescaped)
    assert parsed["target_distance"] == 374

    values: dict[str, object] = {}
    emotion.derive_distances_from_position(values, "[]")
    assert values == {}
    empty = emotion.parse_local_udp_position_update(b'{"detect_position":"[]"}', "127.0.0.1")
    assert empty.nearest_horizontal_distance is None
    assert empty.nearest_distance is None

    for payload in (b"bad", b"{}", b"[]"):
        with pytest.raises((json.JSONDecodeError, emotion.EmotionError)):
            emotion.parse_local_udp_position_payload(payload)
