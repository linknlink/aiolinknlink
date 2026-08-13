"""Shared LinknLink entity helpers."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IbgDataUpdateCoordinator


class IbgCoordinatorEntity(CoordinatorEntity[IbgDataUpdateCoordinator]):
    """Base entity for one iBG subdevice field."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: IbgDataUpdateCoordinator, did: str, key: str) -> None:
        super().__init__(coordinator)
        self.did = did
        self.key = key
        self._subdevice = next(device for device in coordinator.data.subdevices if device.did == did)
        self._attr_unique_id = f"{coordinator.device.id}_{did}_{key}"

    @property
    def available(self) -> bool:
        """Report availability from the gateway and per-sensor refresh."""
        return super().available and self.coordinator.data.states.get(self.did) is not None

    @property
    def device_info(self) -> DeviceInfo:
        """Return subdevice registry information."""
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.coordinator.device.id}_{self.did}")},
            name=self._subdevice.name,
            manufacturer="LinknLink",
            model=f"iBG subdevice {self._subdevice.pid[-8:]}",
            via_device=(DOMAIN, self.coordinator.device.id),
        )

    def _value(self) -> int | float | bool | None:
        state = self.coordinator.data.states.get(self.did)
        if state is None:
            return None
        return state.values.get(self.key)
