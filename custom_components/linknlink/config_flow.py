"""Config flow for LinknLink iBG gateways."""

from __future__ import annotations

from typing import Any

from aiolinknlink import IbgClient, IbgConnectionError, IbgError
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST

from .const import DOMAIN


class LinknLinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a local LinknLink gateway."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle user setup by host address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            try:
                client = IbgClient()
                device = await client.discover_host(host)
                await client.connect(device)
            except IbgConnectionError:
                errors["base"] = "cannot_connect"
            except IbgError:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(device.id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: device.ip})
                return self.async_create_entry(
                    title=f"{device.model} ({device.ip})",
                    data={CONF_HOST: device.ip},
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )
