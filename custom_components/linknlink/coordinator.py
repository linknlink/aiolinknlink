"""Data coordinator for LinknLink iBG gateways."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from aiolinknlink import (
    IbgClient,
    IbgConnectionError,
    IbgDevice,
    IbgError,
    IbgSession,
    IbgSubDevice,
    IbgSubDeviceState,
)

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DOMAIN, UPDATE_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IbgCoordinatorData:
    """Latest safe iBG state used by Home Assistant entities."""

    subdevices: tuple[IbgSubDevice, ...]
    states: dict[str, IbgSubDeviceState | None]


class IbgDataUpdateCoordinator(DataUpdateCoordinator[IbgCoordinatorData]):
    """Poll one local iBG gateway."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: IbgClient,
        device: IbgDevice,
        session: IbgSession,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{device.id}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.client = client
        self.device = device
        self.session = session

    async def _async_update_data(self) -> IbgCoordinatorData:
        try:
            return await self._read_data()
        except IbgConnectionError:
            try:
                self.session = await self.client.connect(self.device)
                return await self._read_data()
            except (IbgConnectionError, IbgError) as err:
                raise UpdateFailed(f"Could not update iBG gateway {self.device.ip}: {err}") from err
        except IbgError as err:
            raise UpdateFailed(f"Invalid response from iBG gateway {self.device.ip}: {err}") from err

    async def _read_data(self) -> IbgCoordinatorData:
        subdevices = await self.client.list_subdevices(self.session)
        states = await self.client.read_supported_states(self.session, subdevices)
        return IbgCoordinatorData(tuple(subdevices), states)
