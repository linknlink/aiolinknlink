"""Binary sensor platform for LinknLink iBG subdevices."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from aiolinknlink import (
    PID_DLT645_ELECTRICITY_METER,
    PID_DTU,
    PID_EMOTION,
    PID_ESENSOR_2000_GEN1,
    PID_ESENSOR_2000_GEN2,
    PID_MODBUS_ELECTRICITY_METER,
    PID_SR3_SENSOR,
    TYPE_EMOTION,
    TYPE_EMOTION_WIRE,
    TYPE_ULTRA,
)

from . import LinknLinkConfigEntry
from .coordinator import EHomeDataUpdateCoordinator, UltraDataUpdateCoordinator
from .entity import EHomeCoordinatorEntity, IbgCoordinatorEntity, UltraCoordinatorEntity

SR3_BINARY_SENSORS = (
    BinarySensorEntityDescription(
        key="occupancy",
        name="Occupancy",
        translation_key="occupancy",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
    ),
)
ULTRA_BINARY_SENSORS = (
    BinarySensorEntityDescription(
        key="occupancy",
        name="Occupancy",
        translation_key="occupancy",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
    ),
    *(
        BinarySensorEntityDescription(
            key=f"zone_{zone}_presence",
            name=f"Zone {zone} presence",
            translation_key=f"zone_{zone}_presence",
            device_class=BinarySensorDeviceClass.OCCUPANCY,
        )
        for zone in range(1, 5)
    ),
)
ULTRA_POSITION_SUBSCRIPTION = BinarySensorEntityDescription(
    key="position_subscription",
    name="Position subscription",
    translation_key="position_subscription",
    device_class=BinarySensorDeviceClass.CONNECTIVITY,
    entity_category=EntityCategory.DIAGNOSTIC,
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
DLT645_ELECTRICITY_METER_BINARY_SENSORS = tuple(
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
    PID_ESENSOR_2000_GEN1: SR3_BINARY_SENSORS,
    PID_ESENSOR_2000_GEN2: SR3_BINARY_SENSORS,
    PID_DTU: DTU_BINARY_SENSORS,
    PID_MODBUS_ELECTRICITY_METER: MODBUS_ELECTRICITY_METER_BINARY_SENSORS,
    PID_DLT645_ELECTRICITY_METER: DLT645_ELECTRICITY_METER_BINARY_SENSORS,
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LinknLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create reviewed binary sensors for supported iBG subdevices."""
    del hass
    coordinator = entry.runtime_data
    if isinstance(coordinator, EHomeDataUpdateCoordinator):
        async_add_entities([EHomePresenceSensor(coordinator)])
        return
    if isinstance(coordinator, UltraDataUpdateCoordinator):
        if coordinator._is_emotion_pro:
            async_add_entities([UltraBinarySensor(coordinator, ULTRA_BINARY_SENSORS[0])])
            return
        is_emotion = coordinator.device.pid.lower() == PID_EMOTION or coordinator.device.type_id in {
            TYPE_EMOTION,
            TYPE_EMOTION_WIRE,
        }
        descriptions = ULTRA_BINARY_SENSORS[:1] if is_emotion else ULTRA_BINARY_SENSORS
        entities = [UltraBinarySensor(coordinator, description) for description in descriptions]
        if coordinator.device.type_id != TYPE_ULTRA and not is_emotion:
            entities.append(UltraPositionSubscriptionBinarySensor(coordinator))
        async_add_entities(entities)
        return
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


class EHomePresenceSensor(EHomeCoordinatorEntity, BinarySensorEntity):
    """SR3 PIR occupancy state."""

    _attr_name = "SR3 occupancy"
    _attr_translation_key = "occupancy"
    _attr_device_class = BinarySensorDeviceClass.OCCUPANCY

    def __init__(self, coordinator: EHomeDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "sr3_occupied")

    @property
    def is_on(self) -> bool | None:
        """Return whether eHome reports a person."""
        value = self.coordinator.data.sr3_occupied
        return bool(value) if value is not None else None


class UltraBinarySensor(UltraCoordinatorEntity, BinarySensorEntity):
    """One validated Ultra2 presence state."""

    entity_description: BinarySensorEntityDescription

    def __init__(
        self,
        coordinator: UltraDataUpdateCoordinator,
        description: BinarySensorEntityDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return the latest validated presence state."""
        value = self._environment_value()
        return value if isinstance(value, bool) else None


class UltraPositionSubscriptionBinarySensor(UltraCoordinatorEntity, BinarySensorEntity):
    """Report whether the Ultra2 confirmed its local position subscription."""

    entity_description = ULTRA_POSITION_SUBSCRIPTION

    def __init__(self, coordinator: UltraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, self.entity_description.key)

    @property
    def available(self) -> bool:
        """Report availability once the subscription object has published state."""
        return self.coordinator.last_update_success and self.coordinator.data.position is not None

    @property
    def is_on(self) -> bool | None:
        """Return the device-confirmed subscription state."""
        position = self.coordinator.data.position
        return position.subscribed if position is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, int | bool | str | None] | None:
        """Expose safe subscription diagnostics."""
        position = self.coordinator.data.position
        if position is None:
            return None
        return {
            "position_stale": position.stale,
            "confirmation_count": position.confirmation_count,
            "last_subscribed_at": (
                position.last_subscribed_at.isoformat() if position.last_subscribed_at is not None else None
            ),
            "last_error": position.last_error,
        }
