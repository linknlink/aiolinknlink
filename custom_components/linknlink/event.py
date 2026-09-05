"""Event platform for LinknLink iBG physical keys."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from aiolinknlink import (
    PID_ESENSOR_2000_GEN1,
    PID_ESENSOR_2000_GEN2,
    PID_SINGLE_CHANNEL_LIGHT_SWITCH,
    PID_SR3_SENSOR,
    PID_THREE_CHANNEL_LIGHT_SWITCH,
    PID_TWO_CHANNEL_LIGHT_SWITCH,
)

from . import LinknLinkConfigEntry
from .coordinator import EHomeDataUpdateCoordinator, UltraDataUpdateCoordinator
from .entity import EHomeCoordinatorEntity, IbgCoordinatorEntity

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
    if isinstance(coordinator, EHomeDataUpdateCoordinator):
        async_add_entities([EHomeKeyEvent(coordinator)])
        return
    if isinstance(coordinator, UltraDataUpdateCoordinator):
        return
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
    scene_counts = {
        PID_SINGLE_CHANNEL_LIGHT_SWITCH: 2,
        PID_TWO_CHANNEL_LIGHT_SWITCH: 4,
        PID_THREE_CHANNEL_LIGHT_SWITCH: 6,
    }
    async_add_entities(
        IbgSceneKeyEvent(
            coordinator,
            device.did,
            f"scenarioswitch_{channel}",
            f"Scene button {channel}",
        )
        for device in coordinator.data.subdevices
        for channel in range(1, scene_counts.get(device.pid, 0) + 1)
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


class EHomeKeyEvent(EHomeCoordinatorEntity, EventEntity):
    """Physical SR3 key press event reported by an eHome gateway."""

    _attr_event_types = [EVENT_TYPE_PRESSED]
    _attr_translation_key = "key"

    def __init__(self, coordinator: EHomeDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "sr3_key")
        self._seen_pressed = coordinator.data.sr3_keypressed == 1

    @callback
    def _handle_coordinator_update(self) -> None:
        pressed = self.coordinator.data.sr3_keypressed == 1
        if pressed and not self._seen_pressed:
            self._trigger_event(EVENT_TYPE_PRESSED, {"keypressed": 1})
        self._seen_pressed = pressed
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


class IbgSceneKeyEvent(IbgCoordinatorEntity, EventEntity):
    """Momentary scene key events reported by a light switch."""

    _attr_event_types = [EVENT_TYPE_PRESSED]

    def __init__(self, coordinator, did: str, scene_key: str, name: str) -> None:
        super().__init__(coordinator, did, scene_key)
        self._scene_key = scene_key
        self._attr_translation_key = f"scene_key_{scene_key.rsplit('_', 1)[-1]}"
        self._attr_name = name
        self._seen_count = coordinator.data.scene_event_counts.get((did, scene_key), 0)

    @callback
    def _handle_coordinator_update(self) -> None:
        event_key = (self.did, self._scene_key)
        count = self.coordinator.data.scene_event_counts.get(event_key, 0)
        if count > self._seen_count:
            self._seen_count = count
            self._trigger_event(
                EVENT_TYPE_PRESSED,
                {self._scene_key: self.coordinator.data.scene_event_values.get(event_key, 1)},
            )
        super()._handle_coordinator_update()
