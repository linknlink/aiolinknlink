"""Tests for model metadata and compatibility APIs."""

from __future__ import annotations

from types import MappingProxyType

import pytest

from aiolinknlink import (
    PROFILES_BY_TYPE,
    TYPE_EMOTION_MAX1,
    TYPE_LEGACY_RM_MONITOR,
    TYPE_ULTRA1,
    DeviceCapability,
    DeviceModel,
    LinknLinkClient,
    LinknLinkDevice,
    LinknLinkSession,
    UltraClient,
    UltraDevice,
    UltraSession,
    get_device_profile,
)


def test_public_compatibility_aliases() -> None:
    """Generic names preserve the existing public constructors."""
    assert LinknLinkClient is UltraClient
    assert LinknLinkDevice is UltraDevice
    assert LinknLinkSession is UltraSession


def test_profiles_are_immutable_and_model_specific() -> None:
    """Capabilities are selected only from the reported wire type."""
    ultra1 = get_device_profile(TYPE_ULTRA1)
    max1 = get_device_profile(TYPE_EMOTION_MAX1)

    assert ultra1 is not None
    assert ultra1.model is DeviceModel.EMOTION_ULTRA1
    assert DeviceCapability.DISTANCE not in ultra1.capabilities
    assert DeviceCapability.POSITION not in ultra1.capabilities
    assert DeviceCapability.LOCAL_UDP not in ultra1.capabilities
    assert DeviceCapability.TARGET_SPEED not in ultra1.capabilities
    assert max1 is not None
    assert DeviceCapability.TARGET_SPEED not in max1.capabilities
    assert isinstance(PROFILES_BY_TYPE, MappingProxyType)
    with pytest.raises(TypeError):
        PROFILES_BY_TYPE[0] = ultra1  # type: ignore[index]


def test_legacy_monitor_is_deliberately_unsupported() -> None:
    """The obsolete RM Monitor PID must never create entities."""
    assert get_device_profile(TYPE_LEGACY_RM_MONITOR) is None


def test_device_exposes_read_only_capabilities() -> None:
    device = LinknLinkDevice(id="device", ip="192.0.2.2", port=80, type_id=TYPE_ULTRA1)

    assert device.profile is get_device_profile(TYPE_ULTRA1)
    assert DeviceCapability.OCCUPANCY in device.capabilities
    assert LinknLinkDevice(id="unknown", ip="192.0.2.3", port=80).capabilities == frozenset()
