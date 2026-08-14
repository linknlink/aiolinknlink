"""Switch platform for LinknLink iBG controllable subdevices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from aiolinknlink import PID_BOX7_CONTROLLER

from . import LinknLinkConfigEntry
from .entity import IbgCoordinatorEntity

BOX7_SWITCHES = tuple(
    SwitchEntityDescription(
        key=f"pwr{channel}",
        name=f"Circuit {channel}",
        translation_key=f"circuit_{channel}",
    )
    for channel in range(1, 8)
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LinknLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the seven reviewed circuit switches for BOX7 controllers."""
    del hass
    coordinator = entry.runtime_data
    async_add_entities(
        IbgBox7Switch(coordinator, device.did, description)
        for device in coordinator.data.subdevices
        if device.pid == PID_BOX7_CONTROLLER
        for description in BOX7_SWITCHES
    )


class IbgBox7Switch(IbgCoordinatorEntity, SwitchEntity):
    """One controllable circuit on a seven-channel controller."""

    entity_description: SwitchEntityDescription

    def __init__(self, coordinator, did: str, description: SwitchEntityDescription) -> None:
        super().__init__(coordinator, did, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the confirmed circuit state."""
        value = self._value()
        return value if isinstance(value, bool) else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the circuit on and wait for device confirmation."""
        del kwargs
        await self.coordinator.async_set_subdevice_state(self.did, {self.key: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the circuit off and wait for device confirmation."""
        del kwargs
        await self.coordinator.async_set_subdevice_state(self.did, {self.key: False})
