"""LinknLink Home Assistant integration."""

from __future__ import annotations

import sys
from contextlib import suppress
from pathlib import Path
from typing import TypeAlias

# Development deployments keep the library checkout in HA's persistent
# configuration volume, so container replacement does not remove it.
_DEVELOPMENT_LIBRARY_SOURCE = Path(__file__).parents[2] / "deps" / "aiolinknlink" / "src"
_BUNDLED_LIBRARY_SOURCE = Path(__file__).parent / "aiolinknlink"

# Internal deployments may provide a newer checkout in /config/deps. HACS
# installs use the bundled client library next to this integration instead.
# Insert the bundled path first, then the development path so the latter wins
# when both are present.
for _library_source in (_BUNDLED_LIBRARY_SOURCE, _DEVELOPMENT_LIBRARY_SOURCE):
    if _library_source.is_dir():
        with suppress(ValueError):
            sys.path.remove(str(_library_source))
        sys.path.insert(0, str(_library_source))

from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import ConfigEntryNotReady  # noqa: E402
from homeassistant.helpers import device_registry as dr  # noqa: E402

from aiolinknlink import (  # noqa: E402  # noqa: E402
    PID_EMOTION,
    PID_ULTRA,
    TYPE_EMOTION,
    TYPE_EMOTION_WIRE,
    TYPE_ULTRA,
    EHomeClient,
    EHomeError,
    IbgClient,
    IbgError,
    UltraClient,
    UltraError,
)

from .const import (  # noqa: E402
    CONF_DEVICE_TYPE,
    CONF_LOCAL_KEY,
    DEVICE_TYPE_EHOME,
    DEVICE_TYPE_EMOTION,
    DEVICE_TYPE_IBG,
    DEVICE_TYPE_ULTRA,
    DEVICE_TYPE_ULTRA2,
    PLATFORMS,
    resolve_local_key_hex,
)
from .coordinator import EHomeDataUpdateCoordinator, IbgDataUpdateCoordinator, UltraDataUpdateCoordinator  # noqa: E402

LinknLinkConfigEntry: TypeAlias = ConfigEntry[
    IbgDataUpdateCoordinator | UltraDataUpdateCoordinator | EHomeDataUpdateCoordinator
]


async def async_setup_entry(hass: HomeAssistant, entry: LinknLinkConfigEntry) -> bool:
    """Set up LinknLink from a config entry."""
    device_type = entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_IBG)
    if device_type in {DEVICE_TYPE_EMOTION, DEVICE_TYPE_ULTRA, DEVICE_TYPE_ULTRA2}:
        return await _async_setup_ultra_entry(hass, entry)
    if device_type == DEVICE_TYPE_EHOME:
        return await _async_setup_ehome_entry(hass, entry)
    return await _async_setup_ibg_entry(hass, entry)


async def _async_setup_ehome_entry(hass: HomeAssistant, entry: LinknLinkConfigEntry) -> bool:
    """Set up one eHome/EHUB Modbus TCP device."""
    client = EHomeClient()
    try:
        device = await client.discover_host(entry.data[CONF_HOST])
        session = await client.connect(device)
    except EHomeError as err:
        raise ConfigEntryNotReady(f"Could not connect to eHome: {err}") from err
    coordinator = EHomeDataUpdateCoordinator(hass, client, device, session)
    await coordinator.async_config_entry_first_refresh()
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
    if local_key is None and session.session_key is not None:
        stored_local_key_hex = resolve_local_key_hex(local_key_hex, session.session_key)
        local_key = bytes.fromhex(stored_local_key_hex)
        hass.config_entries.async_update_entry(
            entry,
            data={
                **entry.data,
                CONF_DEVICE_TYPE: (
                    DEVICE_TYPE_EMOTION
                    if device.type_id in {TYPE_EMOTION, TYPE_EMOTION_WIRE} or device.pid.lower() == PID_EMOTION
                    else entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_ULTRA2)
                ),
                CONF_LOCAL_KEY: stored_local_key_hex,
            },
        )
    coordinator = UltraDataUpdateCoordinator(hass, client, device, session, local_key=local_key)
    await coordinator.async_config_entry_first_refresh()
    if device.type_id not in {TYPE_ULTRA, TYPE_EMOTION, TYPE_EMOTION_WIRE} and device.pid.lower() not in {
        PID_ULTRA,
        PID_EMOTION,
    }:
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
