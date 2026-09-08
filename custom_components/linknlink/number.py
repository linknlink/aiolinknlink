"""Number platform for LinknLink iBG writable numeric outputs."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import UnitOfElectricPotential, UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from aiolinknlink import DTU_VOLTAGE_OUTPUT_FIELD, PID_DTU

from . import LinknLinkConfigEntry
from .coordinator import UltraDataUpdateCoordinator
from .entity import IbgCoordinatorEntity, UltraCoordinatorEntity


@dataclass(frozen=True, kw_only=True)
class UltraRadarNumberEntityDescription(NumberEntityDescription):
    """Describe one writable Ultra2 radar numeric field."""

    integer: bool = False


ULTRA_RADAR_NUMBERS = (
    UltraRadarNumberEntityDescription(
        key="sensitivity",
        name="Sensitivity level",
        translation_key="radar_sensitivity",
        native_min_value=0,
        native_max_value=2,
        native_step=1,
        integer=True,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:radar",
    ),
    UltraRadarNumberEntityDescription(
        key="trigger_speed",
        name="Trigger speed level",
        translation_key="radar_trigger_speed",
        native_min_value=0,
        native_max_value=2,
        native_step=1,
        integer=True,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:speedometer",
    ),
    UltraRadarNumberEntityDescription(
        key="install_mode",
        name="Installation mode",
        translation_key="radar_install_mode",
        native_min_value=0,
        native_max_value=1,
        native_step=1,
        integer=True,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:wall-sconce-flat",
    ),
    UltraRadarNumberEntityDescription(
        key="height",
        name="Installation height",
        translation_key="radar_height",
        native_min_value=0,
        native_max_value=65535,
        native_step=1,
        native_unit_of_measurement=UnitOfLength.CENTIMETERS,
        integer=True,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:arrow-expand-vertical",
    ),
    UltraRadarNumberEntityDescription(
        key="install_direction",
        name="Installation direction",
        translation_key="radar_install_direction",
        native_min_value=0,
        native_max_value=1,
        native_step=1,
        integer=True,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:rotate-3d-variant",
    ),
    UltraRadarNumberEntityDescription(
        key="z_range_minimum",
        name="Minimum Z-axis range",
        translation_key="radar_z_range_minimum",
        native_min_value=-6,
        native_max_value=6,
        native_step=0.1,
        native_unit_of_measurement=UnitOfLength.METERS,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:arrow-collapse-down",
    ),
    UltraRadarNumberEntityDescription(
        key="z_range_maximum",
        name="Maximum Z-axis range",
        translation_key="radar_z_range_maximum",
        native_min_value=-6,
        native_max_value=6,
        native_step=0.1,
        native_unit_of_measurement=UnitOfLength.METERS,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:arrow-collapse-up",
    ),
    UltraRadarNumberEntityDescription(
        key="default_absence_delay",
        name="Default absence delay",
        translation_key="radar_default_absence_delay",
        native_min_value=0,
        native_max_value=64800,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        integer=True,
        mode=NumberMode.BOX,
        entity_category=EntityCategory.CONFIG,
        icon="mdi:timer-outline",
    ),
    *(
        UltraRadarNumberEntityDescription(
            key=f"zone_{zone}_absence_delay",
            name=f"Zone {zone} absence delay",
            translation_key=f"radar_zone_{zone}_absence_delay",
            native_min_value=0,
            native_max_value=64800,
            native_step=1,
            native_unit_of_measurement=UnitOfTime.SECONDS,
            integer=True,
            mode=NumberMode.BOX,
            entity_category=EntityCategory.CONFIG,
            icon="mdi:timer-marker-outline",
        )
        for zone in range(1, 5)
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LinknLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the reviewed DTU voltage output controls."""
    del hass
    coordinator = entry.runtime_data
    if isinstance(coordinator, UltraDataUpdateCoordinator):
        async_add_entities(
            UltraRadarNumber(coordinator, description) for description in ULTRA_RADAR_NUMBERS
        )
        return
    async_add_entities(
        IbgDtuVoltageOutput(coordinator, device.did) for device in coordinator.data.subdevices if device.pid == PID_DTU
    )


class IbgDtuVoltageOutput(IbgCoordinatorEntity, NumberEntity):
    """DTU 0-10 V output with confirmed read-back."""

    _attr_name = "Voltage output"
    _attr_translation_key = "voltage_output"
    _attr_native_min_value = 0.0
    _attr_native_max_value = 10.0
    _attr_native_step = 0.1
    _attr_native_unit_of_measurement = UnitOfElectricPotential.VOLT

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did, DTU_VOLTAGE_OUTPUT_FIELD)

    @property
    def native_value(self) -> float | None:
        """Return the confirmed output voltage."""
        value = self._value()
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    async def async_set_native_value(self, value: float) -> None:
        """Set the output voltage and wait for device confirmation."""
        await self.coordinator.async_set_subdevice_state(self.did, {self.key: value})


class UltraRadarNumber(UltraCoordinatorEntity, NumberEntity):
    """One device-confirmed Ultra2 radar numeric control."""

    entity_description: UltraRadarNumberEntityDescription

    def __init__(
        self,
        coordinator: UltraDataUpdateCoordinator,
        description: UltraRadarNumberEntityDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        """Return the device-read radar configuration value."""
        value = self._radar_value()
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    async def async_set_native_value(self, value: float) -> None:
        """Set one radar field and require its independent device read-back."""
        if self.entity_description.integer:
            if not float(value).is_integer():
                raise ValueError(f"{self.key} requires a whole number")
            integer = int(value)
            if self.key == "sensitivity":
                await self.coordinator.async_set_radar_sensitivity(integer)
            elif self.key == "trigger_speed":
                await self.coordinator.async_set_radar_trigger_speed(integer)
            elif self.key == "install_mode":
                await self.coordinator.async_set_radar_install_mode(integer)
            elif self.key == "height":
                await self.coordinator.async_set_radar_height(integer)
            elif self.key == "install_direction":
                await self.coordinator.async_set_radar_install_direction(integer)
            elif self.key == "default_absence_delay":
                await self.coordinator.async_set_radar_default_absence_delay(integer)
            elif self.key.startswith("zone_") and self.key.endswith("_absence_delay"):
                zone = int(self.key.removeprefix("zone_").removesuffix("_absence_delay"))
                await self.coordinator.async_set_radar_zone_absence_delay(zone, integer)
            else:
                raise ValueError(f"Unsupported Ultra2 radar field: {self.key}")
            return
        z_range = self.coordinator.data.radar.z_range
        if z_range is None:
            raise ValueError("Ultra2 did not report a Z-axis range")
        if self.key == "z_range_minimum":
            await self.coordinator.async_set_radar_z_range(float(value), z_range.maximum)
        elif self.key == "z_range_maximum":
            await self.coordinator.async_set_radar_z_range(z_range.minimum, float(value))
        else:
            raise ValueError(f"Unsupported Ultra2 radar field: {self.key}")
