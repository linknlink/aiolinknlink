"""LinknLink Home Assistant integration."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
import sys

# Development deployments keep the library checkout in HA's persistent
# configuration volume, so container replacement does not remove it.
_LIBRARY_SOURCE = Path(__file__).parents[2] / "deps" / "aiolinknlink" / "src"
if _LIBRARY_SOURCE.is_dir():
    with suppress(ValueError):
        sys.path.remove(str(_LIBRARY_SOURCE))
    sys.path.insert(0, str(_LIBRARY_SOURCE))

from aiolinknlink import IbgClient, IbgError  # noqa: E402

from homeassistant.config_entries import ConfigEntry  # noqa: E402
from homeassistant.const import CONF_HOST  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.exceptions import ConfigEntryNotReady  # noqa: E402
from homeassistant.helpers import device_registry as dr  # noqa: E402

from .const import PLATFORMS  # noqa: E402
from .coordinator import IbgDataUpdateCoordinator  # noqa: E402

type LinknLinkConfigEntry = ConfigEntry[IbgDataUpdateCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: LinknLinkConfigEntry) -> bool:
    """Set up LinknLink from a config entry."""
    client = IbgClient()
    try:
        device = await client.discover_host(entry.data[CONF_HOST])
        session = await client.connect(device)
    except IbgError as err:
        raise ConfigEntryNotReady(f"Could not connect to iBG gateway: {err}") from err
    coordinator = IbgDataUpdateCoordinator(hass, client, device, session)
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
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
