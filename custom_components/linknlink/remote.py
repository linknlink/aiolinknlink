"""Remote platform for eHomeHA and eRemoteHA infrared devices."""

from __future__ import annotations

from typing import Any

from homeassistant.components.remote import RemoteEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.storage import Store

from . import LinknLinkConfigEntry
from .const import DOMAIN
from .coordinator import UltraDataUpdateCoordinator
from .entity import UltraCoordinatorEntity

STORAGE_VERSION = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LinknLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one infrared remote entity."""
    del hass
    coordinator = entry.runtime_data
    if not isinstance(coordinator, UltraDataUpdateCoordinator) or not coordinator._is_remote:
        return
    async_add_entities([LinknLinkRemoteEntity(coordinator)])


class LinknLinkRemoteEntity(UltraCoordinatorEntity, RemoteEntity):
    """A learned-command infrared remote backed by the device TCP API."""

    _attr_has_entity_name = True
    _attr_name = "Remote"
    _attr_should_poll = False

    def __init__(self, coordinator: UltraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "remote")
        self._client = coordinator.get_remote_client()
        self._store: Store[dict[str, str]] | None = None
        self._codes: dict[str, str] = {}

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose command names without exposing infrared payloads."""
        return {"commands": sorted(self._codes)}

    @property
    def available(self) -> bool:
        """Remote availability follows the authenticated device, not sensors."""
        return self.coordinator.last_update_success

    async def async_added_to_hass(self) -> None:
        """Load learned commands and register this entity for custom services."""
        await super().async_added_to_hass()
        self._store = Store(
            self.hass,
            STORAGE_VERSION,
            f"{DOMAIN}.remote.{self.coordinator.device.id}",
        )
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._codes = {key: value for key, value in stored.items() if isinstance(key, str) and isinstance(value, str)}
        self.hass.data.setdefault(DOMAIN, {}).setdefault("remote_entities", {})[self.entity_id] = self
        self.async_write_ha_state()

    async def async_will_remove_from_hass(self) -> None:
        """Unregister this entity from custom service lookup."""
        entities = self.hass.data.get(DOMAIN, {}).get("remote_entities", {})
        entities.pop(self.entity_id, None)
        await super().async_will_remove_from_hass()

    async def async_send_command(self, command: list[str], **kwargs: Any) -> None:
        """Transmit one or more learned infrared commands."""
        del kwargs
        commands = [command] if isinstance(command, str) else command
        if not commands:
            raise HomeAssistantError("At least one remote command is required")
        for name in commands:
            code = self._codes.get(name)
            if code is None:
                raise HomeAssistantError(f"Unknown learned remote command: {name}")
            try:
                await self._client.send_code(code)
            except Exception as err:
                raise HomeAssistantError(f"Could not send remote command {name}: {err}") from err

    async def async_learn_command(self, command: str) -> None:
        """Learn and persist one infrared command."""
        name = command.strip()
        if not name:
            raise HomeAssistantError("Remote command name must not be empty")
        try:
            code = await self._client.learn_code()
        except Exception as err:
            raise HomeAssistantError(f"Could not learn remote command {name}: {err}") from err
        self._codes[name] = code
        if self._store is None:
            raise HomeAssistantError("Remote storage is not ready")
        await self._store.async_save(self._codes)
        self.async_write_ha_state()

    async def async_delete_command(self, command: str) -> None:
        """Delete one learned command from persistent storage."""
        name = command.strip()
        if name not in self._codes:
            raise HomeAssistantError(f"Unknown learned remote command: {name}")
        self._codes.pop(name)
        if self._store is None:
            raise HomeAssistantError("Remote storage is not ready")
        await self._store.async_save(self._codes)
        self.async_write_ha_state()
