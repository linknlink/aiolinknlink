"""Focused tests for the Home Assistant eMotion Ultra2 integration."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("homeassistant")

from aiolinknlink import (  # noqa: E402
    TYPE_ULTRA2,
    UltraConnectionError,
    UltraDevice,
    UltraEnvironmentState,
    UltraPositionSubscriptionState,
    UltraPositionUpdate,
    UltraRadarStatus,
    UltraRadarZRange,
    UltraSession,
    UltraTargetPosition,
)
from custom_components.linknlink.binary_sensor import (  # noqa: E402
    ULTRA_BINARY_SENSORS,
    UltraBinarySensor,
    UltraPositionSubscriptionBinarySensor,
)
from custom_components.linknlink.coordinator import (  # noqa: E402
    UltraCoordinatorData,
    UltraDataUpdateCoordinator,
)
from custom_components.linknlink.number import (  # noqa: E402
    ULTRA_RADAR_NUMBERS,
    UltraRadarNumber,
)
from custom_components.linknlink.sensor import (  # noqa: E402
    ULTRA_POSITION_SENSORS,
    ULTRA_SENSORS,
    UltraPositionSensor,
    UltraSensor,
)

DEVICE = UltraDevice(
    id="e04b41024395",
    ip="192.168.3.61",
    port=80,
    mac="e0:4b:41:02:43:95",
    type_id=TYPE_ULTRA2,
    name="eMotion Ultra 2",
)
SESSION = UltraSession(
    device=DEVICE,
    session_key=b"0123456789abcdef",
    auth_mac=DEVICE.mac,
)
ENVIRONMENT = UltraEnvironmentState(
    device_id=DEVICE.id,
    values={
        "temperature": 27.68,
        "humidity": 56.47,
        "illuminance": 224.0,
        "wifi_signal": -57,
        "occupancy": True,
        "target_count": 2,
        "persons_in_fenced_zones": 1,
        "zone_1_presence": True,
        "zone_1_target_counts": 1,
        "zone_2_presence": False,
        "zone_2_target_counts": 0,
        "zone_3_presence": False,
        "zone_3_target_counts": 0,
        "zone_4_presence": False,
        "zone_4_target_counts": 0,
    },
    available_fields=frozenset(
        {
            "temperature",
            "humidity",
            "illuminance",
            "wifi_signal",
            "occupancy",
            "target_count",
            "persons_in_fenced_zones",
            "zone_1_presence",
            "zone_1_target_counts",
            "zone_2_presence",
            "zone_2_target_counts",
            "zone_3_presence",
            "zone_3_target_counts",
            "zone_4_presence",
            "zone_4_target_counts",
        }
    ),
    received_at=datetime.now(UTC),
)
RADAR = UltraRadarStatus(
    did="e04b41024395dbac00000000dbac0001",
    sensitivity=2,
    trigger_speed=2,
    install_mode=1,
    height=100,
    install_direction=0,
    z_range=UltraRadarZRange(-2.0, 2.0),
    default_absence_delay=60,
    zone_absence_delays=(60, 60, 60, 60),
    received_at=datetime.now(UTC),
)
POSITION_UPDATE = UltraPositionUpdate(
    source_ip=DEVICE.ip,
    targets=(UltraTargetPosition(0.3, 0.4, 1.2), UltraTargetPosition(1.0, 2.0, 0.5)),
    received_at=datetime.now(UTC),
)
POSITION = UltraPositionSubscriptionState(
    subscribed=True,
    stale=False,
    local_port=25825,
    confirmation_count=1,
    latest_update=POSITION_UPDATE,
)


def _coordinator(client: object | None = None) -> UltraDataUpdateCoordinator:
    coordinator = object.__new__(UltraDataUpdateCoordinator)
    coordinator.client = client or AsyncMock()  # type: ignore[assignment]
    coordinator.device = DEVICE
    coordinator.session = SESSION
    coordinator.position_subscription = None
    coordinator.data = UltraCoordinatorData(ENVIRONMENT, RADAR, POSITION)
    coordinator.last_update_success = True
    coordinator.async_set_updated_data = Mock()  # type: ignore[method-assign]
    return coordinator


async def test_ultra_coordinator_reads_environment_and_radar() -> None:
    client = AsyncMock()
    client.get_environment_state.return_value = ENVIRONMENT
    client.get_radar_status.return_value = RADAR
    coordinator = _coordinator(client)
    coordinator.data = None

    data = await coordinator._async_update_data()

    assert data.environment is ENVIRONMENT
    assert data.radar is RADAR
    client.get_environment_state.assert_awaited_once_with(SESSION)
    client.get_radar_status.assert_awaited_once_with(SESSION)


async def test_ultra_coordinator_reconnects_once_without_position_subscription() -> None:
    client = AsyncMock()
    refreshed_session = UltraSession(device=DEVICE, session_key=b"fedcba9876543210")
    client.get_environment_state.side_effect = [UltraConnectionError("expired"), ENVIRONMENT]
    client.connect.return_value = refreshed_session
    client.get_radar_status.return_value = RADAR
    coordinator = _coordinator(client)
    coordinator.data = None

    data = await coordinator._async_update_data()

    assert data.environment is ENVIRONMENT
    assert coordinator.session is refreshed_session
    client.connect.assert_awaited_once_with(DEVICE)


def test_ultra_position_update_refreshes_total_presence_values() -> None:
    coordinator = _coordinator()
    coordinator._handle_position_update(POSITION_UPDATE)

    published = coordinator.async_set_updated_data.call_args.args[0]
    assert published.environment.values["occupancy"] is True
    assert published.environment.values["target_count"] == 2
    assert published.position.latest_update is POSITION_UPDATE


def test_ultra_environment_entities_use_validated_values() -> None:
    coordinator = _coordinator()
    temperature = UltraSensor(coordinator, next(item for item in ULTRA_SENSORS if item.key == "temperature"))
    occupancy = UltraBinarySensor(
        coordinator,
        next(item for item in ULTRA_BINARY_SENSORS if item.key == "occupancy"),
    )

    assert temperature.native_value == 27.68
    assert occupancy.is_on is True
    assert temperature.device_info["identifiers"] == {("linknlink", DEVICE.id)}


def test_ultra_position_entities_expose_distances_and_coordinates() -> None:
    coordinator = _coordinator()
    subscription_entity = UltraPositionSubscriptionBinarySensor(coordinator)
    target_entity = UltraPositionSensor(
        coordinator,
        next(item for item in ULTRA_POSITION_SENSORS if item.key == "position_targets"),
    )
    horizontal_entity = UltraPositionSensor(
        coordinator,
        next(item for item in ULTRA_POSITION_SENSORS if item.key == "nearest_horizontal_distance"),
    )

    assert subscription_entity.available
    assert subscription_entity.is_on
    assert subscription_entity.extra_state_attributes["confirmation_count"] == 1
    assert target_entity.available
    assert target_entity.native_value == 2
    assert horizontal_entity.native_value == pytest.approx(0.5)
    assert target_entity.extra_state_attributes["targets"][0] == {
        "x": 0.3,
        "y": 0.4,
        "z": 1.2,
        "horizontal_distance": 0.5,
        "distance": 1.3,
    }


async def test_ultra_radar_number_uses_confirmed_coordinator_writes() -> None:
    coordinator = _coordinator()
    coordinator.async_set_radar_sensitivity = AsyncMock()  # type: ignore[method-assign]
    coordinator.async_set_radar_z_range = AsyncMock()  # type: ignore[method-assign]
    sensitivity = UltraRadarNumber(
        coordinator,
        next(item for item in ULTRA_RADAR_NUMBERS if item.key == "sensitivity"),
    )
    minimum = UltraRadarNumber(
        coordinator,
        next(item for item in ULTRA_RADAR_NUMBERS if item.key == "z_range_minimum"),
    )

    assert sensitivity.native_value == 2
    assert minimum.native_value == -2.0
    await sensitivity.async_set_native_value(1)
    await minimum.async_set_native_value(-1.5)

    coordinator.async_set_radar_sensitivity.assert_awaited_once_with(1)
    coordinator.async_set_radar_z_range.assert_awaited_once_with(-1.5, 2.0)
