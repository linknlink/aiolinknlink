"""Device profiles and runtime capabilities for local LinknLink devices."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType


class DeviceModel(StrEnum):
    """Stable model identifiers used by protocol and HA adapters."""

    EMOTION_ULTRA1 = "emotion_ultra1"
    EMOTION_ULTRA2 = "emotion_ultra2"
    EMOTION_MAX1 = "emotion_max1"
    EMOTION_MAX2 = "emotion_max2"
    EMOTION_MAX3 = "emotion_max3"
    EHOME_HA = "ehome_ha"
    EREMOTE_HA = "eremote_ha"
    EMOTION_PRO = "emotion_pro"
    EMOTION_PRO_RADAR = "emotion_pro_radar"


class DeviceCapability(StrEnum):
    """Features that may be exposed by a device."""

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
    """Static model metadata selected by DNA type or PID."""

    model: DeviceModel
    display_name: str
    type_ids: frozenset[int]
    pid: str
    capabilities: frozenset[DeviceCapability]


TYPE_ULTRA1 = 0x9CAC
TYPE_ULTRA2 = 0xD7AC
TYPE_ULTRA2_LAN = 0xE3AC
TYPE_EMOTION_MAX1 = 0x9EAC
TYPE_EMOTION_MAX2 = 0xD6AC
TYPE_EMOTION_MAX3 = 0xDEAC
TYPE_EHOME_HA = 0x85AC
TYPE_EREMOTE_HA = 0x90AC

PID_ULTRA1 = "0000000000000000000000009cac0000"
PID_ULTRA2 = "000000000000000000000000d7ac0000"
PID_EMOTION_MAX1 = "0000000000000000000000009eac0000"
PID_EMOTION_MAX2 = "000000000000000000000000d6ac0000"
PID_EMOTION_MAX3 = "000000000000000000000000deac0000"
PID_EHOME_HA = "00000000000000000000000085ac0000"
PID_EREMOTE_HA = "00000000000000000000000090ac0000"

_RADAR_CAPABILITIES = frozenset(
    {
        DeviceCapability.ENVIRONMENT,
        DeviceCapability.TEMPERATURE,
        DeviceCapability.HUMIDITY,
        DeviceCapability.ILLUMINANCE,
        DeviceCapability.OCCUPANCY,
        DeviceCapability.TARGET_COUNT,
        DeviceCapability.ZONES,
        DeviceCapability.RADAR_CONFIGURATION,
        DeviceCapability.ABSENCE_DELAY,
    }
)

_ULTRA1_CAPABILITIES = _RADAR_CAPABILITIES
_ULTRA2_CAPABILITIES = _RADAR_CAPABILITIES | {
    DeviceCapability.DISTANCE,
    DeviceCapability.POSITION,
    DeviceCapability.TARGET_SPEED,
    DeviceCapability.LOCAL_UDP,
}
_MAX1_CAPABILITIES = _RADAR_CAPABILITIES - {DeviceCapability.TARGET_SPEED}
_MAX2_CAPABILITIES = _ULTRA2_CAPABILITIES
_MAX3_CAPABILITIES = _ULTRA2_CAPABILITIES

DEVICE_PROFILES: tuple[DeviceProfile, ...] = (
    DeviceProfile(DeviceModel.EMOTION_ULTRA1, "eMotion Ultra", frozenset({TYPE_ULTRA1}), PID_ULTRA1, _ULTRA1_CAPABILITIES),
    DeviceProfile(
        DeviceModel.EMOTION_ULTRA2,
        "eMotion Ultra2",
        frozenset({TYPE_ULTRA2, TYPE_ULTRA2_LAN}),
        PID_ULTRA2,
        _ULTRA2_CAPABILITIES,
    ),
    DeviceProfile(DeviceModel.EMOTION_MAX1, "eMotion Max", frozenset({TYPE_EMOTION_MAX1}), PID_EMOTION_MAX1, _MAX1_CAPABILITIES),
    DeviceProfile(DeviceModel.EMOTION_MAX2, "eMotion Max 2", frozenset({TYPE_EMOTION_MAX2}), PID_EMOTION_MAX2, _MAX2_CAPABILITIES),
    DeviceProfile(DeviceModel.EMOTION_MAX3, "eMotion Max 3", frozenset({TYPE_EMOTION_MAX3}), PID_EMOTION_MAX3, _MAX3_CAPABILITIES),
    DeviceProfile(DeviceModel.EHOME_HA, "eHomeHA", frozenset({TYPE_EHOME_HA}), PID_EHOME_HA, frozenset({DeviceCapability.REMOTE})),
    DeviceProfile(DeviceModel.EREMOTE_HA, "eRemoteHA", frozenset({TYPE_EREMOTE_HA}), PID_EREMOTE_HA, frozenset({DeviceCapability.REMOTE})),
)

_PROFILES_BY_TYPE = {type_id: profile for profile in DEVICE_PROFILES for type_id in profile.type_ids}
_PROFILES_BY_PID = {profile.pid: profile for profile in DEVICE_PROFILES}
PROFILES_BY_TYPE: Mapping[int, DeviceProfile] = MappingProxyType(_PROFILES_BY_TYPE)
PROFILES_BY_PID: Mapping[str, DeviceProfile] = MappingProxyType(_PROFILES_BY_PID)


def get_device_profile(type_id: int = 0, pid: str = "") -> DeviceProfile | None:
    """Return a profile by wire type first, then by normalized PID."""
    return PROFILES_BY_TYPE.get(type_id) or PROFILES_BY_PID.get(pid.strip().lower())
