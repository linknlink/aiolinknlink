"""Config flow for LinknLink iBG gateways."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from aiolinknlink import IbgClient, IbgConnectionError, IbgError

from .const import CONF_LOCAL_KEY, DOMAIN, resolve_local_key_hex


class LinknLinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a local LinknLink gateway."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle user setup by host address."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST].strip()
            local_key_hex = user_input.get(CONF_LOCAL_KEY, "").strip().lower()
            try:
                if local_key_hex and (
                    len(local_key_hex) != 32 or any(c not in "0123456789abcdef" for c in local_key_hex)
                ):
                    errors[CONF_LOCAL_KEY] = "invalid_local_key"
                    raise ValueError
                client = IbgClient()
                device = await client.discover_host(host)
                session = await client.connect(
                    device, local_key=bytes.fromhex(local_key_hex) if local_key_hex else None
                )
                await client.list_subdevices(session)
            except ValueError:
                pass
            except IbgConnectionError:
                errors["base"] = "cannot_connect"
            except IbgError:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(device.id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: device.ip})
                stored_local_key_hex = resolve_local_key_hex(local_key_hex, session.session_key)
                return self.async_create_entry(
                    title=f"{device.model} ({device.ip})",
                    data={
                        CONF_HOST: device.ip,
                        **({CONF_LOCAL_KEY: stored_local_key_hex} if stored_local_key_hex else {}),
                    },
                )
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Optional(CONF_LOCAL_KEY, default=""): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                }
            ),
            errors=errors,
        )
