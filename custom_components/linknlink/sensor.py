"""Sensor platform for LinknLink iBG subdevices."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass
from homeassistant.const import (
    LIGHT_LUX,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from aiolinknlink import PID_BOX7_CONTROLLER, PID_SR3_SENSOR

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

SENSORS_BY_PID = {
    PID_SR3_SENSOR: SR3_SENSORS,
    PID_BOX7_CONTROLLER: BOX7_SENSORS,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LinknLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors for all reviewed iBG subdevice fields."""
    del hass
    coordinator = entry.runtime_data
    async_add_entities(
        IbgSensor(coordinator, device.did, description)
        for device in coordinator.data.subdevices
        for description in SENSORS_BY_PID.get(device.pid, ())
    )


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
