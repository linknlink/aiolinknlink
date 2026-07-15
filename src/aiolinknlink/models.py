"""Data models for the eMotion Ultra2 integration."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class UltraDevice:
    """Discovered Ultra2 device."""

    id: str
    ip: str
    port: int
    mac: str = ""
    did: str = ""
    pid: str = ""
    type_id: int = 0
    name: str = "eMotion Ultra2"
    model: str = "eMotion Ultra2"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class UltraSession:
    """Authenticated Ultra2 LAN session."""

    device: UltraDevice
    session_key: bytes | None = None
    auth_mac: str = ""
    auth_device_type: int = 0
    command_device_type: int = 0
    command_message_type: int = 0
    command_sequence: int = 0
    auth_status: str = "new"
    auth_error: str = ""
    last_auth_at: datetime | None = None
    last_seen: datetime | None = None


@dataclass(frozen=True, slots=True)
class UltraEnvironmentState:
    """Locally reported Ultra2 environmental and occupancy state."""

    device_id: str
    values: dict[str, int | float | bool]
    available_fields: frozenset[str]
    received_at: datetime


@dataclass(frozen=True, slots=True)
class UltraTargetPosition:
    """One radar target position in meters."""

    x: float
    y: float
    z: float

    @property
    def horizontal_distance(self) -> float:
        """Return the horizontal distance from the radar in meters."""
        return math.hypot(self.x, self.y)

    @property
    def distance(self) -> float:
        """Return the three-dimensional distance from the radar in meters."""
        return math.sqrt(self.x**2 + self.y**2 + self.z**2)


@dataclass(frozen=True, slots=True)
class UltraPositionUpdate:
    """A local UDP target-position update."""

    source_ip: str
    targets: tuple[UltraTargetPosition, ...]
    received_at: datetime

    @property
    def target_count(self) -> int:
        """Return the number of valid targets in this update."""
        return len(self.targets)

    @property
    def nearest_horizontal_distance(self) -> float | None:
        """Return the nearest horizontal target distance in meters."""
        if not self.targets:
            return None
        return min(target.horizontal_distance for target in self.targets)

    @property
    def nearest_distance(self) -> float | None:
        """Return the nearest three-dimensional target distance in meters."""
        if not self.targets:
            return None
        return min(target.distance for target in self.targets)


@dataclass(frozen=True, slots=True)
class UltraLocalUDPConfig:
    """Device-confirmed local UDP upload configuration."""

    ip: str
    port: int
    timeout: int


@dataclass(frozen=True, slots=True)
class UltraRadarStatus:
    """Device-read radar configuration that has been validated locally."""

    did: str
    sensitivity: int
    received_at: datetime
    trigger_speed: int | None = None
    install_mode: int | None = None
    height: int | None = None
    install_direction: int | None = None
    z_range: UltraRadarZRange | None = None
    default_absence_delay: int | None = None
    zone_absence_delays: tuple[
        int | None,
        int | None,
        int | None,
        int | None,
    ] = (None, None, None, None)


@dataclass(frozen=True, slots=True)
class UltraRadarZRange:
    """Device-read Ultra2 radar Z-axis detection range in meters."""

    minimum: float
    maximum: float


@dataclass(frozen=True, slots=True)
class UltraPositionSubscriptionState:
    """Current local UDP subscription and position state."""

    subscribed: bool
    stale: bool
    local_port: int
    confirmation_count: int
    latest_update: UltraPositionUpdate | None = None
    last_subscribed_at: datetime | None = None
    last_error: str | None = None
