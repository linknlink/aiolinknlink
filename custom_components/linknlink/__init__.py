"""LinknLink Home Assistant integration."""

# ruff: noqa: E402

from __future__ import annotations

import os
import re
import sys
from contextlib import suppress
from pathlib import Path
from typing import TypeAlias

# Development deployments keep the library checkout in HA's persistent
# configuration volume, so container replacement does not remove it.
_DEVELOPMENT_LIBRARY_SOURCE = Path(__file__).parents[2] / "deps" / "aiolinknlink" / "src"
_BUNDLED_LIBRARY_ROOT = Path(__file__).parent

# Internal development deployments may provide a checkout in /config/deps.
# HACS installs must use the bundled client library next to this integration;
# otherwise a stale development checkout can override a newer HACS release.
# Insert the development path first and the bundled path last so the bundled
# release wins whenever both are present.
for _library_source in (_DEVELOPMENT_LIBRARY_SOURCE, _BUNDLED_LIBRARY_ROOT):
    if _library_source.is_dir():
        with suppress(ValueError):
            sys.path.remove(str(_library_source))
        sys.path.insert(0, str(_library_source))

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr

from aiolinknlink import (
    PID_EHOME_HA,
    PID_EMOTION,
    PID_EREMOTE_HA,
    TYPE_EHOME_HA,
    TYPE_EMOTION,
    TYPE_EMOTION_WIRE,
    TYPE_EREMOTE_HA,
    DeviceCapability,
    EHomeClient,
    EHomeError,
    EHubClient,
    EHubError,
    EthsClient,
    EthsError,
    IbgClient,
    IbgError,
    UltraClient,
    UltraError,
)

from .const import (
    CONF_DEVICE_TYPE,
    CONF_LOCAL_KEY,
    CONFIGURATION_YAML,
    DEVICE_TYPE_EHOME,
    DEVICE_TYPE_EHUB,
    DEVICE_TYPE_EMOTION,
    DEVICE_TYPE_ETHS,
    DEVICE_TYPE_IBG,
    DEVICE_TYPE_REMOTE,
    DEVICE_TYPE_ULTRA,
    DEVICE_TYPE_ULTRA2,
    DEVICE_TYPE_ZHA_QUIRK,
    PLATFORMS,
    QUIRKS_DIR,
    resolve_local_key_hex,
)
from .coordinator import (
    EHomeDataUpdateCoordinator,
    EHubDataUpdateCoordinator,
    EthsDataUpdateCoordinator,
    IbgDataUpdateCoordinator,
    UltraDataUpdateCoordinator,
)

LinknLinkConfigEntry: TypeAlias = ConfigEntry[
    IbgDataUpdateCoordinator
    | UltraDataUpdateCoordinator
    | EHomeDataUpdateCoordinator
    | EthsDataUpdateCoordinator
    | EHubDataUpdateCoordinator
]


async def async_setup_entry(hass: HomeAssistant, entry: LinknLinkConfigEntry) -> bool:
    """Set up LinknLink from a config entry."""
    device_type = entry.data.get(CONF_DEVICE_TYPE, DEVICE_TYPE_IBG)
    if device_type == DEVICE_TYPE_ZHA_QUIRK:
        await hass.async_add_executor_job(_ensure_zha_quirk_config)
        return True
    if device_type == DEVICE_TYPE_REMOTE:
        return await _async_setup_remote_entry(hass, entry)
    if device_type == DEVICE_TYPE_ETHS:
        return await _async_setup_eths_entry(hass, entry)
    if device_type in {DEVICE_TYPE_EMOTION, DEVICE_TYPE_ULTRA, DEVICE_TYPE_ULTRA2}:
        return await _async_setup_ultra_entry(hass, entry)
    if device_type == DEVICE_TYPE_EHOME:
        return await _async_setup_ehome_entry(hass, entry)
    if device_type == DEVICE_TYPE_EHUB:
        return await _async_setup_ehub_entry(hass, entry)
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


async def _async_setup_ehub_entry(hass: HomeAssistant, entry: LinknLinkConfigEntry) -> bool:
    """Set up one eHub Modbus TCP gateway."""
    client = EHubClient()
    try:
        device = await client.discover_host(entry.data[CONF_HOST])
        session = await client.connect(device)
    except EHubError as err:
        raise ConfigEntryNotReady(f"Could not connect to eHub: {err}") from err
    coordinator = EHubDataUpdateCoordinator(hass, client, device, session)
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


async def _async_setup_eths_entry(hass: HomeAssistant, entry: LinknLinkConfigEntry) -> bool:
    """Set up one eTHS temperature/humidity sensor."""
    client = EthsClient()
    try:
        device = await client.discover_host(entry.data[CONF_HOST])
        session = await client.connect(device)
    except EthsError as err:
        raise ConfigEntryNotReady(f"Could not connect to eTHS: {err}") from err
    coordinator = EthsDataUpdateCoordinator(hass, client, device, session)
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
    if DeviceCapability.LOCAL_UDP in device.capabilities:
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


async def _async_setup_remote_entry(hass: HomeAssistant, entry: LinknLinkConfigEntry) -> bool:
    """Set up an eHomeHA/eRemoteHA infrared device."""
    client = UltraClient()
    local_key_hex = entry.data.get(CONF_LOCAL_KEY, "")
    local_key = bytes.fromhex(local_key_hex) if local_key_hex else None
    try:
        device = await client.discover_host(entry.data[CONF_HOST])
        session = await client.connect(device, session_key=local_key)
    except UltraError as err:
        raise ConfigEntryNotReady(f"Could not connect to infrared remote: {err}") from err
    if device.type_id not in {TYPE_EHOME_HA, TYPE_EREMOTE_HA} and device.pid.lower() not in {
        PID_EHOME_HA,
        PID_EREMOTE_HA,
    }:
        raise ConfigEntryNotReady("Configured remote is not an eHomeHA/eRemoteHA device")
    if local_key is None and session.session_key is not None:
        stored_local_key_hex = resolve_local_key_hex(local_key_hex, session.session_key)
        hass.config_entries.async_update_entry(
            entry,
            data={**entry.data, CONF_LOCAL_KEY: stored_local_key_hex},
        )
    coordinator = UltraDataUpdateCoordinator(hass, client, device, session, local_key=local_key)
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


async def async_unload_entry(hass: HomeAssistant, entry: LinknLinkConfigEntry) -> bool:
    """Unload a LinknLink config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_shutdown()
    return unloaded


def _ensure_zha_quirk_config() -> None:

    if not os.path.exists(CONFIGURATION_YAML):
        return
    with open(CONFIGURATION_YAML, encoding="utf-8") as h:
        content = h.read()
    if "custom_components/linknlink/zha_quirks" in content:
        return
    ZHA_BLOCK = f"""
# === eMotion Air ZHA Quirk - Auto Generated ===
zha:
  enable_quirks: true
  custom_quirks_path: {QUIRKS_DIR}
# === End eMotion Air ZHA Quirk ===
"""
    if "zha:" in content:
        if "custom_quirks_path:" not in content:
            new_content = re.sub(
                r"(^zha:.*$)",
                "\\1  enable_quirks: true\n  custom_quirks_path: " + QUIRKS_DIR + "\n",
                content,
                flags=re.MULTILINE,
            )
            if new_content != content:
                with open(CONFIGURATION_YAML, "w", encoding="utf-8") as h:
                    h.write(new_content)
        return
    if content and not content.endswith("\n"):
        content += "\n"
    content += ZHA_BLOCK
    with open(CONFIGURATION_YAML, "w", encoding="utf-8") as h:
        h.write(content)
