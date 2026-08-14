"""Number platform for LinknLink iBG writable numeric outputs."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity
from homeassistant.const import UnitOfElectricPotential
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from aiolinknlink import DTU_VOLTAGE_OUTPUT_FIELD, PID_DTU

from . import LinknLinkConfigEntry
from .entity import IbgCoordinatorEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LinknLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create the reviewed DTU voltage output controls."""
    del hass
    coordinator = entry.runtime_data
    async_add_entities(
        IbgDtuVoltageOutput(coordinator, device.did)
        for device in coordinator.data.subdevices
        if device.pid == PID_DTU
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
