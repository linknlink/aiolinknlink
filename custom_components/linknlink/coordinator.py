"""Data coordinator for LinknLink iBG gateways."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from aiolinknlink import (
    IbgClient,
    IbgConnectionError,
    IbgDevice,
    IbgError,
    IbgPushSubscription,
    IbgSession,
    IbgSubDevice,
    IbgSubDeviceState,
)

from .const import DOMAIN, UPDATE_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IbgCoordinatorData:
    """Latest safe iBG state used by Home Assistant entities."""

    subdevices: tuple[IbgSubDevice, ...]
    states: dict[str, IbgSubDeviceState | None]
    key_event_counts: dict[str, int] = field(default_factory=dict)


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
        self.push_subscription: IbgPushSubscription | None = None
        self._last_keypressed: dict[str, int] = {}
        self._key_event_counts: dict[str, int] = {}

    async def _async_update_data(self) -> IbgCoordinatorData:
        try:
            return await self._read_data()
        except IbgConnectionError:
            try:
                self.session = await self.client.connect(self.device)
                if self.push_subscription is not None:
                    self.push_subscription.update_session(self.session)
                return await self._read_data()
            except IbgError as err:
                raise UpdateFailed(f"Could not update iBG gateway {self.device.ip}: {err}") from err
        except IbgError as err:
            raise UpdateFailed(f"Invalid response from iBG gateway {self.device.ip}: {err}") from err

    async def _read_data(self) -> IbgCoordinatorData:
        if self.push_subscription is not None:
            self.session = self.push_subscription.session
        subdevices = await self.client.list_subdevices(self.session)
        states = await self.client.read_supported_states(self.session, subdevices)
        self._track_key_edges(states)
        if self.push_subscription is not None:
            self.push_subscription.update_devices(subdevices)
        return IbgCoordinatorData(tuple(subdevices), states, dict(self._key_event_counts))

    async def async_start_push(self) -> None:
        """Start the best-effort iBG local status push listener."""
        if self.push_subscription is not None:
            return
        subscription = IbgPushSubscription(
            self.client,
            self.session,
            self.data.subdevices,
            self._handle_push_state,
        )
        try:
            await subscription.start()
        except OSError as err:
            _LOGGER.warning("Could not start iBG local push listener; polling remains active: %s", err)
            return
        self.push_subscription = subscription

    async def async_shutdown(self) -> None:
        """Stop the local push listener."""
        if self.push_subscription is None:
            return
        await self.push_subscription.stop()
        self.push_subscription = None

    def _handle_push_state(self, state: IbgSubDeviceState) -> None:
        """Merge one authenticated local push and notify entities immediately."""
        if self.data is None or state.device.did not in self.data.states:
            return
        states = dict(self.data.states)
        states[state.device.did] = state
        self._track_key_edges({state.device.did: state})
        self.async_set_updated_data(
            IbgCoordinatorData(
                self.data.subdevices,
                states,
                dict(self._key_event_counts),
            )
        )

    def _track_key_edges(self, states: dict[str, IbgSubDeviceState | None]) -> None:
        """Count only confirmed 2-to-1 physical key transitions."""
        for did, state in states.items():
            if state is None:
                continue
            value = state.values.get("keypressed")
            if not isinstance(value, int) or isinstance(value, bool) or value not in {1, 2}:
                continue
            previous = self._last_keypressed.get(did)
            self._last_keypressed[did] = value
            if previous == 2 and value == 1:
                self._key_event_counts[did] = self._key_event_counts.get(did, 0) + 1
