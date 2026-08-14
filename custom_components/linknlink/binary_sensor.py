"""Binary sensor platform for LinknLink iBG subdevices."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from aiolinknlink import PID_SR3_SENSOR

from . import LinknLinkConfigEntry
from .entity import IbgCoordinatorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LinknLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create occupancy sensors for supported iBG subdevices."""
    del hass
    coordinator = entry.runtime_data
    async_add_entities(
        IbgOccupancySensor(coordinator, device.did)
        for device in coordinator.data.subdevices
        if device.pid == PID_SR3_SENSOR
    )


class IbgOccupancySensor(IbgCoordinatorEntity, BinarySensorEntity):
    """iBG subdevice occupancy state."""

    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY
    _attr_translation_key = "occupancy"

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did, "occupancy")

    @property
    def is_on(self) -> bool | None:
        """Return whether presence was detected."""
        value = self._value()
        return value if isinstance(value, bool) else None
