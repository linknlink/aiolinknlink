"""Data coordinator for LinknLink iBG gateways."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from aiolinknlink import (
    PID_ESENSOR_2000,
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
    key_event_values: dict[str, int] = field(default_factory=dict)


class IbgDataUpdateCoordinator(DataUpdateCoordinator[IbgCoordinatorData]):
    """Poll one local iBG gateway."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: IbgClient,
        device: IbgDevice,
        session: IbgSession,
        *,
        local_key: bytes | None = None,
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
        self.local_key = local_key
        self.push_subscription: IbgPushSubscription | None = None
        self._last_keypressed: dict[str, int] = {}
        self._key_event_counts: dict[str, int] = {}
        self._key_event_values: dict[str, int] = {}

    async def _async_update_data(self) -> IbgCoordinatorData:
        try:
            return await self._read_data()
        except IbgConnectionError:
            try:
                self.session = await self.client.connect(self.device, local_key=self.local_key)
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
        return IbgCoordinatorData(
            tuple(subdevices),
            states,
            dict(self._key_event_counts),
            dict(self._key_event_values),
        )

    async def async_set_subdevice_state(self, did: str, changes: dict[str, bool | int | float]) -> None:
        """Write one reviewed subdevice state and merge its confirmed response."""
        if self.data is None:
            raise IbgError("iBG coordinator has no device data")
        device = next((item for item in self.data.subdevices if item.did == did), None)
        if device is None:
            raise IbgError(f"iBG subdevice is no longer present: {did}")
        try:
            state = await self.client.set_subdevice_state(self.session, device, changes)
        except IbgConnectionError:
            self.session = await self.client.connect(self.device, local_key=self.local_key)
            if self.push_subscription is not None:
                self.push_subscription.update_session(self.session)
            state = await self.client.set_subdevice_state(self.session, device, changes)
        self._handle_push_state(state)

    async def async_start_push(self) -> None:
        """Start the best-effort iBG local status push listener."""
        if self.push_subscription is not None:
            return
        subscription = IbgPushSubscription(
            self.client,
            self.session,
            self.data.subdevices,
            self._handle_push_state,
            local_key=self.local_key,
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
        self._track_key_edges({state.device.did: state}, from_push=True)
        self.async_set_updated_data(
            IbgCoordinatorData(
                self.data.subdevices,
                states,
                dict(self._key_event_counts),
                dict(self._key_event_values),
            )
        )

    def _track_key_edges(
        self,
        states: dict[str, IbgSubDeviceState | None],
        *,
        from_push: bool = False,
    ) -> None:
        """Count confirmed physical key transitions and eSensor key actions."""
        for did, state in states.items():
            if state is None:
                continue
            value = state.values.get("keypressed")
            if not isinstance(value, int) or isinstance(value, bool):
                continue
            previous = self._last_keypressed.get(did)
            self._last_keypressed[did] = value
            if state.device.pid == PID_ESENSOR_2000:
                if from_push and value in {1, 3, 4}:
                    self._key_event_counts[did] = self._key_event_counts.get(did, 0) + 1
                    self._key_event_values[did] = value
            elif value in {1, 2} and previous == 2 and value == 1:
                self._key_event_counts[did] = self._key_event_counts.get(did, 0) + 1
