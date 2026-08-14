"""Persistent local status push subscription for LinknLink iBG gateways."""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import TypeAlias

from .ibg import IbgClient, IbgError, normalize_subdevice_state
from .models import IbgSession, IbgSubDevice, IbgSubDeviceState
from .protocol import dna, gateway

_LOGGER = logging.getLogger(__name__)

MESSAGE_TYPE_HEARTBEAT = 0x69
MESSAGE_TYPE_HEARTBEAT_RESPONSE = 0x03ED
MESSAGE_TYPE_COMMAND_RESPONSE = 0x03EE
CMD_STATUS_RESPONSE_ACK = 3819
DEFAULT_HEARTBEAT_INTERVAL = 3.0
DEFAULT_RETRY_INTERVAL = 3.0
MAX_RETRY_INTERVAL = 30.0

StateCallback: TypeAlias = Callable[[IbgSubDeviceState], None]


class IbgPushProtocol(asyncio.DatagramProtocol):
    """Demultiplex iBG command replies and unsolicited status pushes."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        session: IbgSession,
        callback: StateCallback,
        devices: dict[str, IbgSubDevice],
    ) -> None:
        self._loop = loop
        self.session = session
        self._callback = callback
        self._devices = devices
        self._transport: asyncio.DatagramTransport | None = None
        self._exchange_lock = asyncio.Lock()
        self._pending_response: asyncio.Future[bytes] | None = None
        self._pending_host: str | None = None
        self._pending_accept: dna.PacketAcceptor | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Store the datagram transport."""
        self._transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Route a command reply or decode and acknowledge a status push."""
        if addr[0] != self.session.device.ip:
            return
        pending = self._pending_response
        if (
            pending is not None
            and not pending.done()
            and addr[0] == self._pending_host
            and (self._pending_accept is None or self._pending_accept(data))
        ):
            pending.set_result(bytes(data))
            return
        self._handle_push(data)

    def error_received(self, exc: Exception) -> None:
        """Fail a pending exchange when UDP reports an error."""
        pending = self._pending_response
        if pending is not None and not pending.done():
            pending.set_exception(exc)

    def connection_lost(self, exc: Exception | None) -> None:
        """Fail a pending exchange when the socket closes."""
        pending = self._pending_response
        if pending is not None and not pending.done():
            pending.set_exception(exc or ConnectionError("iBG push socket closed"))

    def update_devices(self, devices: dict[str, IbgSubDevice]) -> None:
        """Replace the set of reviewed subdevices accepted from pushes."""
        self._devices = devices

    async def exchange(
        self,
        target_ip: str,
        target_port: int,
        packet: bytes,
        timeout: float,
        accept: dna.PacketAcceptor | None,
    ) -> bytes:
        """Exchange one DNA packet through the persistent push socket."""
        if timeout <= 0:
            raise dna.DNAError("timeout must be greater than zero")
        async with self._exchange_lock:
            if self._transport is None:
                raise dna.DNAError("iBG push listener is not running")
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

    def _handle_push(self, data: bytes) -> None:
        key = self.session.session_key
        if key is None:
            return
        try:
            header, body = dna.parse_blc_packet(data)
            if header.message_type != MESSAGE_TYPE_COMMAND_RESPONSE or header.status != 0:
                return
            _aes, plain = dna.parse_blc_encrypted_payload(body, key)
            frame = gateway.parse_gateway_frame(plain)
            if frame.command_type != gateway.CMD_STATUS_RESPONSE:
                return
            self._acknowledge(header.sequence)
            did = frame.payload.get("did")
            pid = frame.payload.get("pid")
            if not isinstance(did, str):
                return
            device = self._devices.get(did.lower())
            if device is None:
                return
            if pid is not None and (not isinstance(pid, str) or pid.lower() != device.pid.lower()):
                return
            state = IbgSubDeviceState(
                device=device,
                values=normalize_subdevice_state(device.pid, frame.payload),
                received_at=datetime.now(UTC),
            )
            self.session.last_seen = state.received_at
            self._callback(state)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Ignored invalid iBG status push from %s: %s", self.session.device.ip, err)

    def _acknowledge(self, sequence: int) -> None:
        transport = self._transport
        key = self.session.session_key
        if transport is None or key is None:
            return
        payload = gateway.build_gateway_frame(CMD_STATUS_RESPONSE_ACK, {})
        header = dna.NetworkHeader(
            device_type=self.session.device.type_id,
            message_type=dna.MESSAGE_TYPE_COMMAND,
            sequence=sequence,
            mac=dna.mac_bytes(self.session.device.mac),
            payload_checksum=dna.payload_checksum(payload),
        )
        packet = dna.build_blc_packet(header, dna.build_blc_encrypted_payload(payload, key))
        transport.sendto(packet, (self.session.device.ip, self.session.device.port or dna.DEFAULT_PORT))


class IbgPushSubscription:
    """Keep an iBG local terminal registered and receive status pushes."""

    def __init__(
        self,
        client: IbgClient,
        session: IbgSession,
        devices: list[IbgSubDevice] | tuple[IbgSubDevice, ...],
        callback: StateCallback,
        *,
        port: int = 0,
        heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
        retry_interval: float = DEFAULT_RETRY_INTERVAL,
    ) -> None:
        if heartbeat_interval <= 0:
            raise ValueError("heartbeat_interval must be greater than zero")
        if retry_interval <= 0:
            raise ValueError("retry_interval must be greater than zero")
        self._loop = asyncio.get_running_loop()
        self._client = client
        self.session = session
        self._devices = {device.did: device for device in devices}
        self._callback = callback
        self._requested_port = port
        self._heartbeat_interval = heartbeat_interval
        self._retry_interval = retry_interval
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: IbgPushProtocol | None = None
        self._task: asyncio.Task[None] | None = None
        self._running = False
        self._subscribed = False

    @property
    def subscribed(self) -> bool:
        """Return whether the latest local heartbeat was confirmed."""
        return self._subscribed

    @property
    def local_port(self) -> int:
        """Return the bound local UDP port, or zero before start."""
        if self._transport is None:
            return 0
        socket_name = self._transport.get_extra_info("sockname")
        return int(socket_name[1]) if socket_name is not None else 0

    async def start(self) -> None:
        """Open the local UDP listener and start maintaining heartbeats."""
        if self._running:
            return
        protocol = IbgPushProtocol(self._loop, self.session, self._callback, self._devices)
        transport, _ = await self._loop.create_datagram_endpoint(
            lambda: protocol,
            local_addr=("0.0.0.0", self._requested_port),
            family=socket.AF_INET,
        )
        self._transport = transport
        self._protocol = protocol
        self._running = True
        self._task = self._loop.create_task(
            self._heartbeat_loop(),
            name=f"iBG push heartbeat {self.session.device.id}",
        )

    async def stop(self) -> None:
        """Stop heartbeats and release the local UDP listener."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None
            self._protocol = None
            await asyncio.sleep(0)
        self._subscribed = False

    def update_devices(self, devices: list[IbgSubDevice] | tuple[IbgSubDevice, ...]) -> None:
        """Update the reviewed subdevice identities accepted from pushes."""
        self._devices = {device.did: device for device in devices}
        if self._protocol is not None:
            self._protocol.update_devices(self._devices)

    def update_session(self, session: IbgSession) -> None:
        """Use a session refreshed by the polling coordinator."""
        self.session = session
        if self._protocol is not None:
            self._protocol.session = session

    async def _heartbeat_loop(self) -> None:
        delay = 0.0
        failure_count = 0
        while self._running:
            if delay:
                await asyncio.sleep(delay)
            try:
                await self._heartbeat()
            except asyncio.CancelledError:
                raise
            except (OSError, IbgError, dna.DNAError) as err:
                self._subscribed = False
                failure_count += 1
                _LOGGER.debug("iBG local heartbeat failed for %s: %s", self.session.device.ip, err)
                if failure_count >= 2:
                    try:
                        await self._reauthenticate()
                    except (OSError, IbgError, dna.DNAError) as auth_err:
                        _LOGGER.debug(
                            "iBG local push reauthentication failed for %s: %s",
                            self.session.device.ip,
                            auth_err,
                        )
                delay = min(self._retry_interval * (2 ** min(failure_count - 1, 4)), MAX_RETRY_INTERVAL)
                continue
            failure_count = 0
            self._subscribed = True
            delay = self._heartbeat_interval

    async def _heartbeat(self) -> None:
        protocol = self._protocol
        key = self.session.session_key
        if protocol is None or key is None:
            raise IbgError("iBG push session is not authenticated")

        def accept(candidate: bytes) -> bool:
            return (
                len(candidate) >= dna.BLC_NETWORK_HEADER_SIZE
                and int.from_bytes(candidate[0x26:0x28], "little") == MESSAGE_TYPE_HEARTBEAT_RESPONSE
            )

        sequence = (self.session.command_sequence + 1) & 0xFFFF or 1
        self.session.command_sequence = sequence
        response = await dna.send_encrypted(
            self.session.device.ip,
            self.session.device.port or dna.DEFAULT_PORT,
            dna.NetworkHeader(
                device_type=self.session.device.type_id,
                message_type=MESSAGE_TYPE_HEARTBEAT,
                sequence=sequence,
                mac=dna.mac_bytes(self.session.device.mac),
            ),
            gateway.build_gateway_frame(0, {}),
            key,
            timeout=self._client.command_timeout,
            accept=accept,
            exchange=protocol.exchange,
            compact=True,
        )
        if not _is_valid_heartbeat_response(response):
            raise IbgError("invalid iBG heartbeat response")
        self.session.last_seen = datetime.now(UTC)

    async def _reauthenticate(self) -> None:
        protocol = self._protocol
        if protocol is None:
            raise IbgError("iBG push listener is not running")
        session = await self._client.connect(self.session.device, exchange=protocol.exchange)
        self.update_session(session)


def _is_valid_heartbeat_response(response: bytes) -> bool:
    """Validate firmware heartbeat frames whose length includes zero padding."""
    if len(response) < gateway.UART_HEADER_SIZE:
        return False
    magic, _checksum, command_type, payload_length, version = struct.unpack_from("<IHHHH", response, 0)
    expected_length = gateway.UART_HEADER_SIZE + payload_length
    missing_padding = expected_length - len(response)
    if (
        magic != gateway.UART_MAGIC
        or command_type != 0
        or version != gateway.UART_VERSION
        or missing_padding < 0
        or missing_padding > 15
    ):
        return False
    padded = response + b"\x00" * missing_padding
    return dna.verify_checksum_le(padded, 4) and padded[gateway.UART_HEADER_SIZE :].rstrip(b"\x00") == b"{}"
