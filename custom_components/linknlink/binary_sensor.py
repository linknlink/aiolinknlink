"""Binary sensor platform for LinknLink iBG subdevices."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from aiolinknlink import PID_DTU, PID_MODBUS_ELECTRICITY_METER, PID_SR3_SENSOR

from . import LinknLinkConfigEntry
from .entity import IbgCoordinatorEntity

SR3_BINARY_SENSORS = (
    BinarySensorEntityDescription(
        key="occupancy",
        name="Occupancy",
        translation_key="occupancy",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
    ),
)
DTU_BINARY_SENSORS = tuple(
    BinarySensorEntityDescription(
        key=f"signalinput{channel}",
        name=f"Signal input {channel}",
        translation_key=f"signal_input_{channel}",
    )
    for channel in range(1, 4)
)
MODBUS_ELECTRICITY_METER_BINARY_SENSORS = tuple(
    BinarySensorEntityDescription(
        key=f"{phase}phaseoverload",
        name=f"Phase {phase} overload",
        translation_key=f"phase_{phase.lower()}_overload",
        device_class=BinarySensorDeviceClass.PROBLEM,
    )
    for phase in "ABC"
)
BINARY_SENSORS_BY_PID = {
    PID_SR3_SENSOR: SR3_BINARY_SENSORS,
    PID_DTU: DTU_BINARY_SENSORS,
    PID_MODBUS_ELECTRICITY_METER: MODBUS_ELECTRICITY_METER_BINARY_SENSORS,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LinknLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create reviewed binary sensors for supported iBG subdevices."""
    del hass
    coordinator = entry.runtime_data
    async_add_entities(
        IbgBinarySensor(coordinator, device.did, description)
        for device in coordinator.data.subdevices
        for description in BINARY_SENSORS_BY_PID.get(device.pid, ())
    )


class IbgBinarySensor(IbgCoordinatorEntity, BinarySensorEntity):
    """One reviewed binary state on an iBG subdevice."""

    entity_description: BinarySensorEntityDescription

    def __init__(self, coordinator, did: str, description: BinarySensorEntityDescription) -> None:
        super().__init__(coordinator, did, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the confirmed binary state."""
        value = self._value()
        return value if isinstance(value, bool) else None
