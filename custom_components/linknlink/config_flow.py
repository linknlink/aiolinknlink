"""Config flow for local LinknLink devices."""

from __future__ import annotations

import asyncio
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from aiolinknlink import (
    IbgClient,
    IbgConnectionError,
    IbgDevice,
    IbgError,
    UltraClient,
    UltraConnectionError,
    UltraDevice,
    UltraError,
    PID_ULTRA,
    TYPE_ULTRA,
)

from .const import (
    CONF_DEVICE_TYPE,
    CONF_LOCAL_KEY,
    DEVICE_TYPE_IBG,
    DEVICE_TYPE_ULTRA,
    DEVICE_TYPE_ULTRA2,
    DOMAIN,
    resolve_local_key_hex,
)


class LinknLinkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure a local LinknLink device."""

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
                device = await _discover_device(host)
                if isinstance(device, IbgDevice):
                    client = IbgClient()
                    session = await client.connect(
                        device, local_key=bytes.fromhex(local_key_hex) if local_key_hex else None
                    )
                    await client.list_subdevices(session)
                    stored_local_key_hex = resolve_local_key_hex(local_key_hex, session.session_key)
                    entry_data = {
                        CONF_HOST: device.ip,
                        CONF_DEVICE_TYPE: DEVICE_TYPE_IBG,
                        **({CONF_LOCAL_KEY: stored_local_key_hex} if stored_local_key_hex else {}),
                    }
                else:
                    client = UltraClient()
                    await client.connect(
                        device,
                        session_key=bytes.fromhex(local_key_hex) if local_key_hex else None,
                    )
                    entry_data = {
                        CONF_HOST: device.ip,
                        CONF_DEVICE_TYPE: (
                            DEVICE_TYPE_ULTRA
                            if device.type_id == TYPE_ULTRA or device.pid.lower() == PID_ULTRA
                            else DEVICE_TYPE_ULTRA2
                        ),
                        **({CONF_LOCAL_KEY: local_key_hex} if local_key_hex else {}),
                    }
            except ValueError:
                pass
            except (IbgConnectionError, UltraConnectionError):
                errors["base"] = "cannot_connect"
            except (IbgError, UltraError):
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(device.id)
                self._abort_if_unique_id_configured(updates={CONF_HOST: device.ip})
                return self.async_create_entry(
                    title=f"{device.model} ({device.ip})",
                    data=entry_data,
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


async def _discover_device(host: str) -> IbgDevice | UltraDevice:
    """Detect one supported device without assuming its product family."""
    ibg_result, ultra_result = await asyncio.gather(
        IbgClient().discover_host(host),
        UltraClient().discover_host(host),
        return_exceptions=True,
    )
    if isinstance(ibg_result, IbgDevice):
        return ibg_result
    if isinstance(ultra_result, UltraDevice):
        return ultra_result
    if isinstance(ibg_result, IbgConnectionError) and isinstance(ultra_result, UltraConnectionError):
        raise IbgConnectionError(f"no supported LinknLink device found at {host}")
    for result in (ibg_result, ultra_result):
        if isinstance(result, (IbgError, UltraError)):
            raise result
    raise IbgConnectionError(f"no supported LinknLink device found at {host}")
