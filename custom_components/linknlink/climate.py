"""Climate platform for LinknLink iBG air-conditioner devices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import ClimateEntityFeature, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from aiolinknlink import (
    EAC1_CURRENT_TEMPERATURE_FIELD,
    EAC1_FAN_FIELD,
    EAC1_MODE_FIELD,
    EAC1_POWER_FIELD,
    EAC1_TARGET_TEMPERATURE_FIELD,
    MODBUS_AC_FAN_FIELD,
    MODBUS_AC_MODE_FIELD,
    MODBUS_AC_POWER_FIELD,
    MODBUS_AC_TARGET_TEMPERATURE_FIELD,
    PID_EAC1_PANEL,
    PID_MODBUS_AC,
    PID_WATER_AC_PANEL,
    WATER_AC_PANEL_CURRENT_TEMPERATURE_FIELD,
    WATER_AC_PANEL_FAN_FIELD,
    WATER_AC_PANEL_MODE_FIELD,
    WATER_AC_PANEL_POWER_FIELD,
    WATER_AC_PANEL_TARGET_TEMPERATURE_FIELD,
)

from . import LinknLinkConfigEntry
from .coordinator import UltraDataUpdateCoordinator
from .entity import IbgCoordinatorEntity

FAN_MODE_AUTO = "auto"
FAN_MODE_LOW = "low"
FAN_MODE_MEDIUM = "medium"
FAN_MODE_HIGH = "high"

AC_MODE_TO_HVAC_MODE = {
    0: HVACMode.COOL,
    1: HVACMode.HEAT,
    2: HVACMode.DRY,
    3: HVACMode.FAN_ONLY,
    4: HVACMode.AUTO,
}
HVAC_MODE_TO_AC_MODE = {mode: value for value, mode in AC_MODE_TO_HVAC_MODE.items()}
WATER_AC_PANEL_MODE_TO_HVAC_MODE = {
    0: HVACMode.COOL,
    1: HVACMode.HEAT,
    3: HVACMode.FAN_ONLY,
}
HVAC_MODE_TO_WATER_AC_PANEL_MODE = {mode: value for value, mode in WATER_AC_PANEL_MODE_TO_HVAC_MODE.items()}
MARK_TO_FAN_MODE = {
    0: FAN_MODE_AUTO,
    1: FAN_MODE_LOW,
    2: FAN_MODE_MEDIUM,
    3: FAN_MODE_HIGH,
}
FAN_MODE_TO_MARK = {mode: value for value, mode in MARK_TO_FAN_MODE.items()}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LinknLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create reviewed air-conditioner entities."""
    del hass
    coordinator = entry.runtime_data
    if isinstance(coordinator, UltraDataUpdateCoordinator):
        return
    entities = [
        IbgModbusClimate(coordinator, device.did)
        for device in coordinator.data.subdevices
        if device.pid == PID_MODBUS_AC
    ]
    entities.extend(
        IbgWaterAcPanelClimate(coordinator, device.did)
        for device in coordinator.data.subdevices
        if device.pid == PID_WATER_AC_PANEL
    )
    entities.extend(
        IbgEac1Climate(coordinator, device.did)
        for device in coordinator.data.subdevices
        if device.pid == PID_EAC1_PANEL
    )
    async_add_entities(entities)


class IbgModbusClimate(IbgCoordinatorEntity, ClimateEntity):
    """One confirmed Modbus air conditioner connected through iBG."""

    _attr_name = "Air conditioner"
    _attr_translation_key = "air_conditioner"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 16
    _attr_max_temp = 32
    _attr_target_temperature_step = 1
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
        HVACMode.AUTO,
    ]
    _attr_fan_modes = list(MARK_TO_FAN_MODE.values())
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did, "climate")

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the confirmed power and operating mode."""
        power = self._value_for(MODBUS_AC_POWER_FIELD)
        if power is False:
            return HVACMode.OFF
        mode = self._value_for(MODBUS_AC_MODE_FIELD)
        if power is True and isinstance(mode, int) and not isinstance(mode, bool):
            return AC_MODE_TO_HVAC_MODE.get(mode)
        return None

    @property
    def fan_mode(self) -> str | None:
        """Return the confirmed fan mode."""
        value = self._value_for(MODBUS_AC_FAN_FIELD)
        if isinstance(value, int) and not isinstance(value, bool):
            return MARK_TO_FAN_MODE.get(value)
        return None

    @property
    def target_temperature(self) -> float | None:
        """Return the confirmed target temperature."""
        value = self._value_for(MODBUS_AC_TARGET_TEMPERATURE_FIELD)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    @property
    def current_temperature(self) -> float | None:
        """Return the measured room temperature."""
        value = self._value_for("envtemp")
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set power and operating mode with confirmed read-back."""
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_set_subdevice_state(self.did, {MODBUS_AC_POWER_FIELD: False})
            return
        if hvac_mode not in HVAC_MODE_TO_AC_MODE:
            raise ValueError(f"Unsupported HVAC mode: {hvac_mode}")
        await self.coordinator.async_set_subdevice_state(
            self.did,
            {
                MODBUS_AC_POWER_FIELD: True,
                MODBUS_AC_MODE_FIELD: HVAC_MODE_TO_AC_MODE[hvac_mode],
            },
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan speed with confirmed read-back."""
        if fan_mode not in FAN_MODE_TO_MARK:
            raise ValueError(f"Unsupported fan mode: {fan_mode}")
        await self.coordinator.async_set_subdevice_state(
            self.did,
            {MODBUS_AC_FAN_FIELD: FAN_MODE_TO_MARK[fan_mode]},
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature with confirmed read-back."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
            raise ValueError("A numeric target temperature is required")
        await self.coordinator.async_set_subdevice_state(
            self.did,
            {MODBUS_AC_TARGET_TEMPERATURE_FIELD: temperature},
        )

    def _value_for(self, key: str) -> int | float | bool | str | None:
        state = self.coordinator.data.states.get(self.did)
        if state is None:
            return None
        return state.values.get(key)


class IbgWaterAcPanelClimate(IbgCoordinatorEntity, ClimateEntity):
    """One confirmed RF water-cooled air-conditioner panel through iBG."""

    _attr_name = "Water-cooled air conditioner"
    _attr_translation_key = "water_ac_panel"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 5
    _attr_max_temp = 35
    _attr_target_temperature_step = 1
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.FAN_ONLY,
    ]
    _attr_fan_modes = list(MARK_TO_FAN_MODE.values())
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did, "climate")

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the confirmed power and water-panel operating mode."""
        power = self._value_for(WATER_AC_PANEL_POWER_FIELD)
        if power is False:
            return HVACMode.OFF
        mode = self._value_for(WATER_AC_PANEL_MODE_FIELD)
        if power is True and isinstance(mode, int) and not isinstance(mode, bool):
            return WATER_AC_PANEL_MODE_TO_HVAC_MODE.get(mode)
        return None

    @property
    def fan_mode(self) -> str | None:
        """Return the confirmed water-panel fan mode."""
        value = self._value_for(WATER_AC_PANEL_FAN_FIELD)
        if isinstance(value, int) and not isinstance(value, bool):
            return MARK_TO_FAN_MODE.get(value)
        return None

    @property
    def target_temperature(self) -> float | None:
        """Return the confirmed target temperature."""
        value = self._value_for(WATER_AC_PANEL_TARGET_TEMPERATURE_FIELD)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    @property
    def current_temperature(self) -> float | None:
        """Return the measured indoor temperature."""
        value = self._value_for(WATER_AC_PANEL_CURRENT_TEMPERATURE_FIELD)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set power and water-panel operating mode with confirmed read-back."""
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_set_subdevice_state(self.did, {WATER_AC_PANEL_POWER_FIELD: False})
            return
        if hvac_mode not in HVAC_MODE_TO_WATER_AC_PANEL_MODE:
            raise ValueError(f"Unsupported HVAC mode: {hvac_mode}")
        await self.coordinator.async_set_subdevice_state(
            self.did,
            {
                WATER_AC_PANEL_POWER_FIELD: True,
                WATER_AC_PANEL_MODE_FIELD: HVAC_MODE_TO_WATER_AC_PANEL_MODE[hvac_mode],
            },
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set water-panel fan speed with confirmed read-back."""
        if fan_mode not in FAN_MODE_TO_MARK:
            raise ValueError(f"Unsupported fan mode: {fan_mode}")
        await self.coordinator.async_set_subdevice_state(
            self.did,
            {WATER_AC_PANEL_FAN_FIELD: FAN_MODE_TO_MARK[fan_mode]},
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set water-panel target temperature with confirmed read-back."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
            raise ValueError("A numeric target temperature is required")
        await self.coordinator.async_set_subdevice_state(
            self.did,
            {WATER_AC_PANEL_TARGET_TEMPERATURE_FIELD: temperature},
        )

    def _value_for(self, key: str) -> int | float | bool | str | None:
        state = self.coordinator.data.states.get(self.did)
        if state is None:
            return None
        return state.values.get(key)


class IbgEac1Climate(IbgCoordinatorEntity, ClimateEntity):
    """One confirmed 433 MHz eAC1 air-conditioner panel through iBG."""

    _attr_name = "Air conditioner"
    _attr_translation_key = "eac1_air_conditioner"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 16
    _attr_max_temp = 30
    _attr_target_temperature_step = 1
    _attr_hvac_modes = [
        HVACMode.OFF,
        HVACMode.COOL,
        HVACMode.HEAT,
        HVACMode.FAN_ONLY,
    ]
    _attr_fan_modes = list(MARK_TO_FAN_MODE.values())
    _attr_supported_features = ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE

    def __init__(self, coordinator, did: str) -> None:
        super().__init__(coordinator, did, "climate")

    @property
    def hvac_mode(self) -> HVACMode | None:
        """Return the confirmed power and eAC1 operating mode."""
        power = self._value_for(EAC1_POWER_FIELD)
        if power is False:
            return HVACMode.OFF
        mode = self._value_for(EAC1_MODE_FIELD)
        if power is True and isinstance(mode, int) and not isinstance(mode, bool):
            return WATER_AC_PANEL_MODE_TO_HVAC_MODE.get(mode)
        return None

    @property
    def fan_mode(self) -> str | None:
        """Return the confirmed eAC1 fan mode."""
        value = self._value_for(EAC1_FAN_FIELD)
        if isinstance(value, int) and not isinstance(value, bool):
            return MARK_TO_FAN_MODE.get(value)
        return None

    @property
    def target_temperature(self) -> float | None:
        """Return the confirmed target temperature."""
        value = self._value_for(EAC1_TARGET_TEMPERATURE_FIELD)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    @property
    def current_temperature(self) -> float | None:
        """Return the measured indoor temperature."""
        value = self._value_for(EAC1_CURRENT_TEMPERATURE_FIELD)
        return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set power and operating mode with confirmed read-back."""
        if hvac_mode == HVACMode.OFF:
            await self.coordinator.async_set_subdevice_state(self.did, {EAC1_POWER_FIELD: False})
            return
        if hvac_mode not in HVAC_MODE_TO_WATER_AC_PANEL_MODE:
            raise ValueError(f"Unsupported HVAC mode: {hvac_mode}")
        await self.coordinator.async_set_subdevice_state(
            self.did,
            {
                EAC1_POWER_FIELD: True,
                EAC1_MODE_FIELD: HVAC_MODE_TO_WATER_AC_PANEL_MODE[hvac_mode],
            },
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        """Set fan speed with confirmed read-back."""
        if fan_mode not in FAN_MODE_TO_MARK:
            raise ValueError(f"Unsupported fan mode: {fan_mode}")
        await self.coordinator.async_set_subdevice_state(
            self.did,
            {EAC1_FAN_FIELD: FAN_MODE_TO_MARK[fan_mode]},
        )

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set target temperature with confirmed read-back."""
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if not isinstance(temperature, (int, float)) or isinstance(temperature, bool):
            raise ValueError("A numeric target temperature is required")
        await self.coordinator.async_set_subdevice_state(
            self.did,
            {EAC1_TARGET_TEMPERATURE_FIELD: temperature},
        )

    def _value_for(self, key: str) -> int | float | bool | str | None:
        state = self.coordinator.data.states.get(self.did)
        if state is None:
            return None
        return state.values.get(key)
