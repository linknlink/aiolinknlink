"""Tests for the iBG local LAN client."""

from __future__ import annotations

import json
import struct

import pytest

from aiolinknlink import (
    PID_8_CHANNEL_LIGHT_SWITCH,
    PID_DLT645_ELECTRICITY_METER,
    PID_DTU,
    PID_EAC1_PANEL,
    PID_ESENSOR_2000,
    PID_ESENSOR_2000_GEN1,
    PID_ESENSOR_2000_GEN2,
    PID_MODBUS_AC,
    PID_MODBUS_ELECTRICITY_METER,
    PID_MODBUS_MULTI_SENSOR,
    PID_MODBUS_WATER_METER,
    PID_SINGLE_CHANNEL_LIGHT_SWITCH,
    PID_WATER_AC_PANEL,
    IbgClient,
    IbgConnectionError,
    IbgDevice,
    IbgProtocolError,
    IbgSession,
    IbgSubDevice,
)
from aiolinknlink.ibg import PID_BOX7_CONTROLLER, PID_SR3_SENSOR, normalize_subdevice_state
from aiolinknlink.protocol import dna, gateway

GATEWAY = IbgDevice(
    id="001122334455",
    ip="192.168.1.10",
    port=80,
    mac="00:11:22:33:44:55",
    type_id=0x2B71,
)
SESSION_KEY = b"0123456789abcdef"
SENSOR_DID = "00112233445566778899aabbccddeeff"
BOX7_DID = "00112233445566778899aabbccddeef0"
DTU_DID = "00112233445566778899aabbccddeef1"
MODBUS_AC_DID = "00112233445566778899aabbccddeef2"
WATER_AC_PANEL_DID = "00112233445566778899aabbccddeef3"
EAC1_DID = "00112233445566778899aabbccddeef4"
LIGHT8_DID = "00112233445566778899aabbccddeef6"
SINGLE_CHANNEL_LIGHT_DID = "00112233445566778899aabbccddeef7"


def _response_packet(request: bytes, key: bytes, response: bytes) -> bytes:
    header, _body = dna.parse_blc_packet(request)
    return dna.build_blc_packet(header, dna.build_blc_encrypted_payload(response, key))


def _entry(index: int, *, pid: str = PID_SR3_SENSOR, offline: int = 0) -> dict[str, object]:
    return {
        "did": f"{index:032x}",
        "pid": pid,
        "name": f"Sensor {index}",
        "offline": offline,
    }


async def test_connect_uses_compact_auth_and_extracts_session_key() -> None:
    seen_request = False

    async def exchange(
        _ip: str,
        _port: int,
        packet: bytes,
        _timeout: float,
        _accept: dna.PacketAcceptor | None,
    ) -> bytes:
        nonlocal seen_request
        seen_request = True
        header, body = dna.parse_blc_packet(packet)
        assert header.message_type == dna.MESSAGE_TYPE_AUTH
        assert header.device_type == GATEWAY.type_id
        _aes, auth = dna.parse_blc_encrypted_payload(body, dna.INITIAL_KEY)
        assert auth[4:28] == dna.mac_bytes(GATEWAY.mac) * 4
        return _response_packet(packet, dna.INITIAL_KEY, struct.pack("<I", 1) + SESSION_KEY)

    session = await IbgClient().connect(GATEWAY, exchange=exchange)

    assert seen_request
    assert session.session_key == SESSION_KEY
    assert session.last_auth_at is not None
    assert SESSION_KEY.hex() not in repr(session)


async def test_connect_accepts_previously_paired_local_key_without_auth_exchange() -> None:
    async def exchange(*_args: object) -> bytes:
        raise AssertionError("locked gateway must not be paired again")

    session = await IbgClient().connect(GATEWAY, local_key=SESSION_KEY, exchange=exchange)

    assert session.session_key == SESSION_KEY
    assert session.last_auth_at is not None

    with pytest.raises(IbgConnectionError, match="16 bytes"):
        await IbgClient().connect(GATEWAY, local_key=b"short")


async def test_paginated_subdevice_list_and_safe_state() -> None:
    pages = [_entry(index) for index in range(21)]
    status_payload = {
        "status": 0,
        "did": SENSOR_DID,
        "pid": PID_SR3_SENSOR,
        "envtemp": 257,
        "envhumid": 524,
        "envlux": 126,
        "battery": 100,
        "pir_detected": 1,
        "keypressed": 2,
        "password": "must-not-be-exposed",
        "network_key": "must-not-be-exposed",
    }

    async def exchange(
        _ip: str,
        _port: int,
        packet: bytes,
        _timeout: float,
        _accept: dna.PacketAcceptor | None,
    ) -> bytes:
        _header, body = dna.parse_blc_packet(packet)
        _aes, plain = dna.parse_blc_encrypted_payload(body, SESSION_KEY)
        frame = gateway.parse_gateway_frame(plain)
        if frame.command_type == gateway.CMD_GATEWAY_LIST:
            index = int(frame.payload["index"])
            response = gateway.build_gateway_frame(
                gateway.CMD_GATEWAY_LIST_RESPONSE,
                {"status": 0, "index": index, "total": len(pages), "list": pages[index : index + 10]},
            )
        else:
            assert frame.command_type == gateway.CMD_GET_STATUS
            response = gateway.build_gateway_frame(gateway.CMD_STATUS_RESPONSE, status_payload)
        return _response_packet(packet, SESSION_KEY, response)

    client = IbgClient()
    session = IbgSession(device=GATEWAY, session_key=SESSION_KEY, command_sequence=10)
    devices = await client.list_subdevices(session, exchange=exchange)
    assert len(devices) == 21

    requested = IbgSubDevice(SENSOR_DID, PID_SR3_SENSOR, "Room sensor", True)
    state = await client.get_subdevice_state(session, requested, exchange=exchange)
    assert state.values == {
        "temperature": 25.7,
        "humidity": 52.4,
        "illuminance": 126,
        "battery": 100,
        "occupancy": True,
        "keypressed": 2,
    }
    assert "password" not in state.values
    assert "network_key" not in state.values


def test_state_normalization_rejects_unreviewed_and_invalid_values() -> None:
    assert normalize_subdevice_state("0" * 32, {"envtemp": 230}) == {}
    values = normalize_subdevice_state(
        PID_SR3_SENSOR,
        {
            "envtemp": True,
            "envhumid": 1001,
            "envlux": -1,
            "battery": 101,
            "pir_detected": 3,
            "keypressed": True,
            "mqtt_password": "secret",
        },
    )
    assert values == {}


def test_state_normalization_accepts_confirmed_key_values_only() -> None:
    assert normalize_subdevice_state(PID_SR3_SENSOR, {"keypressed": 1}) == {"keypressed": 1}
    assert normalize_subdevice_state(PID_SR3_SENSOR, {"keypressed": 2}) == {"keypressed": 2}
    assert normalize_subdevice_state(PID_SR3_SENSOR, {"keypressed": 0}) == {}


def test_esensor_2000_state_normalization_uses_documented_scaling_and_enums() -> None:
    values = normalize_subdevice_state(
        PID_ESENSOR_2000_GEN2,
        {
            "envtemp": 237,
            "envhumid": 4960,
            "battery": 100,
            "pir_detected": 1,
            "keypressed": 3,
            "envlux": 1,
            "password": "must-not-be-exposed",
        },
    )

    assert values == {
        "temperature": 23.7,
        "humidity": 49.6,
        "battery": 100,
        "occupancy": True,
        "keypressed": 3,
        "illuminance": 1,
    }


def test_esensor_2000_state_normalization_handles_unknown_and_invalid_values() -> None:
    assert PID_ESENSOR_2000 == PID_ESENSOR_2000_GEN2
    assert normalize_subdevice_state(PID_ESENSOR_2000_GEN2, {"pir_detected": 3, "keypressed": 0}) == {"keypressed": 0}
    assert (
        normalize_subdevice_state(
            PID_ESENSOR_2000_GEN2,
            {
                "envtemp": 12_501,
                "envhumid": 10_001,
                "battery": 101,
                "pir_detected": 2,
                "keypressed": 2,
                "envlux": 65_536,
            },
        )
        == {}
    )


def test_esensor_2000_gen1_state_normalization_uses_documented_scaling_and_enums() -> None:
    values = normalize_subdevice_state(
        PID_ESENSOR_2000_GEN1,
        {
            "envtemp": 237,
            "envhumid": 496,
            "battery": 85,
            "pir_detected": 1,
            "keypressed": 3,
            "envlux": 321,
            "password": "must-not-be-exposed",
        },
    )

    assert values == {
        "temperature": 23.7,
        "humidity": 49.6,
        "battery": 85,
        "occupancy": True,
        "keypressed": 3,
    }


def test_esensor_2000_gen1_state_normalization_rejects_undocumented_and_invalid_values() -> None:
    assert normalize_subdevice_state(PID_ESENSOR_2000_GEN1, {"pir_detected": False, "keypressed": 0}) == {
        "occupancy": False,
        "keypressed": 0,
    }
    assert (
        normalize_subdevice_state(
            PID_ESENSOR_2000_GEN1,
            {
                "envtemp": 1_001,
                "envhumid": 1_001,
                "battery": 101,
                "pir_detected": 2,
                "keypressed": 4,
                "envlux": 123,
                "unreviewed": 1,
            },
        )
        == {}
    )


def test_box7_state_normalization_uses_reviewed_fields_and_scales() -> None:
    values = normalize_subdevice_state(
        PID_BOX7_CONTROLLER,
        {
            "pwr1": 1,
            "pwr2": 0,
            "pwr3": True,
            "pwr4": 2,
            "power": 12345,
            "totalconsum": 98765,
            "envtemp1": -5,
            "envtemp2": 128,
            "envtemp3": 129,
            "Aphasevolt": 2315,
            "Bphasevolt": True,
            "Cphasevolt": 2200,
            "Aphasecurrent": 1234,
            "Bphasecurrent": 999999,
            "Cphasecurrent": -1,
            "alarm_state": 1,
            "tempdif1": 8,
            "password": "must-not-be-exposed",
        },
    )

    assert values == {
        "pwr1": True,
        "pwr2": False,
        "pwr3": True,
        "power": 1234.5,
        "totalconsum": 987.65,
        "envtemp1": -5.0,
        "envtemp2": 128.0,
        "Aphasevolt": 231.5,
        "Cphasevolt": 220.0,
        "Aphasecurrent": 1.234,
        "Bphasecurrent": 999.999,
    }


def test_8_channel_light_switch_normalizes_circuits_and_master_state() -> None:
    values = normalize_subdevice_state(
        PID_8_CHANNEL_LIGHT_SWITCH,
        {
            "pwr1": 1,
            "pwr2": 1,
            "pwr3": True,
            "pwr4": 1,
            "pwr5": 1,
            "pwr6": 1,
            "pwr7": 1,
            "mpwr": 2,
            "password": "must-not-be-exposed",
        },
    )

    assert values == {
        "pwr1": True,
        "pwr2": True,
        "pwr3": True,
        "pwr4": True,
        "pwr5": True,
        "pwr6": True,
        "pwr7": True,
        "mpwr": True,
    }

    mixed = normalize_subdevice_state(
        PID_8_CHANNEL_LIGHT_SWITCH,
        {
            "pwr1": 1,
            "pwr2": 0,
            "pwr3": 1,
            "pwr4": 1,
            "pwr5": 1,
            "pwr6": 1,
            "pwr7": 1,
            "mpwr": 2,
        },
    )
    assert mixed["mpwr"] is False


def test_8_channel_light_switch_master_state_handles_partial_and_invalid_reports() -> None:
    stale_after_all_on = {f"pwr{channel}": 0 for channel in range(1, 8)}
    stale_after_all_on["mpwr"] = 1
    assert normalize_subdevice_state(PID_8_CHANNEL_LIGHT_SWITCH, stale_after_all_on)["mpwr"] is True

    stale_after_all_off = {f"pwr{channel}": 1 for channel in range(1, 8)}
    stale_after_all_off["mpwr"] = 0
    assert normalize_subdevice_state(PID_8_CHANNEL_LIGHT_SWITCH, stale_after_all_off)["mpwr"] is False

    assert normalize_subdevice_state(PID_8_CHANNEL_LIGHT_SWITCH, {"mpwr": 2}) == {}
    assert (
        normalize_subdevice_state(
            PID_8_CHANNEL_LIGHT_SWITCH,
            {"pwr1": 2, "pwr2": -1, "mpwr": 3},
        )
        == {}
    )


def test_single_channel_light_switch_normalizes_backlight_and_scene_keys() -> None:
    values = normalize_subdevice_state(
        PID_SINGLE_CHANNEL_LIGHT_SWITCH,
        {
            "pwr1": 1,
            "bglight": 0,
            "scenarioswitch_1": 1,
            "scenarioswitch_2": 0,
            "password": "must-not-be-exposed",
        },
    )

    assert values == {
        "pwr1": True,
        "bglight": False,
        "scenarioswitch_1": 1,
        "scenarioswitch_2": 0,
    }


def test_dtu_state_normalization_uses_modes_and_documented_scaling() -> None:
    values = normalize_subdevice_state(
        PID_DTU,
        {
            "pwr1": 1,
            "pwr2": False,
            "d1": 1234,
            "d2": 750,
            "d3": 100,
            "date1_type": 0,
            "date2_type": 1,
            "date3_type": 2,
            "voltage": 80,
            "power": 12345,
            "totalconsum": 98765,
            "Aphasevolt": 230,
            "Bphasevolt": 231,
            "Cphasevolt": 232,
            "Aphasecurrent": 123,
            "Bphasecurrent": 456,
            "Cphasecurrent": 65535,
            "signalinput1": 1,
            "signalinput2": 0,
            "signalinput3": True,
            "wifi_switch": 1,
            "password": "must-not-be-exposed",
        },
    )

    assert values == {
        "pwr1": True,
        "pwr2": False,
        "date1_type": 0,
        "d1": 12.34,
        "date2_type": 1,
        "d2": 7.5,
        "signalinput1": True,
        "signalinput2": False,
        "signalinput3": True,
        "voltage": 8.0,
        "power": 1234.5,
        "totalconsum": 987.65,
        "Aphasevolt": 230.0,
        "Bphasevolt": 231.0,
        "Cphasevolt": 232.0,
        "Aphasecurrent": 1.23,
        "Bphasecurrent": 4.56,
        "Cphasecurrent": 655.35,
    }


def test_modbus_ac_state_normalization_uses_documented_fields_and_scaling() -> None:
    values = normalize_subdevice_state(
        PID_MODBUS_AC,
        {
            "pwr": 1,
            "mark": 3,
            "ac_mode": 0,
            "temp": 20,
            "envtemp": 230,
            "errcode": 1,
            "address": 3,
            "modbusreadresult": 0,
            "password": "must-not-be-exposed",
        },
    )

    assert values == {
        "pwr": True,
        "mark": 3,
        "ac_mode": 0,
        "temp": 20,
        "envtemp": 23.0,
        "errcode": 1,
    }


def test_modbus_ac_state_normalization_rejects_invalid_values() -> None:
    assert (
        normalize_subdevice_state(
            PID_MODBUS_AC,
            {
                "pwr": 2,
                "mark": 4,
                "ac_mode": True,
                "temp": 15,
                "envtemp": 651,
                "errcode": -1,
            },
        )
        == {}
    )


def test_modbus_multi_sensor_state_normalization_uses_documented_scaling() -> None:
    values = normalize_subdevice_state(
        PID_MODBUS_MULTI_SENSOR,
        {
            "envtemp": 200,
            "envco2": 1000,
            "envhumid": 400,
            "envlux": 60,
            "address": 2,
            "modbusreadresult": 0,
            "modbuswriteresult": 0,
            "password": "must-not-be-exposed",
        },
    )

    assert values == {
        "temperature": 2.0,
        "carbon_dioxide": 1000.0,
        "humidity": 4.0,
        "illuminance": 60.0,
    }


def test_modbus_multi_sensor_state_normalization_rejects_invalid_values() -> None:
    assert (
        normalize_subdevice_state(
            PID_MODBUS_MULTI_SENSOR,
            {
                "envtemp": -4_001,
                "envco2": True,
                "envhumid": 10_001,
                "envlux": -1,
            },
        )
        == {}
    )


def test_modbus_water_meter_state_normalization_uses_documented_scaling() -> None:
    values = normalize_subdevice_state(
        PID_MODBUS_WATER_METER,
        {
            "fm_positiflow": 1000,
            "fm_instanflow": 3600,
            "address": 1,
            "modbusreadresult": 0,
            "password": "must-not-be-exposed",
        },
    )

    assert values == {"total_water": 10.0, "water_flow_rate": 3600.0}


def test_modbus_water_meter_state_handles_missing_and_invalid_fields() -> None:
    assert normalize_subdevice_state(PID_MODBUS_WATER_METER, {"fm_positiflow": 1000}) == {"total_water": 10.0}
    assert (
        normalize_subdevice_state(
            PID_MODBUS_WATER_METER,
            {
                "fm_positiflow": 10_000_000,
                "fm_instanflow": 4_294_967_296,
            },
        )
        == {}
    )


def test_modbus_electricity_meter_state_normalization_uses_all_documented_fields() -> None:
    values = normalize_subdevice_state(
        PID_MODBUS_ELECTRICITY_METER,
        {
            "Combenergy": 123_456,
            "totalconsum": 883_048,
            "Reverenergy": 123,
            "Aphasevolt": 2_347,
            "Bphasevolt": 2_357,
            "Cphasevolt": 2_352,
            "Aphasecurrent": 9_283,
            "Bphasecurrent": -6_187,
            "Cphasecurrent": 11_901,
            "power": 50_524,
            "Aphasepower": 19_647,
            "Bphasepower": 9_995,
            "Cphasepower": 21_177,
            "Combpowerfactor": 812,
            "Apowerfactor": 901,
            "Bpowerfactor": 685,
            "Cpowerfactor": 756,
            "Aphasehmccurrent": 321,
            "Bphasehmccurrent": 654,
            "Cphasehmccurrent": 987,
            "elec_param": "three-phase",
            "address": 21,
            "Aphaseoverload": 0,
            "Bphaseoverload": 1,
            "Cphaseoverload": False,
            "transformerratio": 100,
            "modbusreadresult": 0,
            "modbuswriteresult": 2,
            "devicename": "第一路三相电表",
            "frequency": 5_001,
            "AB_linevoltage": 4_071,
            "BC_linevoltage": 4_080,
            "AC_linevoltage": 4_075,
            "Aphase_reactivepower": -1_250,
            "Bphase_reactivepower": 2_500,
            "Cphase_reactivepower": 3_750,
            "Total_reactivepower": 5_000,
            "password": "must-not-be-exposed",
        },
    )

    assert values == {
        "Combenergy": 1234.56,
        "totalconsum": 8830.48,
        "Reverenergy": 1.23,
        "Aphasevolt": 234.7,
        "Bphasevolt": 235.7,
        "Cphasevolt": 235.2,
        "Aphasecurrent": 9.283,
        "Bphasecurrent": -6.187,
        "Cphasecurrent": 11.901,
        "power": 5052.4,
        "Aphasepower": 1964.7,
        "Bphasepower": 999.5,
        "Cphasepower": 2117.7,
        "Combpowerfactor": 0.812,
        "Apowerfactor": 0.901,
        "Bpowerfactor": 0.685,
        "Cpowerfactor": 0.756,
        "Aphasehmccurrent": 0.321,
        "Bphasehmccurrent": 0.654,
        "Cphasehmccurrent": 0.987,
        "frequency": 50.01,
        "AB_linevoltage": 407.1,
        "BC_linevoltage": 408.0,
        "AC_linevoltage": 407.5,
        "Aphase_reactivepower": -125.0,
        "Bphase_reactivepower": 250.0,
        "Cphase_reactivepower": 375.0,
        "Total_reactivepower": 500.0,
        "Aphaseoverload": False,
        "Bphaseoverload": True,
        "Cphaseoverload": False,
        "address": 21,
        "transformerratio": 100,
        "modbusreadresult": "success",
        "modbuswriteresult": "partial_success",
        "devicename": "第一路三相电表",
        "elec_param": "three-phase",
    }


def test_modbus_electricity_meter_state_rejects_invalid_values() -> None:
    assert (
        normalize_subdevice_state(
            PID_MODBUS_ELECTRICITY_METER,
            {
                "Combenergy": -1,
                "totalconsum": 4_294_967_296,
                "Aphasevolt": 65_536,
                "Aphasecurrent": 1_000_000,
                "power": 2_147_483_648,
                "Combpowerfactor": 10_000,
                "Aphasehmccurrent": -1,
                "frequency": 10_001,
                "Aphaseoverload": 2,
                "address": 256,
                "transformerratio": 301,
                "modbusreadresult": 3,
                "modbuswriteresult": True,
                "devicename": " ",
                "elec_param": "x" * 1_025,
            },
        )
        == {}
    )


def test_dlt645_electricity_meter_state_normalization_uses_documented_fields() -> None:
    values = normalize_subdevice_state(
        PID_DLT645_ELECTRICITY_METER,
        {
            "Combenergy": 123_456,
            "totalconsum": 883_048,
            "Reverenergy": 123,
            "Aphasevolt": 2_347,
            "Bphasevolt": 2_357,
            "Cphasevolt": 2_352,
            "Aphasecurrent": 9_283,
            "Bphasecurrent": -6_187,
            "Cphasecurrent": 11_901,
            "power": 50_524,
            "Aphasepower": 19_647,
            "Bphasepower": 9_995,
            "Cphasepower": 21_177,
            "Combpowerfactor": 812,
            "Apowerfactor": 901,
            "Bpowerfactor": 685,
            "Cpowerfactor": 756,
            "Aphasehmccurrent": 321,
            "Bphasehmccurrent": 654,
            "Cphasehmccurrent": 987,
            "elec_param": "645-address-configuration",
            "address": 21,
            "Aphaseoverload": 0,
            "Bphaseoverload": 1,
            "Cphaseoverload": False,
            "pwr": 1,
            "frequency": 5_001,
            "modbusreadresult": 0,
            "password": "must-not-be-exposed",
        },
    )

    assert values == {
        "Combenergy": 1234.56,
        "totalconsum": 8830.48,
        "Reverenergy": 1.23,
        "Aphasevolt": 234.7,
        "Bphasevolt": 235.7,
        "Cphasevolt": 235.2,
        "Aphasecurrent": 9.283,
        "Bphasecurrent": -6.187,
        "Cphasecurrent": 11.901,
        "power": 5052.4,
        "Aphasepower": 1964.7,
        "Bphasepower": 999.5,
        "Cphasepower": 2117.7,
        "Combpowerfactor": 0.812,
        "Apowerfactor": 0.901,
        "Bpowerfactor": 0.685,
        "Cpowerfactor": 0.756,
        "Aphasehmccurrent": 0.321,
        "Bphasehmccurrent": 0.654,
        "Cphasehmccurrent": 0.987,
        "Aphaseoverload": False,
        "Bphaseoverload": True,
        "Cphaseoverload": False,
        "address": 21,
        "elec_param": "645-address-configuration",
    }


def test_dlt645_electricity_meter_state_rejects_invalid_and_unreviewed_values() -> None:
    assert (
        normalize_subdevice_state(
            PID_DLT645_ELECTRICITY_METER,
            {
                "Combenergy": -1,
                "totalconsum": 4_294_967_296,
                "Aphasevolt": 65_536,
                "Aphasecurrent": 1_000_000,
                "power": 2_147_483_648,
                "Combpowerfactor": 10_000,
                "Aphasehmccurrent": -1,
                "Aphaseoverload": 2,
                "address": 256,
                "elec_param": "x" * 1_025,
                "pwr": 1,
                "frequency": 5_000,
            },
        )
        == {}
    )


def test_water_ac_panel_state_normalization_uses_documented_fields() -> None:
    values = normalize_subdevice_state(
        PID_WATER_AC_PANEL,
        {
            "pwr": 1,
            "ac_mark": 0,
            "ac_mode": 3,
            "temp": 25,
            "insidetemp": 29,
            "keylocked": 1,
            "acpanel_devtype": 0,
            "password": "must-not-be-exposed",
        },
    )

    assert values == {
        "pwr": True,
        "ac_mark": 0,
        "ac_mode": 3,
        "temp": 25,
        "insidetemp": 29,
    }


def test_water_ac_panel_state_normalization_rejects_invalid_values() -> None:
    assert (
        normalize_subdevice_state(
            PID_WATER_AC_PANEL,
            {
                "pwr": 2,
                "ac_mark": 4,
                "ac_mode": 2,
                "temp": 36,
                "insidetemp": 101,
            },
        )
        == {}
    )


def test_eac1_state_normalization_uses_all_documented_fields() -> None:
    values = normalize_subdevice_state(
        PID_EAC1_PANEL,
        {
            "pwr": 1,
            "ac_mark": 2,
            "ac_mode": 3,
            "temp": 24,
            "insidetemp": 23,
            "insidehumid": 58,
            "keylocked": 1,
            "acpanel_devtype": 0,
            "password": "must-not-be-exposed",
        },
    )

    assert values == {
        "pwr": True,
        "ac_mark": 2,
        "ac_mode": 3,
        "temp": 24,
        "insidetemp": 23,
        "insidehumid": 58,
        "keylocked": True,
        "acpanel_devtype": "water_cooled",
    }


def test_eac1_state_normalization_rejects_invalid_values() -> None:
    assert (
        normalize_subdevice_state(
            PID_EAC1_PANEL,
            {
                "pwr": 2,
                "ac_mark": 4,
                "ac_mode": 2,
                "temp": 31,
                "insidetemp": 101,
                "insidehumid": 101,
                "keylocked": -1,
                "acpanel_devtype": 2,
            },
        )
        == {}
    )


async def test_box7_set_state_sends_only_reviewed_boolean_control() -> None:
    device = IbgSubDevice(BOX7_DID, PID_BOX7_CONTROLLER, "BOX7", True)

    async def exchange(
        _ip: str,
        _port: int,
        packet: bytes,
        _timeout: float,
        _accept: dna.PacketAcceptor | None,
    ) -> bytes:
        _header, body = dna.parse_blc_packet(packet)
        _aes, plain = dna.parse_blc_encrypted_payload(body, SESSION_KEY)
        frame = gateway.parse_gateway_frame(plain)
        assert frame.command_type == gateway.CMD_SET_STATUS
        assert frame.payload == {"did": BOX7_DID, "pwr3": 1}
        return _response_packet(
            packet,
            SESSION_KEY,
            gateway.build_gateway_frame(
                gateway.CMD_STATUS_RESPONSE,
                {
                    "did": BOX7_DID,
                    "pid": PID_BOX7_CONTROLLER,
                    "pwr1": 0,
                    "pwr3": 1,
                    "power": 120,
                },
            ),
        )

    state = await IbgClient().set_subdevice_state(
        IbgSession(device=GATEWAY, session_key=SESSION_KEY),
        device,
        {"pwr3": True},
        exchange=exchange,
    )

    assert state.values == {"pwr1": False, "pwr3": True, "power": 12.0}


async def test_8_channel_light_switch_sets_master_and_confirms_actual_circuits() -> None:
    device = IbgSubDevice(LIGHT8_DID, PID_8_CHANNEL_LIGHT_SWITCH, "RF-light8", True)

    async def exchange(
        _ip: str,
        _port: int,
        packet: bytes,
        _timeout: float,
        _accept: dna.PacketAcceptor | None,
    ) -> bytes:
        _header, body = dna.parse_blc_packet(packet)
        _aes, plain = dna.parse_blc_encrypted_payload(body, SESSION_KEY)
        frame = gateway.parse_gateway_frame(plain)
        assert frame.command_type == gateway.CMD_SET_STATUS
        assert frame.payload == {"did": LIGHT8_DID, "mpwr": 1}
        return _response_packet(
            packet,
            SESSION_KEY,
            gateway.build_gateway_frame(
                gateway.CMD_STATUS_RESPONSE,
                {
                    "did": LIGHT8_DID,
                    "pid": PID_8_CHANNEL_LIGHT_SWITCH,
                    "pwr1": 0,
                    "pwr2": 0,
                    "pwr3": 0,
                    "pwr4": 0,
                    "pwr5": 0,
                    "pwr6": 0,
                    "pwr7": 0,
                    "mpwr": 1,
                },
            ),
        )

    state = await IbgClient().set_subdevice_state(
        IbgSession(device=GATEWAY, session_key=SESSION_KEY),
        device,
        {"mpwr": True},
        exchange=exchange,
    )

    assert state.values == {
        "pwr1": False,
        "pwr2": False,
        "pwr3": False,
        "pwr4": False,
        "pwr5": False,
        "pwr6": False,
        "pwr7": False,
        "mpwr": True,
    }


async def test_single_channel_light_switch_sets_backlight_and_confirms() -> None:
    device = IbgSubDevice(
        SINGLE_CHANNEL_LIGHT_DID,
        PID_SINGLE_CHANNEL_LIGHT_SWITCH,
        "RF-single-light",
        True,
    )

    async def exchange(
        _ip: str,
        _port: int,
        packet: bytes,
        _timeout: float,
        _accept: dna.PacketAcceptor | None,
    ) -> bytes:
        _header, body = dna.parse_blc_packet(packet)
        _aes, plain = dna.parse_blc_encrypted_payload(body, SESSION_KEY)
        frame = gateway.parse_gateway_frame(plain)
        assert frame.command_type == gateway.CMD_SET_STATUS
        assert frame.payload == {"did": SINGLE_CHANNEL_LIGHT_DID, "bglight": 1}
        return _response_packet(
            packet,
            SESSION_KEY,
            gateway.build_gateway_frame(
                gateway.CMD_STATUS_RESPONSE,
                {
                    "did": SINGLE_CHANNEL_LIGHT_DID,
                    "pid": PID_SINGLE_CHANNEL_LIGHT_SWITCH,
                    "pwr1": 0,
                    "bglight": 1,
                    "scenarioswitch_1": 0,
                    "scenarioswitch_2": 0,
                },
            ),
        )

    state = await IbgClient().set_subdevice_state(
        IbgSession(device=GATEWAY, session_key=SESSION_KEY),
        device,
        {"bglight": True},
        exchange=exchange,
    )

    assert state.values["bglight"] is True


async def test_dtu_set_state_encodes_voltage_and_confirms_all_fields() -> None:
    device = IbgSubDevice(DTU_DID, PID_DTU, "DTU", True)

    async def exchange(
        _ip: str,
        _port: int,
        packet: bytes,
        _timeout: float,
        _accept: dna.PacketAcceptor | None,
    ) -> bytes:
        _header, body = dna.parse_blc_packet(packet)
        _aes, plain = dna.parse_blc_encrypted_payload(body, SESSION_KEY)
        frame = gateway.parse_gateway_frame(plain)
        assert frame.command_type == gateway.CMD_SET_STATUS
        assert frame.payload == {"did": DTU_DID, "pwr2": 1, "voltage": 75}
        return _response_packet(
            packet,
            SESSION_KEY,
            gateway.build_gateway_frame(
                gateway.CMD_STATUS_RESPONSE,
                {
                    "did": DTU_DID,
                    "pid": PID_DTU,
                    "pwr1": 0,
                    "pwr2": 1,
                    "voltage": 75,
                },
            ),
        )

    state = await IbgClient().set_subdevice_state(
        IbgSession(device=GATEWAY, session_key=SESSION_KEY),
        device,
        {"pwr2": True, "voltage": 7.5},
        exchange=exchange,
    )

    assert state.values == {"pwr1": False, "pwr2": True, "voltage": 7.5}


async def test_modbus_ac_set_state_encodes_and_confirms_all_fields() -> None:
    device = IbgSubDevice(MODBUS_AC_DID, PID_MODBUS_AC, "Air conditioner", True)

    async def exchange(
        _ip: str,
        _port: int,
        packet: bytes,
        _timeout: float,
        _accept: dna.PacketAcceptor | None,
    ) -> bytes:
        _header, body = dna.parse_blc_packet(packet)
        _aes, plain = dna.parse_blc_encrypted_payload(body, SESSION_KEY)
        frame = gateway.parse_gateway_frame(plain)
        assert frame.command_type == gateway.CMD_SET_STATUS
        assert frame.payload == {"did": MODBUS_AC_DID, "pwr": 1, "ac_mode": 1, "mark": 2, "temp": 24}
        return _response_packet(
            packet,
            SESSION_KEY,
            gateway.build_gateway_frame(
                gateway.CMD_STATUS_RESPONSE,
                {
                    "did": MODBUS_AC_DID,
                    "pid": PID_MODBUS_AC,
                    "pwr": 1,
                    "ac_mode": 1,
                    "mark": 2,
                    "temp": 24,
                    "envtemp": 235,
                    "errcode": 0,
                },
            ),
        )

    state = await IbgClient().set_subdevice_state(
        IbgSession(device=GATEWAY, session_key=SESSION_KEY),
        device,
        {"pwr": True, "ac_mode": 1, "mark": 2, "temp": 24.0},
        exchange=exchange,
    )

    assert state.values == {
        "pwr": True,
        "ac_mode": 1,
        "mark": 2,
        "temp": 24,
        "envtemp": 23.5,
        "errcode": 0,
    }


async def test_water_ac_panel_set_state_encodes_and_confirms_all_fields() -> None:
    device = IbgSubDevice(WATER_AC_PANEL_DID, PID_WATER_AC_PANEL, "RF-water-panel", True)

    async def exchange(
        _ip: str,
        _port: int,
        packet: bytes,
        _timeout: float,
        _accept: dna.PacketAcceptor | None,
    ) -> bytes:
        _header, body = dna.parse_blc_packet(packet)
        _aes, plain = dna.parse_blc_encrypted_payload(body, SESSION_KEY)
        frame = gateway.parse_gateway_frame(plain)
        assert frame.command_type == gateway.CMD_SET_STATUS
        assert frame.payload == {
            "did": WATER_AC_PANEL_DID,
            "pwr": 1,
            "ac_mode": 3,
            "ac_mark": 2,
            "temp": 24,
        }
        return _response_packet(
            packet,
            SESSION_KEY,
            gateway.build_gateway_frame(
                gateway.CMD_STATUS_RESPONSE,
                {
                    "did": WATER_AC_PANEL_DID,
                    "pid": PID_WATER_AC_PANEL,
                    "pwr": 1,
                    "ac_mode": 3,
                    "ac_mark": 2,
                    "temp": 24,
                    "insidetemp": 29,
                    "keylocked": 0,
                },
            ),
        )

    state = await IbgClient().set_subdevice_state(
        IbgSession(device=GATEWAY, session_key=SESSION_KEY),
        device,
        {"pwr": True, "ac_mode": 3, "ac_mark": 2, "temp": 24.0},
        exchange=exchange,
    )

    assert state.values == {
        "pwr": True,
        "ac_mode": 3,
        "ac_mark": 2,
        "temp": 24,
        "insidetemp": 29,
    }


async def test_eac1_set_state_encodes_and_confirms_all_writable_fields() -> None:
    device = IbgSubDevice(EAC1_DID, PID_EAC1_PANEL, "RF-eac1", True)

    async def exchange(
        _ip: str,
        _port: int,
        packet: bytes,
        _timeout: float,
        _accept: dna.PacketAcceptor | None,
    ) -> bytes:
        _header, body = dna.parse_blc_packet(packet)
        _aes, plain = dna.parse_blc_encrypted_payload(body, SESSION_KEY)
        frame = gateway.parse_gateway_frame(plain)
        assert frame.command_type == gateway.CMD_SET_STATUS
        assert frame.payload == {
            "did": EAC1_DID,
            "pwr": 1,
            "ac_mode": 1,
            "ac_mark": 3,
            "temp": 26,
            "keylocked": 1,
        }
        return _response_packet(
            packet,
            SESSION_KEY,
            gateway.build_gateway_frame(
                gateway.CMD_STATUS_RESPONSE,
                {
                    "did": EAC1_DID,
                    "pid": PID_EAC1_PANEL,
                    "pwr": 1,
                    "ac_mode": 1,
                    "ac_mark": 3,
                    "temp": 26,
                    "insidetemp": 23,
                    "insidehumid": 55,
                    "keylocked": 1,
                    "acpanel_devtype": 0,
                },
            ),
        )

    state = await IbgClient().set_subdevice_state(
        IbgSession(device=GATEWAY, session_key=SESSION_KEY),
        device,
        {"pwr": True, "ac_mode": 1, "ac_mark": 3, "temp": 26.0, "keylocked": True},
        exchange=exchange,
    )

    assert state.values == {
        "pwr": True,
        "ac_mode": 1,
        "ac_mark": 3,
        "temp": 26,
        "insidetemp": 23,
        "insidehumid": 55,
        "keylocked": True,
        "acpanel_devtype": "water_cooled",
    }


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"pwr": 1}, "boolean"),
        ({"mark": -1}, "between 0 and 3"),
        ({"mark": 4}, "between 0 and 3"),
        ({"mark": 1.0}, "mode values must be integers"),
        ({"ac_mode": 5}, "between 0 and 4"),
        ({"temp": 15}, "between 16 and 32"),
        ({"temp": 33}, "between 16 and 32"),
        ({"temp": 20.5}, "1 C steps"),
        ({"errcode": 0}, "field"),
    ],
)
async def test_modbus_ac_set_state_rejects_unsafe_requests(
    changes: dict[str, bool | int | float],
    message: str,
) -> None:
    device = IbgSubDevice(MODBUS_AC_DID, PID_MODBUS_AC, "Air conditioner", True)
    with pytest.raises(IbgProtocolError, match=message):
        await IbgClient().set_subdevice_state(
            IbgSession(device=GATEWAY, session_key=SESSION_KEY),
            device,
            changes,
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"pwr": 1}, "boolean"),
        ({"ac_mark": -1}, "between 0 and 3"),
        ({"ac_mark": 4}, "between 0 and 3"),
        ({"ac_mark": 1.0}, "mode values must be integers"),
        ({"ac_mode": 2}, "one of 0, 1, or 3"),
        ({"ac_mode": 4}, "one of 0, 1, or 3"),
        ({"temp": 4}, "between 5 and 35"),
        ({"temp": 36}, "between 5 and 35"),
        ({"temp": 20.5}, "1 C steps"),
        ({"insidetemp": 20}, "field"),
    ],
)
async def test_water_ac_panel_set_state_rejects_unsafe_requests(
    changes: dict[str, bool | int | float],
    message: str,
) -> None:
    device = IbgSubDevice(WATER_AC_PANEL_DID, PID_WATER_AC_PANEL, "RF-water-panel", True)
    with pytest.raises(IbgProtocolError, match=message):
        await IbgClient().set_subdevice_state(
            IbgSession(device=GATEWAY, session_key=SESSION_KEY),
            device,
            changes,
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"pwr": 1}, "boolean"),
        ({"keylocked": 1}, "boolean"),
        ({"ac_mark": -1}, "between 0 and 3"),
        ({"ac_mark": 4}, "between 0 and 3"),
        ({"ac_mode": 2}, "one of 0, 1, or 3"),
        ({"ac_mode": 4}, "one of 0, 1, or 3"),
        ({"temp": 15}, "between 16 and 30"),
        ({"temp": 31}, "between 16 and 30"),
        ({"temp": 20.5}, "1 C steps"),
        ({"insidehumid": 50}, "field"),
        ({"acpanel_devtype": 0}, "field"),
    ],
)
async def test_eac1_set_state_rejects_unsafe_requests(
    changes: dict[str, bool | int | float],
    message: str,
) -> None:
    device = IbgSubDevice(EAC1_DID, PID_EAC1_PANEL, "RF-eac1", True)
    with pytest.raises(IbgProtocolError, match=message):
        await IbgClient().set_subdevice_state(
            IbgSession(device=GATEWAY, session_key=SESSION_KEY),
            device,
            changes,
        )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"pwr1": 1}, "boolean"),
        ({"voltage": -0.1}, "between 0 and 10"),
        ({"voltage": 10.1}, "between 0 and 10"),
        ({"voltage": 1.25}, "0.1 V steps"),
        ({"voltage": True}, "between 0 and 10"),
        ({"d1": 1.0}, "field"),
    ],
)
async def test_dtu_set_state_rejects_unsafe_requests(
    changes: dict[str, bool | int | float],
    message: str,
) -> None:
    device = IbgSubDevice(DTU_DID, PID_DTU, "DTU", True)
    with pytest.raises(IbgProtocolError, match=message):
        await IbgClient().set_subdevice_state(
            IbgSession(device=GATEWAY, session_key=SESSION_KEY),
            device,
            changes,
        )


@pytest.mark.parametrize(
    ("device", "changes", "message"),
    [
        (IbgSubDevice(BOX7_DID, PID_SR3_SENSOR, "Sensor", True), {"pwr1": True}, "unsupported writable"),
        (
            IbgSubDevice(BOX7_DID, PID_DLT645_ELECTRICITY_METER, "DLT645", True),
            {"pwr": True},
            "unsupported writable",
        ),
        (IbgSubDevice(BOX7_DID, PID_BOX7_CONTROLLER, "BOX7", False), {"pwr1": True}, "offline"),
        (IbgSubDevice(BOX7_DID, PID_BOX7_CONTROLLER, "BOX7", True), {}, "at least one"),
        (IbgSubDevice(BOX7_DID, PID_BOX7_CONTROLLER, "BOX7", True), {"alarm_state": True}, "field"),
        (IbgSubDevice(BOX7_DID, PID_BOX7_CONTROLLER, "BOX7", True), {"pwr1": 1}, "boolean"),
        (
            IbgSubDevice(LIGHT8_DID, PID_8_CHANNEL_LIGHT_SWITCH, "RF-light8", True),
            {"mpwr": 2},
            "boolean",
        ),
        (
            IbgSubDevice(
                SINGLE_CHANNEL_LIGHT_DID,
                PID_SINGLE_CHANNEL_LIGHT_SWITCH,
                "RF-single-light",
                True,
            ),
            {"scenarioswitch_1": True},
            "field",
        ),
    ],
)
async def test_box7_set_state_rejects_unsafe_requests(
    device: IbgSubDevice,
    changes: dict[str, bool],
    message: str,
) -> None:
    with pytest.raises((IbgConnectionError, IbgProtocolError), match=message):
        await IbgClient().set_subdevice_state(
            IbgSession(device=GATEWAY, session_key=SESSION_KEY),
            device,
            changes,
        )


async def test_unsupported_pid_is_not_queried() -> None:
    device = IbgSubDevice(SENSOR_DID, "0" * 32, "Unknown", True)
    with pytest.raises(IbgProtocolError, match="unsupported"):
        await IbgClient().get_subdevice_state(
            IbgSession(device=GATEWAY, session_key=SESSION_KEY),
            device,
        )


def test_gateway_frame_round_trip_and_validation() -> None:
    frame = gateway.build_gateway_frame(gateway.CMD_GATEWAY_LIST, {"count": 10, "index": 0})
    parsed = gateway.parse_gateway_frame(frame)
    assert parsed.command_type == gateway.CMD_GATEWAY_LIST
    assert parsed.payload == {"count": 10, "index": 0}

    corrupt = bytearray(frame)
    corrupt[-1] ^= 1
    with pytest.raises(gateway.GatewayProtocolError, match="checksum"):
        gateway.parse_gateway_frame(bytes(corrupt))

    non_object = json.dumps([1, 2]).encode()
    raw = bytearray(struct.pack("<IHHHH", gateway.UART_MAGIC, 0, 1, len(non_object), 1) + non_object)
    dna.write_checksum_le(raw, 4)
    with pytest.raises(gateway.GatewayProtocolError, match="object"):
        gateway.parse_gateway_frame(bytes(raw))
