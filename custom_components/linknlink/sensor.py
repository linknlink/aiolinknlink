"""Sensor platform for LinknLink iBG subdevices."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import (
    CONCENTRATION_PARTS_PER_MILLION,
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from aiolinknlink import PID_BOX7_CONTROLLER, PID_DTU, PID_MODBUS_AC, PID_MODBUS_MULTI_SENSOR, PID_SR3_SENSOR

from . import LinknLinkConfigEntry
from .entity import IbgCoordinatorEntity

SR3_SENSORS = (
    SensorEntityDescription(
        key="temperature",
        name="Temperature",
        translation_key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="humidity",
        name="Humidity",
        translation_key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="illuminance",
        name="Illuminance",
        translation_key="illuminance",
        device_class=SensorDeviceClass.ILLUMINANCE,
        native_unit_of_measurement=LIGHT_LUX,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="battery",
        name="Battery",
        translation_key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)

BOX7_SENSORS = (
    SensorEntityDescription(
        key="power",
        name="Power",
        translation_key="power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="totalconsum",
        name="Total energy",
        translation_key="total_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    *(
        SensorEntityDescription(
            key=f"envtemp{channel}",
            name=f"Temperature {channel}",
            translation_key=f"temperature_{channel}",
            device_class=SensorDeviceClass.TEMPERATURE,
            native_unit_of_measurement=UnitOfTemperature.CELSIUS,
            state_class=SensorStateClass.MEASUREMENT,
        )
        for channel in range(1, 5)
    ),
    *(
        SensorEntityDescription(
            key=f"{phase}phasevolt",
            name=f"Phase {phase} voltage",
            translation_key=f"phase_{phase.lower()}_voltage",
            device_class=SensorDeviceClass.VOLTAGE,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            state_class=SensorStateClass.MEASUREMENT,
        )
        for phase in "ABC"
    ),
    *(
        SensorEntityDescription(
            key=f"{phase}phasecurrent",
            name=f"Phase {phase} current",
            translation_key=f"phase_{phase.lower()}_current",
            device_class=SensorDeviceClass.CURRENT,
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            state_class=SensorStateClass.MEASUREMENT,
        )
        for phase in "ABC"
    ),
)

DTU_ELECTRICAL_SENSORS = tuple(description for description in BOX7_SENSORS if not description.key.startswith("envtemp"))

MODBUS_AC_SENSORS = (
    SensorEntityDescription(
        key="errcode",
        name="Fault code",
        translation_key="fault_code",
        entity_category=EntityCategory.DIAGNOSTIC,
        icon="mdi:alert-circle-outline",
    ),
)

MODBUS_MULTI_SENSORS = (
    SR3_SENSORS[0],
    SensorEntityDescription(
        key="carbon_dioxide",
        name="Carbon dioxide",
        translation_key="carbon_dioxide",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SR3_SENSORS[1],
    SR3_SENSORS[2],
)


@dataclass(frozen=True, kw_only=True)
class DtuAnalogInputEntityDescription(SensorEntityDescription):
    """Describe one DTU analog input and its companion mode field."""

    mode_key: str


DTU_ANALOG_INPUTS = tuple(
    DtuAnalogInputEntityDescription(
        key=f"d{channel}",
        name=f"Analog input {channel}",
        translation_key=f"analog_input_{channel}",
        state_class=SensorStateClass.MEASUREMENT,
        mode_key=f"date{channel}_type",
    )
    for channel in range(1, 4)
)

SENSORS_BY_PID = {
    PID_SR3_SENSOR: SR3_SENSORS,
    PID_BOX7_CONTROLLER: BOX7_SENSORS,
    PID_DTU: DTU_ELECTRICAL_SENSORS,
    PID_MODBUS_AC: MODBUS_AC_SENSORS,
    PID_MODBUS_MULTI_SENSOR: MODBUS_MULTI_SENSORS,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LinknLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors for all reviewed iBG subdevice fields."""
    del hass
    coordinator = entry.runtime_data
    entities = [
        IbgSensor(coordinator, device.did, description)
        for device in coordinator.data.subdevices
        for description in SENSORS_BY_PID.get(device.pid, ())
    ]
    entities.extend(
        IbgDtuAnalogInputSensor(coordinator, device.did, description)
        for device in coordinator.data.subdevices
        if device.pid == PID_DTU
        for description in DTU_ANALOG_INPUTS
    )
    async_add_entities(entities)


class IbgSensor(IbgCoordinatorEntity, SensorEntity):
    """One numeric iBG subdevice sensor."""

    entity_description: SensorEntityDescription

    def __init__(self, coordinator, did: str, description: SensorEntityDescription) -> None:
        super().__init__(coordinator, did, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | float | None:
        """Return the latest safe numeric value."""
        value = self._value()
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


class IbgDtuAnalogInputSensor(IbgSensor):
    """One DTU analog input whose configured mode selects mA or V."""

    entity_description: DtuAnalogInputEntityDescription

    @property
    def device_class(self) -> SensorDeviceClass | None:
        """Return current or voltage according to the DTU input mode."""
        mode = self._input_mode()
        if mode == 0:
            return SensorDeviceClass.CURRENT
        if mode == 1:
            return SensorDeviceClass.VOLTAGE
        return None

    @property
    def native_unit_of_measurement(self) -> str | None:
        """Return mA for current mode and V for voltage mode."""
        mode = self._input_mode()
        if mode == 0:
            return UnitOfElectricCurrent.MILLIAMPERE
        if mode == 1:
            return UnitOfElectricPotential.VOLT
        return None

    def _input_mode(self) -> int | None:
        state = self.coordinator.data.states.get(self.did)
        if state is None:
            return None
        mode = state.values.get(self.entity_description.mode_key)
        return mode if isinstance(mode, int) and not isinstance(mode, bool) and mode in {0, 1} else None
