"""Switch platform for LinknLink iBG controllable subdevices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from aiolinknlink import EAC1_KEY_LOCK_FIELD, PID_BOX7_CONTROLLER, PID_DTU, PID_EAC1_PANEL

from . import LinknLinkConfigEntry
from .entity import IbgCoordinatorEntity

BOX7_SWITCHES = tuple(
    SwitchEntityDescription(
        key=f"pwr{channel}",
        name=f"Switch {channel}",
        translation_key=f"switch_{channel}",
    )
    for channel in range(1, 8)
)
DTU_SWITCHES = BOX7_SWITCHES[:2]
EAC1_SWITCHES = (
    SwitchEntityDescription(
        key=EAC1_KEY_LOCK_FIELD,
        name="Key lock",
        translation_key="key_lock",
        icon="mdi:lock",
    ),
)
SWITCHES_BY_PID = {
    PID_BOX7_CONTROLLER: BOX7_SWITCHES,
    PID_DTU: DTU_SWITCHES,
    PID_EAC1_PANEL: EAC1_SWITCHES,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LinknLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create reviewed power switches for supported iBG subdevices."""
    del hass
    coordinator = entry.runtime_data
    async_add_entities(
        IbgPowerSwitch(coordinator, device.did, description)
        for device in coordinator.data.subdevices
        for description in SWITCHES_BY_PID.get(device.pid, ())
    )


class IbgPowerSwitch(IbgCoordinatorEntity, SwitchEntity):
    """One confirmed boolean control on an iBG subdevice."""

    entity_description: SwitchEntityDescription

    def __init__(self, coordinator, did: str, description: SwitchEntityDescription) -> None:
        super().__init__(coordinator, did, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the confirmed output state."""
        value = self._value()
        return value if isinstance(value, bool) else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the output on and wait for device confirmation."""
        del kwargs
        await self.coordinator.async_set_subdevice_state(self.did, {self.key: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the output off and wait for device confirmation."""
        del kwargs
        await self.coordinator.async_set_subdevice_state(self.did, {self.key: False})
