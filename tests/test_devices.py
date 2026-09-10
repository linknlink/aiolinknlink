"""Tests for shared LinknLink device profiles."""

from aiolinknlink import (
    PID_EMOTION_MAX1,
    PID_ULTRA1,
    TYPE_ULTRA1,
    DeviceCapability,
    DeviceModel,
    UltraDevice,
    get_device_profile,
)


def test_profiles_match_wire_type_and_pid() -> None:
    ultra1 = get_device_profile(TYPE_ULTRA1)
    max1 = get_device_profile(pid=PID_EMOTION_MAX1)

    assert ultra1 is not None
    assert ultra1.model is DeviceModel.EMOTION_ULTRA
    assert DeviceCapability.POSITION not in ultra1.capabilities
    assert max1 is not None
    assert max1.model is DeviceModel.EMOTION_MAX1
    assert DeviceCapability.TARGET_SPEED not in max1.capabilities
    for capability in (
        DeviceCapability.TEMPERATURE,
        DeviceCapability.HUMIDITY,
        DeviceCapability.ILLUMINANCE,
        DeviceCapability.WIFI_SIGNAL,
    ):
        assert capability not in max1.capabilities


def test_ultra_device_exposes_profile_capabilities() -> None:
    device = UltraDevice(
        id="020000000110",
        ip="192.0.2.8",
        port=80,
        pid=PID_ULTRA1,
        type_id=TYPE_ULTRA1,
    )

    assert device.profile is not None
    assert device.profile.model is DeviceModel.EMOTION_ULTRA
    assert DeviceCapability.OCCUPANCY in device.capabilities
