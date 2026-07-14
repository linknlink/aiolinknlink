"""Local UDP position subscription for eMotion Ultra2."""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import TypeAlias

from .client import (
    UltraClient,
    UltraError,
    UltraProtocolError,
    derive_ultra2_protocol_mac,
)
from .models import (
    UltraPositionSubscriptionState,
    UltraPositionUpdate,
    UltraRadarStatus,
    UltraSession,
)
from .protocol import dna, emotion

_LOGGER = logging.getLogger(__name__)

DEFAULT_SUBSCRIPTION_TIMEOUT = 60
DEFAULT_RENEW_INTERVAL = 40.0
DEFAULT_POSITION_TTL = 30.0
DEFAULT_RETRY_INTERVAL = 5.0
MAX_RETRY_INTERVAL = 60.0

PositionCallback: TypeAlias = Callable[[UltraPositionUpdate], None]
StatusCallback: TypeAlias = Callable[[UltraPositionSubscriptionState], None]


class UltraLocalUDPProtocol(asyncio.DatagramProtocol):
    """Demultiplex DNA responses and Ultra2 position updates on one socket."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        callback: PositionCallback,
        expected_host: str | None,
    ) -> None:
        self._loop = loop
        self._callback = callback
        self._expected_host = expected_host
        self._transport: asyncio.DatagramTransport | None = None
        self._exchange_lock = asyncio.Lock()
        self._pending_response: asyncio.Future[bytes] | None = None
        self._pending_host: str | None = None
        self._pending_accept: dna.PacketAcceptor | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Store the datagram transport."""
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Handle a DNA response or position update."""
        if data.startswith(dna.MAGIC):
            pending = self._pending_response
            if (
                pending is not None
                and not pending.done()
                and addr[0] == self._pending_host
                and (self._pending_accept is None or self._pending_accept(data))
            ):
                pending.set_result(bytes(data))
            return

        if self._expected_host is not None and addr[0] != self._expected_host:
            return
        try:
            update = emotion.parse_local_udp_position_update(data, addr[0])
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Ignored Ultra2 local UDP payload from %s: %s", addr, err)
            return
        try:
            self._callback(update)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unhandled Ultra2 local UDP position callback error")

    def error_received(self, exc: Exception) -> None:
        """Fail a pending command when the UDP transport reports an error."""
        pending = self._pending_response
        if pending is not None and not pending.done():
            pending.set_exception(exc)

    def connection_lost(self, exc: Exception | None) -> None:
        """Fail a pending command when the socket closes."""
        pending = self._pending_response
        if pending is not None and not pending.done():
            pending.set_exception(exc or ConnectionError("local UDP socket closed"))

    async def exchange(
        self,
        target_ip: str,
        target_port: int,
        packet: bytes,
        timeout: float,
        accept: dna.PacketAcceptor | None,
    ) -> bytes:
        """Send a DNA packet and await its response on this listener socket."""
        if timeout <= 0:
            raise dna.DNAError("timeout must be greater than zero")
        async with self._exchange_lock:
            if self._transport is None:
                raise dna.DNAError("local UDP listener is not running")
            pending = self._loop.create_future()
            self._pending_response = pending
            self._pending_host = target_ip
            self._pending_accept = accept
            try:
                self._transport.sendto(packet, (target_ip, target_port))
                try:
                    return await asyncio.wait_for(pending, timeout)
                except TimeoutError as err:
                    raise dna.DNAError(f"timeout waiting for DNA response from {target_ip}:{target_port}") from err
            finally:
                if not pending.done():
                    pending.cancel()
                self._pending_response = None
                self._pending_host = None
                self._pending_accept = None


class UltraLocalUDPListener:
    """Manage a reusable asyncio UDP endpoint."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        port: int,
        callback: PositionCallback,
        *,
        expected_host: str | None = None,
        local_host: str = "0.0.0.0",
    ) -> None:
        self._loop = loop
        self._requested_port = port
        self.port = port
        self._callback = callback
        self._expected_host = expected_host
        self._local_host = local_host
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: UltraLocalUDPProtocol | None = None

    async def start(self) -> None:
        """Start the listener."""
        if self._transport is not None:
            return
        protocol = UltraLocalUDPProtocol(self._loop, self._callback, self._expected_host)
        transport, _ = await self._loop.create_datagram_endpoint(
            lambda: protocol,
            local_addr=(self._local_host, self._requested_port),
            family=socket.AF_INET,
        )
        self._transport = transport
        self._protocol = protocol
        socket_name = transport.get_extra_info("sockname")
        if socket_name is None:
            transport.close()
            self._transport = None
            self._protocol = None
            raise OSError("local UDP listener has no socket address")
        self.port = int(socket_name[1])
        _LOGGER.debug("Ultra2 local UDP listener started on port %s", self.port)

    async def stop(self) -> None:
        """Stop the listener and release its socket."""
        if self._transport is None:
            return
        self._transport.close()
        self._transport = None
        self._protocol = None
        await asyncio.sleep(0)

    async def exchange(
        self,
        target_ip: str,
        target_port: int,
        packet: bytes,
        timeout: float,
        accept: dna.PacketAcceptor | None,
    ) -> bytes:
        """Exchange a DNA packet through the listener socket."""
        if self._protocol is None:
            raise dna.DNAError("local UDP listener is not running")
        return await self._protocol.exchange(target_ip, target_port, packet, timeout, accept)


class UltraPositionSubscription:
    """Maintain one Ultra2 local position subscription."""

    def __init__(
        self,
        client: UltraClient,
        session: UltraSession,
        *,
        port: int = 0,
        subscription_timeout: int = DEFAULT_SUBSCRIPTION_TIMEOUT,
        renew_interval: float = DEFAULT_RENEW_INTERVAL,
        position_ttl: float = DEFAULT_POSITION_TTL,
        retry_interval: float = DEFAULT_RETRY_INTERVAL,
        callback: PositionCallback | None = None,
        status_callback: StatusCallback | None = None,
    ) -> None:
        if subscription_timeout <= 0:
            raise ValueError("subscription_timeout must be greater than zero")
        if renew_interval <= 0 or renew_interval >= subscription_timeout:
            raise ValueError("renew_interval must be greater than zero and less than subscription_timeout")
        if position_ttl <= 0:
            raise ValueError("position_ttl must be greater than zero")
        if retry_interval <= 0:
            raise ValueError("retry_interval must be greater than zero")

        self._loop = asyncio.get_running_loop()
        self._client = client
        self._session = session
        self._subscription_timeout = subscription_timeout
        self._renew_interval = renew_interval
        self._position_ttl = position_ttl
        self._retry_interval = retry_interval
        self._callback = callback
        self._status_callback = status_callback
        self._listener = UltraLocalUDPListener(
            self._loop,
            port,
            self._handle_position,
            expected_host=session.device.ip,
        )
        self._operation_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._expiry_handle: asyncio.TimerHandle | None = None
        self._subscribed_event = asyncio.Event()
        self._confirmed_event = asyncio.Event()
        self._running = False
        self._subscribed = False
        self._confirmation_count = 0
        self._stale = True
        self._latest_update: UltraPositionUpdate | None = None
        self._last_subscribed_at: datetime | None = None
        self._last_error: str | None = None
        self._protocol_mac = derive_ultra2_protocol_mac(session.device.mac)

    @property
    def state(self) -> UltraPositionSubscriptionState:
        """Return an immutable snapshot of the subscription state."""
        return UltraPositionSubscriptionState(
            subscribed=self._subscribed,
            stale=self._stale,
            local_port=self._listener.port,
            confirmation_count=self._confirmation_count,
            latest_update=self._latest_update,
            last_subscribed_at=self._last_subscribed_at,
            last_error=self._last_error,
        )

    async def start(self) -> None:
        """Start listening and maintain the subscription in the background."""
        if self._running:
            return
        await self._listener.start()
        self._running = True
        self._task = self._loop.create_task(
            self._subscription_loop(),
            name=f"Ultra2 position subscription {self._session.device.id}",
        )
        self._notify_status()

    async def stop(self) -> None:
        """Stop renewal, expiry tracking, and the UDP listener."""
        if not self._running:
            return
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._expiry_handle is not None:
            self._expiry_handle.cancel()
            self._expiry_handle = None
        await self._listener.stop()
        self._subscribed = False
        self._notify_status()

    async def wait_subscribed(self, timeout: float | None = None) -> None:
        """Wait until the device confirms a subscription or sends a position."""
        if timeout is None:
            await self._subscribed_event.wait()
            return
        await asyncio.wait_for(self._subscribed_event.wait(), timeout)

    async def wait_confirmed(self, timeout: float | None = None) -> None:
        """Wait until the device confirms at least one subscription command."""
        if timeout is None:
            await self._confirmed_event.wait()
            return
        await asyncio.wait_for(self._confirmed_event.wait(), timeout)

    async def get_radar_status(self) -> UltraRadarStatus:
        """Read radar configuration through the subscription's UDP socket."""
        if not self._running:
            raise UltraError("position subscription is not running")
        async with self._operation_lock:
            return await self._client.get_radar_status(
                self._session,
                exchange=self._listener.exchange,
            )

    async def set_radar_sensitivity(self, sensitivity: int) -> UltraRadarStatus:
        """Set and read back radar sensitivity on the shared UDP socket."""
        if not self._running:
            raise UltraError("position subscription is not running")
        async with self._operation_lock:
            return await self._client.set_radar_sensitivity(
                self._session,
                sensitivity,
                exchange=self._listener.exchange,
            )

    async def set_radar_trigger_speed(self, trigger_speed: int) -> UltraRadarStatus:
        """Set and read back radar trigger speed on the shared UDP socket."""
        return await self._run_radar_operation(
            self._client.set_radar_trigger_speed,
            trigger_speed,
        )

    async def set_radar_install_mode(self, install_mode: int) -> UltraRadarStatus:
        """Set and read back radar installation mode on the shared UDP socket."""
        return await self._run_radar_operation(
            self._client.set_radar_install_mode,
            install_mode,
        )

    async def set_radar_height(self, height: int) -> UltraRadarStatus:
        """Set and read back installation height on the shared UDP socket."""
        return await self._run_radar_operation(self._client.set_radar_height, height)

    async def set_radar_install_direction(self, install_direction: int) -> UltraRadarStatus:
        """Set and read back cable orientation on the shared UDP socket."""
        return await self._run_radar_operation(
            self._client.set_radar_install_direction,
            install_direction,
        )

    async def set_radar_z_range(self, minimum: float, maximum: float) -> UltraRadarStatus:
        """Set and read back the Z-axis detection range on the shared UDP socket."""
        return await self._run_radar_operation(
            self._client.set_radar_z_range,
            minimum,
            maximum,
        )

    async def set_radar_default_absence_delay(self, seconds: int) -> UltraRadarStatus:
        """Set and read back the default absence delay on the shared UDP socket."""
        return await self._run_radar_operation(
            self._client.set_radar_default_absence_delay,
            seconds,
        )

    async def set_radar_zone_absence_delay(
        self,
        zone: int,
        seconds: int,
    ) -> UltraRadarStatus:
        """Set and read back one zone's absence delay on the shared UDP socket."""
        return await self._run_radar_operation(
            self._client.set_radar_zone_absence_delay,
            zone,
            seconds,
        )

    async def _run_radar_operation(
        self,
        operation: Callable[..., Awaitable[UltraRadarStatus]],
        *args: object,
    ) -> UltraRadarStatus:
        """Serialize one radar operation through the persistent UDP socket."""
        if not self._running:
            raise UltraError("position subscription is not running")
        async with self._operation_lock:
            return await operation(
                self._session,
                *args,
                exchange=self._listener.exchange,
            )

    async def _subscription_loop(self) -> None:
        failure_count = 0
        delay = 0.0
        while self._running:
            if delay:
                await asyncio.sleep(delay)
            try:
                await self._renew_subscription()
            except asyncio.CancelledError:
                raise
            except (OSError, UltraError) as err:
                failure_count += 1
                self._subscribed = False
                self._last_error = str(err) or type(err).__name__
                if failure_count >= 2:
                    self._session.session_key = None
                self._notify_status()
                delay = min(
                    self._retry_interval if delay == 0 else delay * 2,
                    MAX_RETRY_INTERVAL,
                )
                continue

            failure_count = 0
            self._subscribed = True
            self._confirmation_count += 1
            self._last_subscribed_at = datetime.now(UTC)
            self._last_error = None
            self._subscribed_event.set()
            self._confirmed_event.set()
            self._notify_status()
            delay = self._renew_interval

    async def _renew_subscription(self) -> None:
        """Authenticate and renew while excluding configuration operations."""
        async with self._operation_lock:
            if self._session.auth_mac != self._protocol_mac:
                self._session.session_key = None
            if not self._session.session_key:
                await self._client.reauthenticate(
                    self._session,
                    protocol_mac=self._protocol_mac,
                    exchange=self._listener.exchange,
                )
            try:
                confirmed = await self._client.subscribe_local_udp_push(
                    self._session,
                    self._listener.port,
                    self._subscription_timeout,
                    try_all=self._confirmation_count == 0,
                    exchange=self._listener.exchange,
                )
            except UltraError:
                if self._confirmation_count == 0:
                    raise
                self._session.session_key = None
                await self._client.reauthenticate(
                    self._session,
                    protocol_mac=self._protocol_mac,
                    exchange=self._listener.exchange,
                )
                confirmed = await self._client.subscribe_local_udp_push(
                    self._session,
                    self._listener.port,
                    self._subscription_timeout,
                    exchange=self._listener.exchange,
                )
            if confirmed.port != self._listener.port:
                raise UltraProtocolError(
                    f"device confirmed a different local UDP port ({confirmed.port}, expected {self._listener.port})"
                )
            if confirmed.timeout != self._subscription_timeout:
                raise UltraProtocolError(
                    "device confirmed a different subscription timeout "
                    f"({confirmed.timeout}, expected {self._subscription_timeout})"
                )

    def _handle_position(self, update: UltraPositionUpdate) -> None:
        if not self._running:
            return
        self._latest_update = update
        self._stale = False
        self._subscribed = True
        self._last_error = None
        self._subscribed_event.set()
        if self._expiry_handle is not None:
            self._expiry_handle.cancel()
        self._expiry_handle = self._loop.call_later(
            self._position_ttl,
            self._expire_position,
            update,
        )
        if self._callback is not None:
            try:
                self._callback(update)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unhandled Ultra2 position callback error")
        self._notify_status()

    def _expire_position(self, update: UltraPositionUpdate) -> None:
        self._expiry_handle = None
        if self._latest_update is not update or self._stale:
            return
        self._stale = True
        self._notify_status()

    def _notify_status(self) -> None:
        if self._status_callback is not None:
            try:
                self._status_callback(self.state)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unhandled Ultra2 position status callback error")
