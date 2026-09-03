"""LinknLink Home Assistant integration."""

from __future__ import annotations

import sys
from contextlib import suppress
from pathlib import Path
from typing import TypeAlias

# Development deployments keep the library checkout in HA's persistent
# configuration volume, so container replacement does not remove it.
_LIBRARY_SOURCE = Path(__file__).parents[2] / "deps" / "aiolinknlink" / "src"
if _LIBRARY_SOURCE.is_dir():
    with suppress(ValueError):
        sys.path.remove(str(_LIBRARY_SOURCE))
    sys.path.insert(0, str(_LIBRARY_SOURCE))

from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import ConfigEntryNotReady  # noqa: E402
from homeassistant.helpers import device_registry as dr  # noqa: E402

from aiolinknlink import IbgClient, IbgError, UltraClient, UltraError  # noqa: E402
from aiolinknlink import PID_ULTRA, TYPE_ULTRA  # noqa: E402

from .const import (  # noqa: E402
    CONF_DEVICE_TYPE,
    CONF_LOCAL_KEY,
    DEVICE_TYPE_IBG,
    DEVICE_TYPE_ULTRA,
    DEVICE_TYPE_ULTRA2,
    PLATFORMS,
    resolve_local_key_hex,
)
from .coordinator import IbgDataUpdateCoordinator, UltraDataUpdateCoordinator  # noqa: E402

LinknLinkConfigEntry: TypeAlias = ConfigEntry[IbgDataUpdateCoordinator | UltraDataUpdateCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: LinknLinkConfigEntry) -> bool:
    """Set up LinknLink from a config entry."""
    device_type = entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_IBG)
    if device_type in {DEVICE_TYPE_ULTRA, DEVICE_TYPE_ULTRA2}:
        return await _async_setup_ultra_entry(hass, entry)
    return await _async_setup_ibg_entry(hass, entry)


async def _async_setup_ibg_entry(hass: HomeAssistant, entry: LinknLinkConfigEntry) -> bool:
    """Set up one iBG gateway, including legacy entries without a type marker."""
    client = IbgClient()
    local_key_hex = entry.data.get(CONF_LOCAL_KEY, "")
    local_key = bytes.fromhex(local_key_hex) if local_key_hex else None
    try:
        device = await client.discover_host(entry.data[CONF_HOST])
        session = await client.connect(device, local_key=local_key)
    except IbgError as err:
        raise ConfigEntryNotReady(f"Could not connect to iBG gateway: {err}") from err
    if local_key is None and session.session_key is not None:
        stored_local_key_hex = resolve_local_key_hex(local_key_hex, session.session_key)
        local_key = bytes.fromhex(stored_local_key_hex)
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_LOCAL_KEY: stored_local_key_hex},
        )
    coordinator = IbgDataUpdateCoordinator(hass, client, device, session, local_key=local_key)
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_start_push()
    entry.runtime_data = coordinator
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("linknlink", device.id)},
        name=device.name,
        manufacturer="LinknLink",
        model=device.model,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_setup_ultra_entry(hass: HomeAssistant, entry: LinknLinkConfigEntry) -> bool:
    """Set up one eMotion Ultra or Ultra2."""
    client = UltraClient()
    local_key_hex = entry.data.get(CONF_LOCAL_KEY, "")
    local_key = bytes.fromhex(local_key_hex) if local_key_hex else None
    try:
        device = await client.discover_host(entry.data[CONF_HOST])
        session = await client.connect(device, session_key=local_key)
    except UltraError as err:
        raise ConfigEntryNotReady(f"Could not connect to eMotion Ultra: {err}") from err
    coordinator = UltraDataUpdateCoordinator(hass, client, device, session, local_key=local_key)
    await coordinator.async_config_entry_first_refresh()
    if device.type_id != TYPE_ULTRA and device.pid.lower() != PID_ULTRA:
        await coordinator.async_start_position()
    entry.runtime_data = coordinator
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("linknlink", device.id)},
        name=device.name,
        manufacturer="LinknLink",
        model=device.model,
    )
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: LinknLinkConfigEntry) -> bool:
    """Unload a LinknLink config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded
