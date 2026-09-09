"""Remote platform for eHomeHA, eRemoteHA, and eHub infrared devices."""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from typing import Any

from homeassistant.components.remote import RemoteEntity, RemoteEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.storage import Store

from . import LinknLinkConfigEntry
from .const import DOMAIN
from .coordinator import EHubDataUpdateCoordinator, UltraDataUpdateCoordinator
from .entity import EHubCoordinatorEntity, UltraCoordinatorEntity

STORAGE_VERSION = 1


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LinknLinkConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create one infrared remote entity."""
    del hass
    coordinator = entry.runtime_data
    if isinstance(coordinator, EHubDataUpdateCoordinator):
        async_add_entities([EHubRemoteEntity(coordinator)])
    elif isinstance(coordinator, UltraDataUpdateCoordinator) and coordinator._is_remote:
        async_add_entities([LinknLinkRemoteEntity(coordinator)])


class _LearnedRemoteMixin:
    """Persistence and command handling shared by local remote entities."""

    async def _async_load_codes(self) -> None:
        await super().async_added_to_hass()
        self._store = Store(
            self.hass,
            STORAGE_VERSION,
            f"{DOMAIN}.remote.{self.coordinator.device.id}",
        )
        stored = await self._store.async_load()
        if isinstance(stored, dict):
            self._codes = {
                key: value for key, value in stored.items() if isinstance(key, str) and isinstance(value, str)
            }
        self.async_write_ha_state()

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"commands": sorted(self._codes)}

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    async def async_send_command(self, command: Iterable[str], **kwargs: Any) -> None:
        commands = list(command)
        if not commands:
            raise HomeAssistantError("At least one remote command is required")
        repeats = kwargs.get("num_repeats", 1)
        delay = kwargs.get("delay_secs", 0)
        if not isinstance(repeats, int) or repeats < 1:
            raise HomeAssistantError("num_repeats must be a positive integer")
        for name in commands:
            code = self._codes.get(name)
            if code is None:
                raise HomeAssistantError(f"Unknown learned remote command: {name}")
            try:
                for index in range(repeats):
                    await self._client.send_code(code)
                    if delay and index + 1 < repeats:
                        await asyncio.sleep(float(delay))
            except Exception as err:
                raise HomeAssistantError(f"Could not send remote command {name}: {err}") from err

    async def async_learn_command(self, **kwargs: Any) -> None:
        command = kwargs.get("command")
        if isinstance(command, list):
            name = command[0].strip() if len(command) == 1 and isinstance(command[0], str) else ""
        else:
            name = command.strip() if isinstance(command, str) else ""
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

    async def async_delete_command(self, **kwargs: Any) -> None:
        command = kwargs.get("command")
        if isinstance(command, list):
            names = [item.strip() for item in command if isinstance(item, str)]
        elif isinstance(command, str):
            names = [command.strip()]
        else:
            names = []
        if not names:
            raise HomeAssistantError("At least one remote command is required")
        missing = [name for name in names if name not in self._codes]
        if missing:
            raise HomeAssistantError(f"Unknown learned remote command: {missing[0]}")
        for name in names:
            self._codes.pop(name)
        if self._store is None:
            raise HomeAssistantError("Remote storage is not ready")
        await self._store.async_save(self._codes)
        self.async_write_ha_state()


class LinknLinkRemoteEntity(_LearnedRemoteMixin, UltraCoordinatorEntity, RemoteEntity):
    """A learned-command infrared remote backed by the device TCP API."""

    _attr_has_entity_name = True
    _attr_name = "Remote"
    _attr_should_poll = False
    _attr_supported_features = RemoteEntityFeature.LEARN_COMMAND | RemoteEntityFeature.DELETE_COMMAND

    def __init__(self, coordinator: UltraDataUpdateCoordinator) -> None:
        UltraCoordinatorEntity.__init__(self, coordinator, "remote")
        self._client = coordinator.get_remote_client()
        self._store: Store[dict[str, str]] | None = None
        self._codes: dict[str, str] = {}

    async def async_added_to_hass(self) -> None:
        """Load learned commands when the entity is added to Home Assistant."""
        await self._async_load_codes()


class EHubRemoteEntity(_LearnedRemoteMixin, EHubCoordinatorEntity, RemoteEntity):
    """A learned-command infrared remote backed by an eHub TCP API."""

    _attr_has_entity_name = True
    _attr_name = "Remote"
    _attr_should_poll = False
    _attr_supported_features = RemoteEntityFeature.LEARN_COMMAND | RemoteEntityFeature.DELETE_COMMAND

    def __init__(self, coordinator: EHubDataUpdateCoordinator) -> None:
        EHubCoordinatorEntity.__init__(self, coordinator, "remote")
        self._client = coordinator.get_remote_client()
        self._store: Store[dict[str, str]] | None = None
        self._codes: dict[str, str] = {}

    async def async_added_to_hass(self) -> None:
        """Load learned commands when the entity is added to Home Assistant."""
        await self._async_load_codes()
