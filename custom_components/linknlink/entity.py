"""Shared LinknLink entity helpers."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import EHomeDataUpdateCoordinator, IbgDataUpdateCoordinator, UltraDataUpdateCoordinator

SUBDEVICE_MODELS = {
    "00000000000000000000000005000100": "RF environment/occupancy sensor",
    "00000000000000000000000031130100": "Seven-channel controller",
    "0000000000000000000000000b150100": "DTU",
    "00000000000000000000000093150100": "Modbus air conditioner",
    "0000000000000000000000000f160100": "Modbus multifunction sensor",
    "00000000000000000000000034150100": "Modbus water meter",
    "000000000000000000000000ed140100": "Modbus electricity meter",
    "000000000000000000000000d10f0100": "DLT645 electricity meter",
    "0000000000000000000000002b160100": "Water-cooled air-conditioner panel",
    "0000000000000000000000009b100100": "433 smart air-conditioner panel eAC1",
    "000000000000000000000000b5120100": "eSensor-2000 Gen 1",
    "00000000000000000000000043160100": "eSensor-2000 Gen 2",
    "000000000000000000000000d7140100": "Eight-channel light switch",
    "00000000000000000000000020110100": "Single-channel light switch",
    "00000000000000000000000021110100": "Two-channel light switch",
    "00000000000000000000000022110100": "Three-channel light switch",
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
        name = self._subdevice.name
        state = self.coordinator.data.states.get(self.did)
        if state is not None:
            reported_name = state.values.get("devicename")
            if isinstance(reported_name, str) and reported_name and reported_name != name:
                name = f"{name} {reported_name}"
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self.coordinator.device.id}_{self.did}")},
            name=name,
            manufacturer="LinknLink",
            model=SUBDEVICE_MODELS.get(self._subdevice.pid, f"iBG subdevice {self._subdevice.pid[-8:]}"),
            via_device=(DOMAIN, self.coordinator.device.id),
        )

    def _value(self) -> int | float | bool | str | None:
        state = self.coordinator.data.states.get(self.did)
        if state is None:
            return None
        return state.values.get(self.key)


class UltraCoordinatorEntity(CoordinatorEntity[UltraDataUpdateCoordinator]):
    """Base entity for one Ultra2 environment or radar field."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: UltraDataUpdateCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{coordinator.device.id}_{key}"

    @property
    def available(self) -> bool:
        """Report availability for optional environment and radar fields."""
        if not super().available or self.coordinator.data is None:
            return False
        if self.key in self.coordinator.data.environment.available_fields:
            return self.key in self.coordinator.data.environment.values
        if self.coordinator.data.emotion is not None and self._emotion_value() is not None:
            return True
        return self._radar_value() is not None

    @property
    def device_info(self) -> DeviceInfo:
        """Return the Ultra2 device registry information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.id)},
            name=self.coordinator.device.name,
            manufacturer="LinknLink",
            model=self.coordinator.device.model,
        )

    def _environment_value(self) -> int | float | bool | None:
        return self.coordinator.data.environment.values.get(self.key)

    def _emotion_value(self) -> int | None:
        state = self.coordinator.data.emotion
        if state is None:
            return None
        if self.key == "absence_delay":
            return state.absence_delay
        if self.key == "sensitivity":
            return state.sensitivity
        if self.key == "firmware_version":
            return state.firmware_version
        return None

    def _radar_value(self) -> int | float | None:
        radar = self.coordinator.data.radar
        if radar is None:
            return None
        if self.key == "sensitivity":
            return radar.sensitivity
        if self.key == "trigger_speed":
            return radar.trigger_speed
        if self.key == "install_mode":
            return radar.install_mode
        if self.key == "height":
            return radar.height
        if self.key == "install_direction":
            return radar.install_direction
        if self.key == "z_range_minimum":
            return radar.z_range.minimum if radar.z_range is not None else None
        if self.key == "z_range_maximum":
            return radar.z_range.maximum if radar.z_range is not None else None
        if self.key == "default_absence_delay":
            return radar.default_absence_delay
        if self.key.startswith("zone_") and self.key.endswith("_absence_delay"):
            zone_text = self.key.removeprefix("zone_").removesuffix("_absence_delay")
            if zone_text.isdigit() and 1 <= (zone := int(zone_text)) <= 4:
                return radar.zone_absence_delays[zone - 1]
        return None


class EHomeCoordinatorEntity(CoordinatorEntity[EHomeDataUpdateCoordinator]):
    """Base entity for one eHome field."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: EHomeDataUpdateCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self.key = key
        self._attr_unique_id = f"{coordinator.device.id}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return eHome device registry information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device.id)},
            name=self.coordinator.device.name,
            manufacturer="LinknLink",
            model=self.coordinator.device.model,
        )
