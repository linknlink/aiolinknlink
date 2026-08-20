"""Event platform for LinknLink iBG physical keys."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from aiolinknlink import PID_ESENSOR_2000_GEN1, PID_ESENSOR_2000_GEN2, PID_SR3_SENSOR

from . import LinknLinkConfigEntry
from .entity import IbgCoordinatorEntity

EVENT_TYPE_PRESSED = "pressed"
EVENT_TYPE_DOUBLE_PRESSED = "double_pressed"
EVENT_TYPE_LONG_PRESSED = "long_pressed"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LinknLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a physical key event entity for each supported iBG sensor."""
    del hass
    coordinator = entry.runtime_data
    async_add_entities(
        IbgKeyEvent(coordinator, device.did) for device in coordinator.data.subdevices if device.pid == PID_SR3_SENSOR
    )
    async_add_entities(
        IbgEsensorGen1KeyEvent(coordinator, device.did)
        for device in coordinator.data.subdevices
        if device.pid == PID_ESENSOR_2000_GEN1
    )
    async_add_entities(
        IbgEsensorKeyEvent(coordinator, device.did)
        for device in coordinator.data.subdevices
        if device.pid == PID_ESENSOR_2000_GEN2
    )


class IbgKeyEvent(IbgCoordinatorEntity, EventEntity):
    """Physical key press events reported by an iBG subdevice."""

    _attr_event_types = [EVENT_TYPE_PRESSED]
    _attr_translation_key = "key"

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did, "key")
        self._seen_count = coordinator.data.key_event_counts.get(did, 0)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Emit one HA event for each newly observed press edge."""
        count = self.coordinator.data.key_event_counts.get(self.did, 0)
        if count > self._seen_count:
            self._seen_count = count
            self._trigger_event(EVENT_TYPE_PRESSED, {"keypressed": 1})
        super()._handle_coordinator_update()


class IbgEsensorKeyEvent(IbgCoordinatorEntity, EventEntity):
    """Physical key actions reported by an eSensor-2000 second generation."""

    _attr_event_types = [EVENT_TYPE_PRESSED, EVENT_TYPE_DOUBLE_PRESSED, EVENT_TYPE_LONG_PRESSED]
    _attr_translation_key = "esensor_key"

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did, "key")
        self._seen_count = coordinator.data.key_event_counts.get(did, 0)

    @callback
    def _handle_coordinator_update(self) -> None:
        count = self.coordinator.data.key_event_counts.get(self.did, 0)
        if count > self._seen_count:
            self._seen_count = count
            value = self.coordinator.data.key_event_values.get(self.did)
            event_type = {
                1: EVENT_TYPE_PRESSED,
                3: EVENT_TYPE_DOUBLE_PRESSED,
                4: EVENT_TYPE_LONG_PRESSED,
            }.get(value)
            if event_type is not None:
                self._trigger_event(event_type, {"keypressed": value})
        super()._handle_coordinator_update()


class IbgEsensorGen1KeyEvent(IbgCoordinatorEntity, EventEntity):
    """Physical key actions reported by an eSensor-2000 first generation."""

    _attr_event_types = [EVENT_TYPE_PRESSED, EVENT_TYPE_DOUBLE_PRESSED]
    _attr_translation_key = "esensor_gen1_key"

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did, "key")
        self._seen_count = coordinator.data.key_event_counts.get(did, 0)

    @callback
    def _handle_coordinator_update(self) -> None:
        count = self.coordinator.data.key_event_counts.get(self.did, 0)
        if count > self._seen_count:
            self._seen_count = count
            value = self.coordinator.data.key_event_values.get(self.did)
            event_type = {
                1: EVENT_TYPE_PRESSED,
                3: EVENT_TYPE_DOUBLE_PRESSED,
            }.get(value)
            if event_type is not None:
                self._trigger_event(event_type, {"keypressed": value})
        super()._handle_coordinator_update()
