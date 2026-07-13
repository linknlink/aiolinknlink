"""Local UDP push listener for Ultra position updates."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from .protocol import emotion

_LOGGER = logging.getLogger(__name__)


class UltraLocalUDPProtocol(asyncio.DatagramProtocol):
    """Datagram protocol parsing Ultra position push payloads."""

    def __init__(self, callback: Callable[[str, dict[str, Any]], None]) -> None:
        self._callback = callback

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Handle incoming datagram."""
        try:
            payload = emotion.parse_local_udp_position_payload(data)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("Ignored Ultra local UDP payload from %s: %s", addr, err)
            return
        self._callback(addr[0], payload)


class UltraLocalUDPListener:
    """Manage an asyncio UDP endpoint."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        port: int,
        callback: Callable[[str, dict[str, Any]], None],
    ) -> None:
        self._loop = loop
        self.port = port
        self._callback = callback
        self._transport: asyncio.DatagramTransport | None = None

    async def start(self) -> None:
        """Start listener."""
        if self._transport is not None:
            return
        transport, _ = await self._loop.create_datagram_endpoint(
            lambda: UltraLocalUDPProtocol(self._callback),
            local_addr=("0.0.0.0", self.port),
            family=0,
        )
        self._transport = transport
        _LOGGER.debug("Ultra local UDP listener started on port %s", self.port)

    async def stop(self) -> None:
        """Stop listener."""
        if self._transport is None:
            return
        self._transport.close()
        self._transport = None
