"""Data models for eMotion Ultra integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class UltraDevice:
    """Discovered Ultra device."""

    id: str
    ip: str
    port: int
    mac: str = ""
    did: str = ""
    pid: str = ""
    type_id: int = 0
    name: str = "eMotion Ultra"
    model: str = "eMotion Ultra"
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class UltraSession:
    """Authenticated Ultra LAN session."""

    device: UltraDevice
    session_key: bytes | None = None
    auth_device_type: int = 0
    command_sequence: int = 0
    auth_status: str = "new"
    auth_error: str = ""
    last_auth_at: datetime | None = None
    last_seen: datetime | None = None


@dataclass(slots=True)
class UltraSubDeviceState:
    """State for an Ultra child/subdevice."""

    did: str
    pid: str = ""
    name: str = ""
    type: str = ""
    fields: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime | None = None


@dataclass(slots=True)
class UltraState:
    """Aggregated Ultra device state."""

    device_id: str
    online: bool
    values: dict[str, Any] = field(default_factory=dict)
    children: dict[str, UltraSubDeviceState] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class UltraEntityInfo:
    """HA entity mapping metadata."""

    unique_id: str
    name: str
    domain: str
    attr: str
    device_id: str
    device_class: str | None = None
    native_unit_of_measurement: str | None = None
    state_class: str | None = None
    writable: bool = False
    sub_did: str = ""
    extra: dict[str, Any] = field(default_factory=dict)
