"""Supported LinknLink device models and local capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class DeviceModel(StrEnum):
    """Stable identifiers for supported LinknLink device models."""

    EMOTION_ULTRA2 = "emotion_ultra2"
    EMOTION_ULTRA1 = "emotion_ultra1"
    EMOTION_MAX1 = "emotion_max1"
    EMOTION_MAX2 = "emotion_max2"
    EMOTION_MAX3 = "emotion_max3"
    EHOME_HA = "ehome_ha"
    EREMOTE_HA = "eremote_ha"
    EMOTION_PRO = "emotion_pro"


class DeviceCapability(StrEnum):
    """Features that may be exposed for a device model."""

    ENVIRONMENT = "environment"
    TEMPERATURE = "temperature"
    HUMIDITY = "humidity"
    ILLUMINANCE = "illuminance"
    OCCUPANCY = "occupancy"
    TARGET_COUNT = "target_count"
    ZONES = "zones"
    DISTANCE = "distance"
    POSITION = "position"
    TARGET_SPEED = "target_speed"
    RADAR_CONFIGURATION = "radar_configuration"
    LOCAL_UDP = "local_udp"
    REMOTE = "remote"
    ABSENCE_DELAY = "absence_delay"


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    """Immutable model metadata selected by the DNA wire device type."""

    model: DeviceModel
    display_name: str
    type_ids: frozenset[int]
    pid: str
    capabilities: frozenset[DeviceCapability]


TYPE_ULTRA1: Final = 0x9CAC
TYPE_ULTRA2: Final = 0xD7AC
TYPE_ULTRA2_LAN: Final = 0xE3AC
TYPE_EMOTION_MAX1: Final = 0x9EAC
TYPE_EMOTION_MAX2: Final = 0xD6AC
TYPE_EMOTION_MAX3: Final = 0xDEAC
TYPE_EHOME_HA: Final = 0x85AC
TYPE_EREMOTE_HA: Final = 0x90AC
TYPE_EMOTION_PRO: Final = 0x6FAC
TYPE_LEGACY_RM_MONITOR: Final = 0x1B52

PID_ULTRA1: Final = "0000000000000000000000009cac0000"
PID_ULTRA2: Final = "000000000000000000000000d7ac0000"
PID_EMOTION_MAX1: Final = "0000000000000000000000009eac0000"
PID_EMOTION_MAX2: Final = "000000000000000000000000d6ac0000"
PID_EMOTION_MAX3: Final = "000000000000000000000000deac0000"
PID_EHOME_HA: Final = "00000000000000000000000085ac0000"
PID_EREMOTE_HA: Final = "00000000000000000000000090ac0000"
PID_EMOTION_PRO: Final = "0000000000000000000000006fac0000"

DISPLAY_MODEL_ULTRA2: Final = "eMotion Ultra2"
DISPLAY_MODEL_ULTRA1: Final = "eMotion Ultra (1st generation)"

_RADAR_CAPABILITIES = frozenset(
    {
        DeviceCapability.ENVIRONMENT,
        DeviceCapability.TEMPERATURE,
        DeviceCapability.HUMIDITY,
        DeviceCapability.ILLUMINANCE,
        DeviceCapability.OCCUPANCY,
        DeviceCapability.TARGET_COUNT,
        DeviceCapability.ZONES,
        DeviceCapability.DISTANCE,
        DeviceCapability.POSITION,
        DeviceCapability.RADAR_CONFIGURATION,
        DeviceCapability.LOCAL_UDP,
        DeviceCapability.ABSENCE_DELAY,
    }
)

DEVICE_PROFILES: Final[tuple[DeviceProfile, ...]] = (
    DeviceProfile(
        DeviceModel.EMOTION_ULTRA2,
        DISPLAY_MODEL_ULTRA2,
        frozenset({TYPE_ULTRA2, TYPE_ULTRA2_LAN}),
        PID_ULTRA2,
        _RADAR_CAPABILITIES,
    ),
    DeviceProfile(
        DeviceModel.EMOTION_ULTRA1,
        DISPLAY_MODEL_ULTRA1,
        frozenset({TYPE_ULTRA1}),
        PID_ULTRA1,
        _RADAR_CAPABILITIES,
    ),
    DeviceProfile(
        DeviceModel.EMOTION_MAX1,
        "eMotion Max",
        frozenset({TYPE_EMOTION_MAX1}),
        PID_EMOTION_MAX1,
        _RADAR_CAPABILITIES - {DeviceCapability.TARGET_SPEED},
    ),
    DeviceProfile(
        DeviceModel.EMOTION_MAX2,
        "eMotion Max 2",
        frozenset({TYPE_EMOTION_MAX2}),
        PID_EMOTION_MAX2,
        _RADAR_CAPABILITIES,
    ),
    DeviceProfile(
        DeviceModel.EMOTION_MAX3,
        "eMotion Max 3",
        frozenset({TYPE_EMOTION_MAX3}),
        PID_EMOTION_MAX3,
        _RADAR_CAPABILITIES,
    ),
    DeviceProfile(
        DeviceModel.EHOME_HA, "eHomeHA", frozenset({TYPE_EHOME_HA}), PID_EHOME_HA, frozenset({DeviceCapability.REMOTE})
    ),
    DeviceProfile(
        DeviceModel.EREMOTE_HA,
        "eRemoteHA",
        frozenset({TYPE_EREMOTE_HA}),
        PID_EREMOTE_HA,
        frozenset({DeviceCapability.REMOTE}),
    ),
    DeviceProfile(
        DeviceModel.EMOTION_PRO,
        "eMotion Pro",
        frozenset({TYPE_EMOTION_PRO}),
        PID_EMOTION_PRO,
        frozenset(
            {
                DeviceCapability.ENVIRONMENT,
                DeviceCapability.TEMPERATURE,
                DeviceCapability.HUMIDITY,
                DeviceCapability.OCCUPANCY,
                DeviceCapability.ABSENCE_DELAY,
            }
        ),
    ),
)

_BY_TYPE = {type_id: profile for profile in DEVICE_PROFILES for type_id in profile.type_ids}
_BY_PID = {profile.pid: profile for profile in DEVICE_PROFILES}
PROFILES_BY_TYPE: Final[Mapping[int, DeviceProfile]] = MappingProxyType(_BY_TYPE)
PROFILES_BY_PID: Final[Mapping[str, DeviceProfile]] = MappingProxyType(_BY_PID)


def get_device_profile(type_id: int = 0, pid: str = "") -> DeviceProfile | None:
    """Return immutable metadata for a wire type or PID."""
    if profile := PROFILES_BY_TYPE.get(type_id):
        return profile
    return PROFILES_BY_PID.get(pid.strip().lower())
