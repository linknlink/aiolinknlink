"""Edge-case tests for the persistent local UDP transport."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from aiolinknlink import (
    TYPE_ULTRA2,
    UltraDevice,
    UltraError,
    UltraLocalUDPConfig,
    UltraPositionSubscription,
    UltraSession,
)
from aiolinknlink.local_udp import (
    UltraLocalUDPListener,
    UltraLocalUDPProtocol,
)
from aiolinknlink.protocol import dna, emotion


def session() -> UltraSession:
    """Create a connected-looking Ultra2 session."""
    return UltraSession(
        device=UltraDevice(
            id="e04b410244c7",
            ip="127.0.0.1",
            port=80,
            mac="e0:4b:41:02:44:c7",
            type_id=TYPE_ULTRA2,
        ),
        session_key=b"0123456789abcdef",
        auth_mac="e0:4b:41:02:44:c9",
    )


async def test_protocol_filters_payloads_and_isolates_callback_errors() -> None:
    loop = asyncio.get_running_loop()
    updates = []
    protocol = UltraLocalUDPProtocol(loop, updates.append, "127.0.0.1")

    protocol.datagram_received(b'{"detect_position":"[]"}', ("127.0.0.2", 80))
    protocol.datagram_received(b"bad", ("127.0.0.1", 80))
    assert updates == []

    def fail_callback(_update: object) -> None:
        raise RuntimeError("consumer failed")

    failing = UltraLocalUDPProtocol(loop, fail_callback, None)
    failing.datagram_received(
        b'{"detect_position":"[{\\"x\\":1,\\"y\\":2,\\"z\\":3}]"}',
        ("127.0.0.1", 80),
    )


async def test_protocol_pending_response_filters_and_errors() -> None:
    loop = asyncio.get_running_loop()
    protocol = UltraLocalUDPProtocol(loop, lambda _update: None, None)
    protocol.datagram_received(dna.MAGIC + b"ignored", ("127.0.0.1", 80))

    pending = loop.create_future()
    protocol._pending_response = pending
    protocol._pending_host = "127.0.0.1"
    protocol._pending_accept = lambda _data: False
    protocol.datagram_received(dna.MAGIC + b"rejected", ("127.0.0.1", 80))
    assert not pending.done()
    protocol._pending_accept = None
    protocol.datagram_received(dna.MAGIC + b"accepted", ("127.0.0.1", 80))
    assert await pending == dna.MAGIC + b"accepted"

    error_pending = loop.create_future()
    protocol._pending_response = error_pending
    protocol.error_received(OSError("socket error"))
    with pytest.raises(OSError, match="socket error"):
        await error_pending

    closed_pending = loop.create_future()
    protocol._pending_response = closed_pending
    protocol.connection_lost(None)
    with pytest.raises(ConnectionError, match="closed"):
        await closed_pending


async def test_protocol_exchange_validation_and_timeout() -> None:
    loop = asyncio.get_running_loop()
    protocol = UltraLocalUDPProtocol(loop, lambda _update: None, None)
    with pytest.raises(dna.DNAError, match="greater than zero"):
        await protocol.exchange("127.0.0.1", 80, b"packet", 0, None)
    with pytest.raises(dna.DNAError, match="not running"):
        await protocol.exchange("127.0.0.1", 80, b"packet", 1, None)

    class Transport:
        def sendto(self, _data: bytes, _target: tuple[str, int]) -> None:
            return None

    protocol._transport = Transport()  # type: ignore[assignment]
    with pytest.raises(dna.DNAError, match="timeout"):
        await protocol.exchange("127.0.0.1", 80, b"packet", 0.001, None)


async def test_listener_lifecycle_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    loop = asyncio.get_running_loop()
    listener = UltraLocalUDPListener(loop, 0, lambda _update: None)
    await listener.stop()
    with pytest.raises(dna.DNAError, match="not running"):
        await listener.exchange("127.0.0.1", 80, b"packet", 1, None)

    class Transport:
        closed = False

        def get_extra_info(self, _name: str) -> None:
            return None

        def close(self) -> None:
            self.closed = True

    transport = Transport()
    monkeypatch.setattr(
        loop,
        "create_datagram_endpoint",
        AsyncMock(return_value=(transport, None)),
    )
    with pytest.raises(OSError, match="no socket address"):
        await listener.start()
    assert transport.closed


@pytest.mark.parametrize(
    "kwargs",
    [
        {"subscription_timeout": 0},
        {"subscription_timeout": 1, "renew_interval": 0},
        {"subscription_timeout": 1, "renew_interval": 1},
        {"position_ttl": 0},
        {"retry_interval": 0},
    ],
)
async def test_subscription_constructor_validation(kwargs: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        UltraPositionSubscription(AsyncMock(), session(), **kwargs)


async def test_subscription_idempotent_lifecycle_and_wait_without_timeout() -> None:
    client = AsyncMock()

    async def subscribe(
        _session: UltraSession,
        port: int,
        timeout: int,
        **_kwargs: object,
    ) -> UltraLocalUDPConfig:
        return UltraLocalUDPConfig("127.0.0.1", port, timeout)

    client.subscribe_local_udp_push.side_effect = subscribe
    subscription = UltraPositionSubscription(
        client,
        session(),
        subscription_timeout=1,
        renew_interval=0.5,
    )
    await subscription.start()
    await subscription.start()
    await subscription.wait_subscribed()
    await subscription.wait_confirmed()
    await subscription.stop()
    await subscription.stop()


@pytest.mark.parametrize(
    ("method", "arguments"),
    [
        ("get_radar_status", ()),
        ("set_radar_sensitivity", (1,)),
        ("set_radar_trigger_speed", (1,)),
        ("set_radar_install_mode", (1,)),
        ("set_radar_height", (100,)),
        ("set_radar_install_direction", (1,)),
        ("set_radar_z_range", (-1.0, 1.0)),
        ("set_radar_default_absence_delay", (60,)),
        ("set_radar_zone_absence_delay", (1, 60)),
    ],
)
async def test_radar_operations_require_running_subscription(
    method: str,
    arguments: tuple[object, ...],
) -> None:
    subscription = UltraPositionSubscription(AsyncMock(), session())
    with pytest.raises(UltraError, match="not running"):
        await getattr(subscription, method)(*arguments)


async def test_subscription_initial_failure_recovers() -> None:
    client = AsyncMock()
    current_session = session()
    calls = 0

    async def subscribe(
        _session: UltraSession,
        port: int,
        timeout: int,
        **_kwargs: object,
    ) -> UltraLocalUDPConfig:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise UltraError("initial failure")
        return UltraLocalUDPConfig("127.0.0.1", port, timeout)

    client.subscribe_local_udp_push.side_effect = subscribe
    subscription = UltraPositionSubscription(
        client,
        current_session,
        subscription_timeout=1,
        renew_interval=0.5,
        retry_interval=0.001,
    )
    await subscription.start()
    try:
        await subscription.wait_confirmed(1)
        assert calls == 2
        assert subscription.state.last_error is None
    finally:
        await subscription.stop()


async def test_position_expiry_ignores_old_update_and_replaces_timer() -> None:
    subscription = UltraPositionSubscription(AsyncMock(), session())
    old = emotion.parse_local_udp_position_update(
        b'{"detect_position":"[{\\"x\\":1,\\"y\\":2,\\"z\\":3}]"}',
        "127.0.0.1",
        datetime.now(UTC),
    )
    new = emotion.parse_local_udp_position_update(
        b'{"detect_position":"[{\\"x\\":2,\\"y\\":3,\\"z\\":4}]"}',
        "127.0.0.1",
        datetime.now(UTC),
    )
    subscription._handle_position(old)
    assert subscription.state.latest_update is None

    subscription._running = True
    subscription._handle_position(old)
    first_handle = subscription._expiry_handle
    subscription._handle_position(new)
    assert first_handle is not None and first_handle.cancelled()
    subscription._expire_position(old)
    assert not subscription.state.stale
    subscription._expire_position(new)
    assert subscription.state.stale
    subscription._running = False
