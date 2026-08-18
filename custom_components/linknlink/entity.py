"""Shared LinknLink entity helpers."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import IbgDataUpdateCoordinator

SUBDEVICE_MODELS = {
    "00000000000000000000000005000100": "RF environment/occupancy sensor",
    "00000000000000000000000031130100": "Seven-channel controller",
    "0000000000000000000000000b150100": "DTU",
    "00000000000000000000000093150100": "Modbus air conditioner",
    "0000000000000000000000000f160100": "Modbus multifunction sensor",
    "00000000000000000000000034150100": "Modbus water meter",
    "0000000000000000000000002b160100": "Water-cooled air-conditioner panel",
}


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
            model=SUBDEVICE_MODELS.get(self._subdevice.pid, f"iBG subdevice {self._subdevice.pid[-8:]}"),
            via_device=(DOMAIN, self.coordinator.device.id),
        )

    def _value(self) -> int | float | bool | None:
        state = self.coordinator.data.states.get(self.did)
        if state is None:
            return None
        return state.values.get(self.key)
