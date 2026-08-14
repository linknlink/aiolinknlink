"""Event platform for LinknLink iBG physical keys."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import LinknLinkConfigEntry
from .entity import IbgCoordinatorEntity

EVENT_TYPE_PRESSED = "pressed"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LinknLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a physical key event entity for each supported iBG sensor."""
    del hass
    coordinator = entry.runtime_data
    async_add_entities(IbgKeyEvent(coordinator, did) for did in coordinator.data.states)


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
