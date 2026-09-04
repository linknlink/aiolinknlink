"""Focused tests for the legacy eMotion presence-sensor integration."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock

import pytest

pytest.importorskip("homeassistant")

from aiolinknlink import (  # noqa: E402
    PID_EMOTION,
    TYPE_EMOTION,
    EmotionPresenceState,
    UltraDevice,
    UltraEnvironmentState,
    UltraSession,
)
from custom_components.linknlink.binary_sensor import (  # noqa: E402
    ULTRA_BINARY_SENSORS,
    UltraBinarySensor,
)
from custom_components.linknlink.coordinator import (  # noqa: E402
    UltraCoordinatorData,
    UltraDataUpdateCoordinator,
)
from custom_components.linknlink.number import (  # noqa: E402
    EMOTION_NUMBERS,
    UltraRadarNumber,
)
from custom_components.linknlink.sensor import (  # noqa: E402
    EMOTION_SENSORS,
    UltraSensor,
)

DEVICE = UltraDevice(
    id="e04b41006515",
    ip="192.168.3.31",
    port=80,
    mac="e0:4b:41:00:65:15",
    pid=PID_EMOTION,
    type_id=TYPE_EMOTION,
    name="eMotion",
    model="eMotion",
)
STATE = EmotionPresenceState(
    device_id=DEVICE.id,
    occupied=True,
    absence_delay=60,
    sensitivity=2,
    firmware_version=62217,
    received_at=datetime.now(UTC),
)


def _coordinator(client: object | None = None) -> UltraDataUpdateCoordinator:
    coordinator = object.__new__(UltraDataUpdateCoordinator)
    coordinator.client = client or AsyncMock()
    coordinator.device = DEVICE
    coordinator.session = UltraSession(device=DEVICE, session_key=b"0123456789abcdef")
    coordinator.local_key = None
    coordinator.position_subscription = None
    coordinator.data = UltraCoordinatorData(
        UltraEnvironmentState(
            device_id=DEVICE.id,
            values={
                "occupancy": True,
                "absence_delay": 60,
                "sensitivity": 2,
                "firmware_version": 62217,
            },
            available_fields=frozenset({"occupancy", "absence_delay", "sensitivity", "firmware_version"}),
            received_at=STATE.received_at,
        ),
        None,
        None,
        STATE,
    )
    coordinator.last_update_success = True
    coordinator.async_set_updated_data = Mock()
    return coordinator


async def test_emotion_coordinator_reads_only_presence_state() -> None:
    client = AsyncMock()
    client.get_emotion_state.return_value = STATE
    coordinator = _coordinator(client)
    coordinator.data = None

    data = await coordinator._async_update_data()

    assert data.emotion is STATE
    assert data.radar is None
    assert data.position is None
    assert data.environment.values["occupancy"] is True
    client.get_emotion_state.assert_awaited_once_with(coordinator.session)


async def test_emotion_number_controls_use_confirmed_writes() -> None:
    coordinator = _coordinator()
    coordinator.async_set_emotion_absence_delay = AsyncMock()
    coordinator.async_set_emotion_sensitivity = AsyncMock()

    delay = UltraRadarNumber(coordinator, EMOTION_NUMBERS[0])
    sensitivity = UltraRadarNumber(coordinator, EMOTION_NUMBERS[1])

    assert delay.native_value == 60
    assert sensitivity.native_value == 2
    await delay.async_set_native_value(30)
    await sensitivity.async_set_native_value(1)
    coordinator.async_set_emotion_absence_delay.assert_awaited_once_with(30)
    coordinator.async_set_emotion_sensitivity.assert_awaited_once_with(1)


def test_emotion_entities_do_not_create_ultra2_extras() -> None:
    coordinator = _coordinator()
    occupancy = UltraBinarySensor(coordinator, ULTRA_BINARY_SENSORS[0])
    firmware = UltraSensor(coordinator, EMOTION_SENSORS[0])

    assert occupancy.is_on is True
    assert firmware.native_value == 62217
    assert firmware.available
