"""Focused tests for the Home Assistant iBG coordinator and entities."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")

from homeassistant.components.climate.const import HVACMode  # noqa: E402
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass  # noqa: E402
from homeassistant.const import (  # noqa: E402
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.helpers.update_coordinator import UpdateFailed  # noqa: E402

from aiolinknlink import (  # noqa: E402
    PID_BOX7_CONTROLLER,
    PID_DTU,
    PID_MODBUS_AC,
    PID_MODBUS_MULTI_SENSOR,
    PID_MODBUS_WATER_METER,
    PID_WATER_AC_PANEL,
    IbgConnectionError,
    IbgDevice,
    IbgProtocolError,
    IbgSession,
    IbgSubDevice,
    IbgSubDeviceState,
)
from custom_components.linknlink.binary_sensor import DTU_BINARY_SENSORS, IbgBinarySensor  # noqa: E402
from custom_components.linknlink.climate import IbgModbusClimate, IbgWaterAcPanelClimate  # noqa: E402
from custom_components.linknlink.const import resolve_local_key_hex  # noqa: E402
from custom_components.linknlink.coordinator import (  # noqa: E402
    IbgCoordinatorData,
    IbgDataUpdateCoordinator,
)
from custom_components.linknlink.entity import IbgCoordinatorEntity  # noqa: E402
from custom_components.linknlink.number import IbgDtuVoltageOutput  # noqa: E402
from custom_components.linknlink.sensor import (  # noqa: E402
    BOX7_SENSORS,
    DTU_ANALOG_INPUTS,
    DTU_ELECTRICAL_SENSORS,
    MODBUS_AC_SENSORS,
    MODBUS_MULTI_SENSORS,
    MODBUS_WATER_SENSORS,
    SENSORS_BY_PID,
    SR3_SENSORS,
    IbgDtuAnalogInputSensor,
)
from custom_components.linknlink.switch import BOX7_SWITCHES, DTU_SWITCHES, IbgPowerSwitch  # noqa: E402

GATEWAY = IbgDevice(
    id="001122334455",
    ip="192.168.1.10",
    port=80,
    mac="00:11:22:33:44:55",
    type_id=0x2B71,
)
SUBDEVICE = IbgSubDevice(
    did="00112233445566778899aabbccddeeff",
    pid="00000000000000000000000005000100",
    name="Room sensor",
    online=True,
)


def test_local_key_resolution_persists_negotiated_pairing_key() -> None:
    negotiated = b"0123456789abcdef"

    assert resolve_local_key_hex("", negotiated) == negotiated.hex()
    assert resolve_local_key_hex("fedcba9876543210fedcba9876543210", negotiated) == ("fedcba9876543210fedcba9876543210")
    assert resolve_local_key_hex("", None) == ""


def _coordinator(client: object) -> IbgDataUpdateCoordinator:
    coordinator = object.__new__(IbgDataUpdateCoordinator)
    coordinator.client = client  # type: ignore[assignment]
    coordinator.device = GATEWAY
    coordinator.session = IbgSession(device=GATEWAY, session_key=b"0123456789abcdef")
    coordinator.local_key = None
    coordinator.push_subscription = None
    coordinator._last_keypressed = {}
    coordinator._key_event_counts = {}
    return coordinator


async def test_coordinator_reauthenticates_once_after_connection_failure() -> None:
    client = AsyncMock()
    refreshed = IbgSession(device=GATEWAY, session_key=b"fedcba9876543210")
    client.list_subdevices.side_effect = [IbgConnectionError("expired"), [SUBDEVICE]]
    client.connect.return_value = refreshed
    client.read_supported_states.return_value = {SUBDEVICE.did: None}
    coordinator = _coordinator(client)

    data = await coordinator._async_update_data()

    assert data.subdevices == (SUBDEVICE,)
    assert data.states == {SUBDEVICE.did: None}
    assert coordinator.session is refreshed
    client.connect.assert_awaited_once_with(GATEWAY, local_key=None)


async def test_coordinator_converts_repeated_connection_failure() -> None:
    client = AsyncMock()
    client.list_subdevices.side_effect = IbgConnectionError("offline")
    client.connect.side_effect = IbgConnectionError("still offline")
    coordinator = _coordinator(client)

    with pytest.raises(UpdateFailed, match="Could not update"):
        await coordinator._async_update_data()


async def test_coordinator_converts_protocol_failure_without_reauth() -> None:
    client = AsyncMock()
    client.list_subdevices.side_effect = IbgProtocolError("invalid response")
    coordinator = _coordinator(client)

    with pytest.raises(UpdateFailed, match="Invalid response"):
        await coordinator._async_update_data()
    client.connect.assert_not_awaited()


def test_entity_availability_and_safe_value() -> None:
    state = IbgSubDeviceState(
        SUBDEVICE,
        {"temperature": 25.5},
        datetime.now(UTC),
    )
    coordinator = object.__new__(IbgDataUpdateCoordinator)
    coordinator.device = GATEWAY
    coordinator.data = IbgCoordinatorData((SUBDEVICE,), {SUBDEVICE.did: state})
    coordinator.last_update_success = True
    entity = IbgCoordinatorEntity(coordinator, SUBDEVICE.did, "temperature")

    assert entity.available
    assert entity._value() == 25.5
    assert entity.device_info["via_device"] == ("linknlink", GATEWAY.id)

    coordinator.data = IbgCoordinatorData((SUBDEVICE,), {SUBDEVICE.did: None})
    assert not entity.available
    assert entity._value() is None


def test_coordinator_counts_only_two_to_one_key_edges() -> None:
    coordinator = _coordinator(AsyncMock())

    def key_state(value: int) -> IbgSubDeviceState:
        return IbgSubDeviceState(SUBDEVICE, {"keypressed": value}, datetime.now(UTC))

    coordinator._track_key_edges({SUBDEVICE.did: key_state(1)})
    assert coordinator._key_event_counts == {}
    coordinator._track_key_edges({SUBDEVICE.did: key_state(2)})
    coordinator._track_key_edges({SUBDEVICE.did: key_state(1)})
    coordinator._track_key_edges({SUBDEVICE.did: key_state(1)})
    coordinator._track_key_edges({SUBDEVICE.did: key_state(2)})

    assert coordinator._key_event_counts == {SUBDEVICE.did: 1}


async def test_box7_switch_uses_confirmed_coordinator_control() -> None:
    device = IbgSubDevice(
        did="00112233445566778899aabbccddeef0",
        pid=PID_BOX7_CONTROLLER,
        name="BOX7",
        online=True,
    )
    coordinator = object.__new__(IbgDataUpdateCoordinator)
    coordinator.device = GATEWAY
    coordinator.data = IbgCoordinatorData(
        (device,),
        {device.did: IbgSubDeviceState(device, {"pwr1": False}, datetime.now(UTC))},
    )
    coordinator.last_update_success = True
    coordinator.async_set_subdevice_state = AsyncMock()  # type: ignore[method-assign]
    entity = IbgPowerSwitch(coordinator, device.did, BOX7_SWITCHES[0])

    assert entity.is_on is False
    await entity.async_turn_on()
    coordinator.async_set_subdevice_state.assert_awaited_once_with(device.did, {"pwr1": True})


async def test_dtu_entities_use_input_modes_and_confirmed_controls() -> None:
    device = IbgSubDevice(
        did="00112233445566778899aabbccddeef1",
        pid=PID_DTU,
        name="DTU",
        online=True,
    )
    coordinator = object.__new__(IbgDataUpdateCoordinator)
    coordinator.device = GATEWAY
    coordinator.data = IbgCoordinatorData(
        (device,),
        {
            device.did: IbgSubDeviceState(
                device,
                {
                    "d1": 12.34,
                    "date1_type": 0,
                    "d2": 7.5,
                    "date2_type": 1,
                    "signalinput1": True,
                    "voltage": 8.0,
                },
                datetime.now(UTC),
            )
        },
    )
    coordinator.last_update_success = True
    coordinator.async_set_subdevice_state = AsyncMock()  # type: ignore[method-assign]

    current_input = IbgDtuAnalogInputSensor(coordinator, device.did, DTU_ANALOG_INPUTS[0])
    voltage_input = IbgDtuAnalogInputSensor(coordinator, device.did, DTU_ANALOG_INPUTS[1])
    signal = IbgBinarySensor(coordinator, device.did, DTU_BINARY_SENSORS[0])
    output = IbgDtuVoltageOutput(coordinator, device.did)

    assert current_input.native_value == 12.34
    assert current_input.device_class == SensorDeviceClass.CURRENT
    assert current_input.native_unit_of_measurement == UnitOfElectricCurrent.MILLIAMPERE
    assert voltage_input.native_value == 7.5
    assert voltage_input.device_class == SensorDeviceClass.VOLTAGE
    assert voltage_input.native_unit_of_measurement == UnitOfElectricPotential.VOLT
    assert signal.is_on is True
    assert output.native_value == 8.0
    await output.async_set_native_value(7.5)
    coordinator.async_set_subdevice_state.assert_awaited_once_with(device.did, {"voltage": 7.5})


async def test_modbus_ac_climate_maps_state_and_uses_confirmed_controls() -> None:
    device = IbgSubDevice(
        did="00112233445566778899aabbccddeef2",
        pid=PID_MODBUS_AC,
        name="PLC-2",
        online=True,
    )
    coordinator = object.__new__(IbgDataUpdateCoordinator)
    coordinator.device = GATEWAY
    coordinator.data = IbgCoordinatorData(
        (device,),
        {
            device.did: IbgSubDeviceState(
                device,
                {"pwr": True, "ac_mode": 0, "mark": 3, "temp": 20, "envtemp": 23.0, "errcode": 1},
                datetime.now(UTC),
            )
        },
    )
    coordinator.last_update_success = True
    coordinator.async_set_subdevice_state = AsyncMock()  # type: ignore[method-assign]
    entity = IbgModbusClimate(coordinator, device.did)

    assert entity.hvac_mode == HVACMode.COOL
    assert entity.fan_mode == "high"
    assert entity.target_temperature == 20.0
    assert entity.current_temperature == 23.0

    await entity.async_set_hvac_mode(HVACMode.OFF)
    coordinator.async_set_subdevice_state.assert_awaited_once_with(device.did, {"pwr": False})
    coordinator.async_set_subdevice_state.reset_mock()

    await entity.async_set_hvac_mode(HVACMode.HEAT)
    coordinator.async_set_subdevice_state.assert_awaited_once_with(device.did, {"pwr": True, "ac_mode": 1})
    coordinator.async_set_subdevice_state.reset_mock()

    await entity.async_set_fan_mode("medium")
    coordinator.async_set_subdevice_state.assert_awaited_once_with(device.did, {"mark": 2})
    coordinator.async_set_subdevice_state.reset_mock()

    await entity.async_set_temperature(temperature=24.0)
    coordinator.async_set_subdevice_state.assert_awaited_once_with(device.did, {"temp": 24.0})


async def test_water_ac_panel_climate_maps_state_and_uses_confirmed_controls() -> None:
    device = IbgSubDevice(
        did="00112233445566778899aabbccddeef3",
        pid=PID_WATER_AC_PANEL,
        name="RF-b951a3b7",
        online=True,
    )
    second_device = IbgSubDevice(
        did="00112233445566778899aabbccddeef4",
        pid=PID_WATER_AC_PANEL,
        name="RF-second-panel",
        online=True,
    )
    coordinator = object.__new__(IbgDataUpdateCoordinator)
    coordinator.device = GATEWAY
    coordinator.data = IbgCoordinatorData(
        (device, second_device),
        {
            device.did: IbgSubDeviceState(
                device,
                {"pwr": True, "ac_mode": 0, "ac_mark": 0, "temp": 25, "insidetemp": 29},
                datetime.now(UTC),
            ),
            second_device.did: IbgSubDeviceState(
                second_device,
                {"pwr": False, "ac_mode": 1, "ac_mark": 1, "temp": 20, "insidetemp": 22},
                datetime.now(UTC),
            ),
        },
    )
    coordinator.last_update_success = True
    coordinator.async_set_subdevice_state = AsyncMock()  # type: ignore[method-assign]
    entity = IbgWaterAcPanelClimate(coordinator, device.did)
    second_entity = IbgWaterAcPanelClimate(coordinator, second_device.did)

    assert entity.hvac_mode == HVACMode.COOL
    assert entity.fan_mode == "auto"
    assert entity.target_temperature == 25.0
    assert entity.current_temperature == 29.0
    assert entity.min_temp == 5
    assert entity.max_temp == 35
    assert second_entity.hvac_mode == HVACMode.OFF
    assert entity.unique_id != second_entity.unique_id

    await entity.async_set_hvac_mode(HVACMode.OFF)
    coordinator.async_set_subdevice_state.assert_awaited_once_with(device.did, {"pwr": False})
    coordinator.async_set_subdevice_state.reset_mock()

    await entity.async_set_hvac_mode(HVACMode.FAN_ONLY)
    coordinator.async_set_subdevice_state.assert_awaited_once_with(device.did, {"pwr": True, "ac_mode": 3})
    coordinator.async_set_subdevice_state.reset_mock()

    await entity.async_set_fan_mode("high")
    coordinator.async_set_subdevice_state.assert_awaited_once_with(device.did, {"ac_mark": 3})
    coordinator.async_set_subdevice_state.reset_mock()

    await entity.async_set_temperature(temperature=24.0)
    coordinator.async_set_subdevice_state.assert_awaited_once_with(device.did, {"temp": 24.0})

    with pytest.raises(ValueError, match="Unsupported HVAC mode"):
        await entity.async_set_hvac_mode(HVACMode.DRY)


def test_entity_catalogs_are_pid_specific() -> None:
    assert len(BOX7_SWITCHES) == 7
    assert len(BOX7_SENSORS) == 12
    assert len(DTU_SWITCHES) == 2
    assert len(DTU_ELECTRICAL_SENSORS) == 8
    assert len(DTU_ANALOG_INPUTS) == 3
    assert len(DTU_BINARY_SENSORS) == 3
    assert len(SR3_SENSORS) == 4
    assert len(MODBUS_AC_SENSORS) == 1
    assert len(MODBUS_MULTI_SENSORS) == 4
    assert len(MODBUS_WATER_SENSORS) == 2
    assert SENSORS_BY_PID[PID_BOX7_CONTROLLER] == BOX7_SENSORS
    assert SENSORS_BY_PID[PID_DTU] == DTU_ELECTRICAL_SENSORS
    assert SENSORS_BY_PID[PID_MODBUS_AC] == MODBUS_AC_SENSORS
    assert SENSORS_BY_PID[PID_MODBUS_MULTI_SENSOR] == MODBUS_MULTI_SENSORS
    assert SENSORS_BY_PID[PID_MODBUS_WATER_METER] == MODBUS_WATER_SENSORS
    assert MODBUS_WATER_SENSORS[0].device_class == SensorDeviceClass.WATER
    assert MODBUS_WATER_SENSORS[0].native_unit_of_measurement == UnitOfVolume.CUBIC_METERS
    assert MODBUS_WATER_SENSORS[0].state_class == SensorStateClass.TOTAL_INCREASING
    assert MODBUS_WATER_SENSORS[1].device_class == SensorDeviceClass.VOLUME_FLOW_RATE
    assert MODBUS_WATER_SENSORS[1].native_unit_of_measurement == UnitOfVolumeFlowRate.LITERS_PER_HOUR
    assert MODBUS_WATER_SENSORS[1].state_class == SensorStateClass.MEASUREMENT
    assert [description.name for description in BOX7_SWITCHES] == [f"Switch {channel}" for channel in range(1, 8)]
    assert len({description.name for description in BOX7_SENSORS}) == 12


def test_entity_translations_cover_parameter_derived_names() -> None:
    """Keep per-channel and per-phase names available to HA's entity registry."""
    component_dir = Path(__file__).parents[1] / "custom_components" / "linknlink"
    catalogs = (
        json.loads((component_dir / "translations" / "en.json").read_text()),
        json.loads((component_dir / "translations" / "zh-Hans.json").read_text()),
    )

    for catalog in catalogs:
        sensor_names = catalog["entity"]["sensor"]
        binary_sensor_names = catalog["entity"]["binary_sensor"]
        number_names = catalog["entity"]["number"]
        switch_names = catalog["entity"]["switch"]
        climate_names = catalog["entity"]["climate"]
        assert all(description.translation_key in sensor_names for description in BOX7_SENSORS)
        assert all(description.translation_key in sensor_names for description in DTU_ANALOG_INPUTS)
        assert all(description.translation_key in sensor_names for description in MODBUS_MULTI_SENSORS)
        assert all(description.translation_key in sensor_names for description in MODBUS_WATER_SENSORS)
        assert all(description.translation_key in binary_sensor_names for description in DTU_BINARY_SENSORS)
        assert all(description.translation_key in switch_names for description in BOX7_SWITCHES)
        assert "voltage_output" in number_names
        assert "fault_code" in sensor_names
        assert "air_conditioner" in climate_names
        assert "water_ac_panel" in climate_names
        assert len({switch_names[f"switch_{channel}"]["name"] for channel in range(1, 8)}) == 7
