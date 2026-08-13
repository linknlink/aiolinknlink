"""Focused tests for the Home Assistant iBG coordinator and entities."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")

from homeassistant.helpers.update_coordinator import UpdateFailed  # noqa: E402

from aiolinknlink import (  # noqa: E402
    IbgConnectionError,
    IbgDevice,
    IbgProtocolError,
    IbgSession,
    IbgSubDevice,
    IbgSubDeviceState,
)
from custom_components.linknlink.coordinator import (  # noqa: E402
    IbgCoordinatorData,
    IbgDataUpdateCoordinator,
)
from custom_components.linknlink.entity import IbgCoordinatorEntity  # noqa: E402

GATEWAY = IbgDevice(
    id="001122334455",
    ip="192.168.1.10",
    port=80,
    mac="00:11:22:33:44:55",
    type_id=0x2B71,
)
SUBDEVICE = IbgSubDevice(
    did="00112233445566778899aabbccddeeff",
    pid="00000000000000000000000005000100",
    name="Room sensor",
    online=True,
)


def _coordinator(client: object) -> IbgDataUpdateCoordinator:
    coordinator = object.__new__(IbgDataUpdateCoordinator)
    coordinator.client = client  # type: ignore[assignment]
    coordinator.device = GATEWAY
    coordinator.session = IbgSession(device=GATEWAY, session_key=b"0123456789abcdef")
    return coordinator


async def test_coordinator_reauthenticates_once_after_connection_failure() -> None:
    client = AsyncMock()
    refreshed = IbgSession(device=GATEWAY, session_key=b"fedcba9876543210")
    client.list_subdevices.side_effect = [IbgConnectionError("expired"), [SUBDEVICE]]
    client.connect.return_value = refreshed
    client.read_supported_states.return_value = {SUBDEVICE.did: None}
    coordinator = _coordinator(client)

    data = await coordinator._async_update_data()

    assert data.subdevices == (SUBDEVICE,)
    assert data.states == {SUBDEVICE.did: None}
    assert coordinator.session is refreshed
    client.connect.assert_awaited_once_with(GATEWAY)


async def test_coordinator_converts_repeated_connection_failure() -> None:
    client = AsyncMock()
    client.list_subdevices.side_effect = IbgConnectionError("offline")
    client.connect.side_effect = IbgConnectionError("still offline")
    coordinator = _coordinator(client)

    with pytest.raises(UpdateFailed, match="Could not update"):
        await coordinator._async_update_data()


async def test_coordinator_converts_protocol_failure_without_reauth() -> None:
    client = AsyncMock()
    client.list_subdevices.side_effect = IbgProtocolError("invalid response")
    coordinator = _coordinator(client)

    with pytest.raises(UpdateFailed, match="Invalid response"):
        await coordinator._async_update_data()
    client.connect.assert_not_awaited()


def test_entity_availability_and_safe_value() -> None:
    state = IbgSubDeviceState(
        SUBDEVICE,
        {"temperature": 25.5},
        datetime.now(UTC),
    )
    coordinator = object.__new__(IbgDataUpdateCoordinator)
    coordinator.device = GATEWAY
    coordinator.data = IbgCoordinatorData((SUBDEVICE,), {SUBDEVICE.did: state})
    coordinator.last_update_success = True
    entity = IbgCoordinatorEntity(coordinator, SUBDEVICE.did, "temperature")

    assert entity.available
    assert entity._value() == 25.5
    assert entity.device_info["via_device"] == ("linknlink", GATEWAY.id)

    coordinator.data = IbgCoordinatorData((SUBDEVICE,), {SUBDEVICE.did: None})
    assert not entity.available
    assert entity._value() is None
