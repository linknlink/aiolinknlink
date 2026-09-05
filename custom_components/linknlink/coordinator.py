"""Data coordinator for LinknLink iBG gateways."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from aiolinknlink import (
    PID_EMOTION,
    PID_ESENSOR_2000_GEN1,
    PID_ESENSOR_2000_GEN2,
    PID_SINGLE_CHANNEL_LIGHT_SWITCH,
    PID_THREE_CHANNEL_LIGHT_SWITCH,
    PID_TWO_CHANNEL_LIGHT_SWITCH,
    PID_ULTRA,
    SINGLE_CHANNEL_LIGHT_SCENE_FIELDS,
    THREE_CHANNEL_LIGHT_SCENE_FIELDS,
    TWO_CHANNEL_LIGHT_SCENE_FIELDS,
    TYPE_EMOTION,
    TYPE_EMOTION_WIRE,
    TYPE_ULTRA,
    EHomeClient,
    EHomeConnectionError,
    EHomeDevice,
    EHomeError,
    EHomeSession,
    EHomeState,
    EmotionPresenceState,
    IbgClient,
    IbgConnectionError,
    IbgDevice,
    IbgError,
    IbgPushSubscription,
    IbgSession,
    IbgSubDevice,
    IbgSubDeviceState,
    UltraClient,
    UltraConnectionError,
    UltraDevice,
    UltraEnvironmentState,
    UltraError,
    UltraPositionSubscription,
    UltraPositionSubscriptionState,
    UltraPositionUpdate,
    UltraProtocolError,
    UltraRadarStatus,
    UltraSession,
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
    scene_event_counts: dict[tuple[str, str], int] = field(default_factory=dict)
    scene_event_values: dict[tuple[str, str], int] = field(default_factory=dict)


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
        self._last_scene_values: dict[tuple[str, str], int] = {}
        self._scene_event_counts: dict[tuple[str, str], int] = {}
        self._scene_event_values: dict[tuple[str, str], int] = {}

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
        self._track_scene_edges(states)
        if self.push_subscription is not None:
            self.push_subscription.update_devices(subdevices)
        return IbgCoordinatorData(
            tuple(subdevices),
            states,
            dict(self._key_event_counts),
            dict(self._key_event_values),
            dict(self._scene_event_counts),
            dict(self._scene_event_values),
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
        self._handle_push_state(state, from_push=False)

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

    def _handle_push_state(
        self,
        state: IbgSubDeviceState,
        *,
        from_push: bool = True,
    ) -> None:
        """Merge one confirmed state and notify entities immediately."""
        if self.data is None or state.device.did not in self.data.states:
            return
        states = dict(self.data.states)
        states[state.device.did] = state
        self._track_key_edges({state.device.did: state}, from_push=from_push)
        self._track_scene_edges({state.device.did: state}, from_push=from_push)
        self.async_set_updated_data(
            IbgCoordinatorData(
                self.data.subdevices,
                states,
                dict(self._key_event_counts),
                dict(self._key_event_values),
                dict(self._scene_event_counts),
                dict(self._scene_event_values),
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
            if state.device.pid in {PID_ESENSOR_2000_GEN1, PID_ESENSOR_2000_GEN2}:
                supported_actions = {1, 3} if state.device.pid == PID_ESENSOR_2000_GEN1 else {1, 3, 4}
                if from_push and value in supported_actions:
                    self._key_event_counts[did] = self._key_event_counts.get(did, 0) + 1
                    self._key_event_values[did] = value
            elif value in {1, 2} and previous == 2 and value == 1:
                self._key_event_counts[did] = self._key_event_counts.get(did, 0) + 1

    def _track_scene_edges(
        self,
        states: dict[str, IbgSubDeviceState | None],
        *,
        from_push: bool = False,
    ) -> None:
        """Count only pushed zero-to-one transitions from momentary scene keys."""
        for did, state in states.items():
            if state is None:
                continue
            scene_fields = {
                PID_SINGLE_CHANNEL_LIGHT_SWITCH: SINGLE_CHANNEL_LIGHT_SCENE_FIELDS,
                PID_TWO_CHANNEL_LIGHT_SWITCH: TWO_CHANNEL_LIGHT_SCENE_FIELDS,
                PID_THREE_CHANNEL_LIGHT_SWITCH: THREE_CHANNEL_LIGHT_SCENE_FIELDS,
            }.get(state.device.pid)
            if scene_fields is None:
                continue
            for field_name in scene_fields:
                value = state.values.get(field_name)
                if not isinstance(value, int) or isinstance(value, bool) or value not in {0, 1}:
                    continue
                event_key = (did, field_name)
                previous = self._last_scene_values.get(event_key)
                self._last_scene_values[event_key] = value
                if from_push and previous == 0 and value == 1:
                    self._scene_event_counts[event_key] = self._scene_event_counts.get(event_key, 0) + 1
                    self._scene_event_values[event_key] = value


class EHomeDataUpdateCoordinator(DataUpdateCoordinator[EHomeState]):
    """Poll one eHome/EHUB Modbus TCP sensor."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: EHomeClient,
        device: EHomeDevice,
        session: EHomeSession,
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

    async def _async_update_data(self) -> EHomeState:
        try:
            return await self.client.read_state(self.session)
        except EHomeConnectionError:
            self.session = await self.client.connect(self.device)
            return await self.client.read_state(self.session)
        except EHomeError as err:
            raise UpdateFailed(f"Could not update eHome {self.device.ip}: {err}") from err

    async def async_set_absence_delay(self, value: int) -> None:
        """Set and publish the confirmed absence delay."""
        try:
            state = await self.client.set_absence_delay(self.session, value)
        except EHomeConnectionError:
            self.session = await self.client.connect(self.device)
            state = await self.client.set_absence_delay(self.session, value)
        self.async_set_updated_data(state)

    async def async_shutdown(self) -> None:
        """Release the logical session."""
        await self.client.close(self.session)


@dataclass(frozen=True, slots=True)
class UltraCoordinatorData:
    """Latest validated eMotion Ultra state used by Home Assistant entities."""

    environment: UltraEnvironmentState
    radar: UltraRadarStatus | None
    position: UltraPositionSubscriptionState | None = None
    emotion: EmotionPresenceState | None = None


class UltraDataUpdateCoordinator(DataUpdateCoordinator[UltraCoordinatorData]):
    """Poll one local eMotion Ultra or Ultra2."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: UltraClient,
        device: UltraDevice,
        session: UltraSession,
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
        self.position_subscription: UltraPositionSubscription | None = None

    async def _async_update_data(self) -> UltraCoordinatorData:
        try:
            return await self._read_data()
        except UltraProtocolError as err:
            raise UpdateFailed(f"Invalid response from Ultra2 {self.device.ip}: {err}") from err
        except UltraError as err:
            if self.position_subscription is not None:
                raise UpdateFailed(f"Could not update Ultra2 {self.device.ip}: {err}") from err
            try:
                self.session = await self.client.connect(self.device, session_key=self.local_key)
                return await self._read_data()
            except UltraError as err:
                raise UpdateFailed(f"Could not update Ultra2 {self.device.ip}: {err}") from err

    async def _read_data(self) -> UltraCoordinatorData:
        if self._is_emotion:
            emotion = await self.client.get_emotion_state(self.session)
            environment = UltraEnvironmentState(
                device_id=emotion.device_id,
                values={
                    "occupancy": emotion.occupied,
                    "absence_delay": emotion.absence_delay,
                    "sensitivity": emotion.sensitivity,
                    "firmware_version": emotion.firmware_version,
                },
                available_fields=frozenset({"occupancy", "absence_delay", "sensitivity", "firmware_version"}),
                received_at=emotion.received_at,
            )
            return UltraCoordinatorData(
                environment=environment,
                radar=None,
                position=None,
                emotion=emotion,
            )
        environment = await self.client.get_environment_state(self.session)
        if self.device.type_id == TYPE_ULTRA or self.device.pid.lower() == PID_ULTRA:
            return UltraCoordinatorData(environment=environment, radar=None, position=None)
        if self.position_subscription is None:
            radar = await self.client.get_radar_status(self.session)
            position = None
        else:
            radar = await self.position_subscription.get_radar_status()
            position = self.position_subscription.state
        return UltraCoordinatorData(environment=environment, radar=radar, position=position)

    @property
    def _is_emotion(self) -> bool:
        return self.device.type_id in {TYPE_EMOTION, TYPE_EMOTION_WIRE} or self.device.pid.lower() == PID_EMOTION

    async def async_set_emotion_absence_delay(self, value: int) -> None:
        """Set eMotion absence delay and publish the confirmed state."""
        await self._async_emotion_operation(self.client.set_emotion_absence_delay, value)

    async def async_set_emotion_sensitivity(self, value: int) -> None:
        """Set eMotion sensitivity and publish the confirmed state."""
        await self._async_emotion_operation(self.client.set_emotion_sensitivity, value)

    async def _async_emotion_operation(
        self,
        operation: Callable[..., Awaitable[EmotionPresenceState]],
        value: int,
    ) -> None:
        try:
            state = await operation(self.session, value)
        except UltraProtocolError:
            raise
        except (UltraConnectionError, UltraError):
            self.session = await self.client.connect(self.device, session_key=self.local_key)
            state = await operation(self.session, value)
        environment = UltraEnvironmentState(
            device_id=state.device_id,
            values={
                "occupancy": state.occupied,
                "absence_delay": state.absence_delay,
                "sensitivity": state.sensitivity,
                "firmware_version": state.firmware_version,
            },
            available_fields=frozenset({"occupancy", "absence_delay", "sensitivity", "firmware_version"}),
            received_at=state.received_at,
        )
        self.async_set_updated_data(
            UltraCoordinatorData(environment=environment, radar=None, position=None, emotion=state)
        )

    async def async_start_position(self) -> None:
        """Start local multi-target position updates after entry setup."""
        if self.position_subscription is not None:
            return
        subscription = UltraPositionSubscription(
            self.client,
            self.session,
            callback=self._handle_position_update,
            status_callback=self._handle_position_status,
        )
        await subscription.start()
        self.position_subscription = subscription

    def _handle_position_update(self, update: UltraPositionUpdate) -> None:
        """Publish one local position update immediately."""
        if self.data is not None and self.position_subscription is not None:
            environment = self.data.environment
            values = dict(environment.values)
            # The UDP position stream is the freshest source for total presence
            # and target count. ESPHome can lag behind while the radar is active.
            values["occupancy"] = update.target_count > 0
            values["target_count"] = update.target_count
            environment = replace(
                environment,
                values=values,
                available_fields=environment.available_fields | frozenset({"occupancy", "target_count"}),
                received_at=update.received_at,
            )
            self.async_set_updated_data(
                replace(
                    self.data,
                    environment=environment,
                    position=self.position_subscription.state,
                )
            )

    def _handle_position_status(self, state: UltraPositionSubscriptionState) -> None:
        """Publish subscription and position freshness changes."""
        if self.data is not None:
            self.async_set_updated_data(replace(self.data, position=state))

    async def async_set_radar_sensitivity(self, value: int) -> None:
        """Set radar sensitivity and merge the confirmed read-back."""
        await self._async_radar_operation("set_radar_sensitivity", self.client.set_radar_sensitivity, value)

    async def async_set_radar_trigger_speed(self, value: int) -> None:
        """Set radar trigger speed and merge the confirmed read-back."""
        await self._async_radar_operation("set_radar_trigger_speed", self.client.set_radar_trigger_speed, value)

    async def async_set_radar_install_mode(self, value: int) -> None:
        """Set radar installation mode and merge the confirmed read-back."""
        await self._async_radar_operation("set_radar_install_mode", self.client.set_radar_install_mode, value)

    async def async_set_radar_height(self, value: int) -> None:
        """Set radar installation height and merge the confirmed read-back."""
        await self._async_radar_operation("set_radar_height", self.client.set_radar_height, value)

    async def async_set_radar_install_direction(self, value: int) -> None:
        """Set radar installation direction and merge the confirmed read-back."""
        await self._async_radar_operation(
            "set_radar_install_direction",
            self.client.set_radar_install_direction,
            value,
        )

    async def async_set_radar_z_range(self, minimum: float, maximum: float) -> None:
        """Set both Z-axis limits and merge the confirmed read-back."""
        await self._async_radar_operation(
            "set_radar_z_range",
            self.client.set_radar_z_range,
            minimum,
            maximum,
        )

    async def async_set_radar_default_absence_delay(self, value: int) -> None:
        """Set the default absence delay and merge the confirmed read-back."""
        await self._async_radar_operation(
            "set_radar_default_absence_delay",
            self.client.set_radar_default_absence_delay,
            value,
        )

    async def async_set_radar_zone_absence_delay(self, zone: int, value: int) -> None:
        """Set one zone absence delay and merge the confirmed read-back."""
        await self._async_radar_operation(
            "set_radar_zone_absence_delay",
            self.client.set_radar_zone_absence_delay,
            zone,
            value,
        )

    async def _async_radar_operation(
        self,
        operation_name: str,
        operation: Callable[..., Awaitable[UltraRadarStatus]],
        *args: object,
    ) -> None:
        """Run one radar write, retry one expired session, and publish confirmation."""
        if self.position_subscription is not None:
            radar = await self._position_radar_operation(operation_name, *args)
        else:
            try:
                radar = await operation(self.session, *args)
            except UltraProtocolError:
                raise
            except (UltraConnectionError, UltraError):
                self.session = await self.client.connect(self.device, session_key=self.local_key)
                radar = await operation(self.session, *args)
        if self.data is None:
            raise UltraError("Ultra2 coordinator has no device data")
        self.async_set_updated_data(replace(self.data, radar=radar))

    async def _position_radar_operation(self, operation_name: str, *args: object) -> UltraRadarStatus:
        """Run one radar operation through the position subscription socket."""
        subscription = self.position_subscription
        if subscription is None:
            raise UltraError("Ultra2 position subscription is not running")
        operation = getattr(subscription, operation_name, None)
        if operation is None:
            raise UltraError(f"Unsupported Ultra2 radar operation: {operation_name}")
        return await operation(*args)

    async def async_shutdown(self) -> None:
        """Shut down coordinator resources."""
        if self.position_subscription is not None:
            await self.position_subscription.stop()
            self.position_subscription = None
