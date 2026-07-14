"""Tests for the persistent Ultra2 local UDP transport."""

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
    UltraRadarStatus,
    UltraSession,
)
from aiolinknlink.local_udp import UltraLocalUDPListener
from aiolinknlink.protocol import dna


class _ExchangeServer(asyncio.DatagramProtocol):
    transport: asyncio.DatagramTransport

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.transport.sendto(dna.MAGIC + b"response", addr)


async def test_listener_demultiplexes_command_and_position() -> None:
    loop = asyncio.get_running_loop()
    updates = []
    update_received = asyncio.Event()

    def callback(update) -> None:
        updates.append(update)
        update_received.set()

    listener = UltraLocalUDPListener(
        loop,
        0,
        callback,
        expected_host="127.0.0.1",
    )
    await listener.start()
    server_transport, _ = await loop.create_datagram_endpoint(
        _ExchangeServer,
        local_addr=("127.0.0.1", 0),
    )
    sender_transport, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol,
        remote_addr=("127.0.0.1", listener.port),
    )
    try:
        server_port = server_transport.get_extra_info("sockname")[1]
        response = await listener.exchange(
            "127.0.0.1",
            server_port,
            dna.MAGIC + b"request",
            1,
            None,
        )
        sender_transport.sendto(b'{"detect_position":"[{\\"x\\":0.3,\\"y\\":0.4,\\"z\\":1.2}]"}')
        await asyncio.wait_for(update_received.wait(), 1)
    finally:
        sender_transport.close()
        server_transport.close()
        await listener.stop()

    assert response == dna.MAGIC + b"response"
    assert listener.port > 0
    assert updates[0].target_count == 1
    assert updates[0].nearest_distance == 1.3


async def test_position_subscription_renews_expires_and_stops() -> None:
    device = UltraDevice(
        id="e04b410244c7",
        ip="127.0.0.1",
        port=80,
        mac="e0:4b:41:02:44:c7",
        type_id=TYPE_ULTRA2,
    )
    session = UltraSession(device=device, session_key=b"0123456789abcdef")
    client = AsyncMock()

    async def reauthenticate(_session, *, protocol_mac, exchange):
        assert _session is session
        assert protocol_mac == "e0:4b:41:02:44:c9"
        assert exchange is not None
        _session.auth_mac = protocol_mac
        _session.session_key = b"0123456789abcdef"

    client.reauthenticate.side_effect = reauthenticate

    subscribe_count = 0

    async def subscribe(_session, port, timeout, *, exchange, try_all=True):
        nonlocal subscribe_count
        subscribe_count += 1
        assert _session is session
        assert port > 0
        assert timeout == 1
        assert exchange is not None
        if subscribe_count == 2:
            assert try_all is False
            raise UltraError("expired session")
        return UltraLocalUDPConfig(ip="127.0.0.1", port=port, timeout=timeout)

    client.subscribe_local_udp_push.side_effect = subscribe
    updates = []
    states = []
    update_received = asyncio.Event()

    def on_update(update) -> None:
        updates.append(update)
        update_received.set()

    subscription = UltraPositionSubscription(
        client,
        session,
        subscription_timeout=1,
        renew_interval=0.05,
        position_ttl=0.02,
        callback=on_update,
        status_callback=states.append,
    )
    await subscription.start()
    await subscription.wait_subscribed(1)
    await subscription.wait_confirmed(1)
    port = subscription.state.local_port
    loop = asyncio.get_running_loop()
    sender, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol,
        remote_addr=("127.0.0.1", port),
    )
    try:
        sender.sendto(b'{"detect_position":"[{\\"x\\":1,\\"y\\":2,\\"z\\":3}]"}')
        await asyncio.wait_for(update_received.wait(), 1)
        assert subscription.state.stale is False
        await asyncio.sleep(0.06)
        assert subscription.state.stale is True
        assert client.subscribe_local_udp_push.await_count >= 2
        assert subscription.state.confirmation_count >= 2
        assert client.reauthenticate.await_count == 2
    finally:
        sender.close()
        await subscription.stop()

    assert updates[0].target_count == 1
    assert states[-1].subscribed is False


async def test_position_subscription_retries_invalid_confirmation() -> None:
    """Port and timeout read-back mismatches retry without killing the task."""
    device = UltraDevice(
        id="e04b410244c7",
        ip="127.0.0.1",
        port=80,
        mac="e0:4b:41:02:44:c7",
        type_id=TYPE_ULTRA2,
    )
    session = UltraSession(device=device, session_key=b"0123456789abcdef")
    client = AsyncMock()

    async def reauthenticate(_session, *, protocol_mac, exchange):
        _session.auth_mac = protocol_mac
        _session.session_key = b"0123456789abcdef"

    client.reauthenticate.side_effect = reauthenticate
    subscribe_count = 0

    async def subscribe(_session, port, timeout, *, exchange, try_all=True):
        nonlocal subscribe_count
        subscribe_count += 1
        if subscribe_count == 1:
            return UltraLocalUDPConfig(ip="127.0.0.1", port=port + 1, timeout=timeout)
        if subscribe_count == 2:
            return UltraLocalUDPConfig(ip="127.0.0.1", port=port, timeout=timeout + 1)
        return UltraLocalUDPConfig(ip="127.0.0.1", port=port, timeout=timeout)

    client.subscribe_local_udp_push.side_effect = subscribe
    subscription = UltraPositionSubscription(
        client,
        session,
        subscription_timeout=1,
        renew_interval=0.5,
        retry_interval=0.01,
    )

    await subscription.start()
    try:
        await subscription.wait_confirmed(1)
        assert subscription.state.subscribed is True
        assert subscription.state.confirmation_count == 1
        assert subscription.state.last_error is None
        assert client.subscribe_local_udp_push.await_count == 3
        assert client.reauthenticate.await_count == 2
    finally:
        await subscription.stop()


async def test_position_subscription_isolates_callback_errors() -> None:
    """Consumer callback failures must not stop updates or renewals."""
    device = UltraDevice(
        id="e04b410244c7",
        ip="127.0.0.1",
        port=80,
        mac="e0:4b:41:02:44:c7",
        type_id=TYPE_ULTRA2,
    )
    session = UltraSession(
        device=device,
        session_key=b"0123456789abcdef",
        auth_mac="e0:4b:41:02:44:c9",
    )
    client = AsyncMock()

    async def subscribe(_session, port, timeout, *, exchange, try_all=True):
        return UltraLocalUDPConfig(ip="127.0.0.1", port=port, timeout=timeout)

    client.subscribe_local_udp_push.side_effect = subscribe

    def fail_callback(_value) -> None:
        raise RuntimeError("consumer failed")

    subscription = UltraPositionSubscription(
        client,
        session,
        subscription_timeout=1,
        renew_interval=0.05,
        position_ttl=0.5,
        callback=fail_callback,
        status_callback=fail_callback,
    )
    await subscription.start()
    await subscription.wait_confirmed(1)
    loop = asyncio.get_running_loop()
    sender, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol,
        remote_addr=("127.0.0.1", subscription.state.local_port),
    )
    try:
        sender.sendto(b'{"detect_position":"[{\\"x\\":1,\\"y\\":2,\\"z\\":3}]"}')
        await asyncio.sleep(0.08)
        assert subscription.state.stale is False
        assert subscription.state.confirmation_count >= 2
    finally:
        sender.close()
        await subscription.stop()


async def test_radar_configuration_reuses_subscription_socket() -> None:
    """Radar reads and writes are serialized through the persistent socket."""
    device = UltraDevice(
        id="e04b410244c7",
        ip="127.0.0.1",
        port=80,
        mac="e0:4b:41:02:44:c7",
        type_id=TYPE_ULTRA2,
    )
    session = UltraSession(
        device=device,
        session_key=b"0123456789abcdef",
        auth_mac="e0:4b:41:02:44:c9",
    )
    client = AsyncMock()

    async def subscribe(_session, port, timeout, *, exchange, try_all=True):
        return UltraLocalUDPConfig(ip="127.0.0.1", port=port, timeout=timeout)

    client.subscribe_local_udp_push.side_effect = subscribe
    radar_status = UltraRadarStatus(
        did="e04b410244c7dbac00000000dbac0001",
        sensitivity=2,
        received_at=datetime.now(UTC),
    )
    client.get_radar_status.return_value = radar_status
    client.set_radar_sensitivity.return_value = radar_status
    subscription = UltraPositionSubscription(
        client,
        session,
        subscription_timeout=1,
        renew_interval=0.5,
    )

    await subscription.start()
    try:
        await subscription.wait_confirmed(1)
        assert await subscription.get_radar_status() is radar_status
        assert await subscription.set_radar_sensitivity(2) is radar_status
        assert client.get_radar_status.call_args.kwargs["exchange"] is not None
        assert client.set_radar_sensitivity.call_args.kwargs["exchange"] is not None
    finally:
        await subscription.stop()

    with pytest.raises(UltraError, match="not running"):
        await subscription.get_radar_status()
