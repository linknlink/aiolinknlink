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
    UnitOfFrequency,
    UnitOfPower,
    UnitOfReactivePower,
    UnitOfTemperature,
    UnitOfVolume,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from aiolinknlink import (
    PID_BOX7_CONTROLLER,
    PID_DLT645_ELECTRICITY_METER,
    PID_DTU,
    PID_EAC1_PANEL,
    PID_ESENSOR_2000_GEN1,
    PID_ESENSOR_2000_GEN2,
    PID_MODBUS_AC,
    PID_MODBUS_ELECTRICITY_METER,
    PID_MODBUS_MULTI_SENSOR,
    PID_MODBUS_WATER_METER,
    PID_SR3_SENSOR,
)

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
ESENSOR_2000_GEN1_SENSORS = (SR3_SENSORS[0], SR3_SENSORS[1], SR3_SENSORS[3])

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

MODBUS_WATER_SENSORS = (
    SensorEntityDescription(
        key="total_water",
        name="Total water",
        translation_key="total_water",
        device_class=SensorDeviceClass.WATER,
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="water_flow_rate",
        name="Water flow rate",
        translation_key="water_flow_rate",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        native_unit_of_measurement=UnitOfVolumeFlowRate.LITERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
    ),
)

EAC1_SENSORS = (
    SensorEntityDescription(
        key="insidehumid",
        name="Indoor humidity",
        translation_key="indoor_humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    SensorEntityDescription(
        key="acpanel_devtype",
        name="Device type",
        translation_key="ac_panel_device_type",
        device_class=SensorDeviceClass.ENUM,
        options=["water_cooled", "vrv"],
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)

MODBUS_ELECTRICITY_METER_SENSORS = (
    SensorEntityDescription(
        key="Combenergy",
        name="Combined active energy",
        translation_key="combined_active_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="totalconsum",
        name="Forward active energy",
        translation_key="forward_active_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
    ),
    SensorEntityDescription(
        key="Reverenergy",
        name="Reverse active energy",
        translation_key="reverse_active_energy",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
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
            key=f"{pair}_linevoltage",
            name=f"Line {pair} voltage",
            translation_key=f"line_{pair.lower()}_voltage",
            device_class=SensorDeviceClass.VOLTAGE,
            native_unit_of_measurement=UnitOfElectricPotential.VOLT,
            state_class=SensorStateClass.MEASUREMENT,
        )
        for pair in ("AB", "BC", "AC")
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
    SensorEntityDescription(
        key="power",
        name="Total active power",
        translation_key="total_active_power",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    *(
        SensorEntityDescription(
            key=f"{phase}phasepower",
            name=f"Phase {phase} active power",
            translation_key=f"phase_{phase.lower()}_active_power",
            device_class=SensorDeviceClass.POWER,
            native_unit_of_measurement=UnitOfPower.WATT,
            state_class=SensorStateClass.MEASUREMENT,
        )
        for phase in "ABC"
    ),
    SensorEntityDescription(
        key="Combpowerfactor",
        name="Combined power factor",
        translation_key="combined_power_factor",
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:cosine-wave",
    ),
    *(
        SensorEntityDescription(
            key=f"{phase}powerfactor",
            name=f"Phase {phase} power factor",
            translation_key=f"phase_{phase.lower()}_power_factor",
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:cosine-wave",
        )
        for phase in "ABC"
    ),
    *(
        SensorEntityDescription(
            key=f"{phase}phasehmccurrent",
            name=f"Phase {phase} harmonic current",
            translation_key=f"phase_{phase.lower()}_harmonic_current",
            device_class=SensorDeviceClass.CURRENT,
            native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
            state_class=SensorStateClass.MEASUREMENT,
        )
        for phase in "ABC"
    ),
    SensorEntityDescription(
        key="frequency",
        name="Frequency",
        translation_key="frequency",
        device_class=SensorDeviceClass.FREQUENCY,
        native_unit_of_measurement=UnitOfFrequency.HERTZ,
        state_class=SensorStateClass.MEASUREMENT,
    ),
    *(
        SensorEntityDescription(
            key=f"{phase}phase_reactivepower",
            name=f"Phase {phase} reactive power",
            translation_key=f"phase_{phase.lower()}_reactive_power",
            native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
            state_class=SensorStateClass.MEASUREMENT,
            icon="mdi:flash-outline",
        )
        for phase in "ABC"
    ),
    SensorEntityDescription(
        key="Total_reactivepower",
        name="Total reactive power",
        translation_key="total_reactive_power",
        native_unit_of_measurement=UnitOfReactivePower.VOLT_AMPERE_REACTIVE,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:flash-outline",
    ),
    SensorEntityDescription(
        key="devicename",
        name="Reported device name",
        translation_key="reported_device_name",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:label-outline",
    ),
    SensorEntityDescription(
        key="elec_param",
        name="Electrical parameters",
        translation_key="electrical_parameters",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:text-box-outline",
    ),
    SensorEntityDescription(
        key="address",
        name="Modbus address",
        translation_key="modbus_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:numeric",
    ),
    SensorEntityDescription(
        key="transformerratio",
        name="Transformer ratio",
        translation_key="transformer_ratio",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:current-ac",
    ),
    SensorEntityDescription(
        key="modbusreadresult",
        name="Modbus read result",
        translation_key="modbus_read_result",
        device_class=SensorDeviceClass.ENUM,
        options=["success", "failure", "partial_success"],
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    SensorEntityDescription(
        key="modbuswriteresult",
        name="Modbus write result",
        translation_key="modbus_write_result",
        device_class=SensorDeviceClass.ENUM,
        options=["success", "failure", "partial_success"],
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)

DLT645_MEASUREMENT_KEYS = frozenset(
    {
        "Combenergy",
        "totalconsum",
        "Reverenergy",
        *(f"{phase}phasevolt" for phase in "ABC"),
        *(f"{phase}phasecurrent" for phase in "ABC"),
        "power",
        *(f"{phase}phasepower" for phase in "ABC"),
        "Combpowerfactor",
        *(f"{phase}powerfactor" for phase in "ABC"),
        *(f"{phase}phasehmccurrent" for phase in "ABC"),
    }
)
DLT645_ELECTRICITY_METER_SENSORS = tuple(
    description for description in MODBUS_ELECTRICITY_METER_SENSORS if description.key in DLT645_MEASUREMENT_KEYS
) + (
    SensorEntityDescription(
        key="elec_param",
        name="Electrical parameters",
        translation_key="electrical_parameters",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:text-box-outline",
    ),
    SensorEntityDescription(
        key="address",
        name="DLT645 address",
        translation_key="dlt645_address",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        icon="mdi:numeric",
    ),
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
    PID_ESENSOR_2000_GEN1: ESENSOR_2000_GEN1_SENSORS,
    PID_ESENSOR_2000_GEN2: SR3_SENSORS,
    PID_BOX7_CONTROLLER: BOX7_SENSORS,
    PID_DTU: DTU_ELECTRICAL_SENSORS,
    PID_MODBUS_AC: MODBUS_AC_SENSORS,
    PID_MODBUS_MULTI_SENSOR: MODBUS_MULTI_SENSORS,
    PID_MODBUS_WATER_METER: MODBUS_WATER_SENSORS,
    PID_MODBUS_ELECTRICITY_METER: MODBUS_ELECTRICITY_METER_SENSORS,
    PID_DLT645_ELECTRICITY_METER: DLT645_ELECTRICITY_METER_SENSORS,
    PID_EAC1_PANEL: EAC1_SENSORS,
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
    """One reviewed iBG subdevice sensor."""

    entity_description: SensorEntityDescription

    def __init__(self, coordinator, did: str, description: SensorEntityDescription) -> None:
        super().__init__(coordinator, did, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> int | float | str | None:
        """Return the latest safe scalar value."""
        value = self._value()
        return value if isinstance(value, (int, float, str)) and not isinstance(value, bool) else None


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
