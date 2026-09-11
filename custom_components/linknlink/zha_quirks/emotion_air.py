r"""ZHA quirk for the LinknLink eMotion Air multi-sensor / smart button.

This file ships with the LinknLink HACS integration. HACS keeps it up to
date, so point ZHA straight at this directory in configuration.yaml:

    zha:
      enable_quirks: true
      custom_quirks_path: /config/custom_components/linknlink/zha_quirks/

The directory must exist before Home Assistant starts, and quirks are only
loaded at startup. After restarting, remove the device from ZHA and pair it
again so zigpy re-reads the cluster schema.

Firmware >= V1.2.7 emits a cluster-specific BUTTON_ACTION command on the
proprietary 0xFC01 cluster instead of OnOff/LevelControl or Analog/Multistate
attribute reports.

Wire payload of cluster 0xFC01, command 0x00 (server -> client):
    [0]    uint8            action_id  1=single 2=double 3=triple 4=hold
    [1..]  CharacterString  action_str "single"/"double"/"triple"/"hold"

e.g. b"\x01\x06single"

This quirk provides BOTH:
  1) zha_event with command = single/double/triple/hold
  2) a visible sensor entity (last_action) showing the same text

There is no "release" event, and "hold" fires once the button has been held
for 2 s (it does not wait for the release edge).
"""

from __future__ import annotations

import logging
import struct

import zigpy.types as t
from zhaquirks import CustomCluster
from zhaquirks.const import (
    BUTTON,
    COMMAND,
    DOUBLE_PRESS,
    LONG_PRESS,
    SHORT_PRESS,
    TRIPLE_PRESS,
    ZHA_SEND_EVENT,
)
from zigpy.profiles import zha
from zigpy.zcl import foundation
from zigpy.zcl.clusters.general import (
    Basic,
    Ota,
    PollControl,
    PowerConfiguration,
)
from zigpy.zcl.clusters.measurement import (
    IlluminanceMeasurement,
    OccupancySensing,
    RelativeHumidity,
    TemperatureMeasurement,
)
from zigpy.zcl.foundation import BaseAttributeDefs, ZCLAttributeDef

_LOGGER = logging.getLogger(__name__)

BUTTON_ACTION_CLUSTER_ID = 0xFC01
WWAH_CLUSTER_ID = 0xFC57

ACTION_SINGLE = "single"
ACTION_DOUBLE = "double"
ACTION_TRIPLE = "triple"
ACTION_HOLD = "hold"

ACTION_BY_ID = {
    1: ACTION_SINGLE,
    2: ACTION_DOUBLE,
    3: ACTION_TRIPLE,
    4: ACTION_HOLD,
}


class ButtonAction(t.enum8):
    """Button action values exposed by the last_action sensor entity."""

    single = 1
    double = 2
    triple = 3
    hold = 4


ACTION_ENUM_BY_ID = {
    1: ButtonAction.single,
    2: ButtonAction.double,
    3: ButtonAction.triple,
    4: ButtonAction.hold,
}


class ButtonActionCluster(CustomCluster):
    """Proprietary LinknLink button action cluster (0xFC01)."""

    cluster_id = BUTTON_ACTION_CLUSTER_ID
    name = "LinknLink Button Action"
    ep_attribute = "linknlink_button_action"

    ATTR_LAST_ACTION = 0x0000

    class AttributeDefs(BaseAttributeDefs):
        """Quirk-local attribute, used to expose a readable HA sensor entity."""

        last_action = ZCLAttributeDef(
            id=0x0000,
            type=ButtonAction,
            access="rp",
            is_manufacturer_specific=True,
        )
        cluster_revision = foundation.ZCL_CLUSTER_REVISION_ATTR

    # The device sends this as a server -> client cluster command, so on the
    # server cluster representation it belongs in client_commands.
    class ClientCommandDefs(foundation.BaseCommandDefs):
        """Cluster commands sent by the device."""

        button_action = foundation.ZCLCommandDef(
            id=0x00,
            schema={"action_id": t.uint8_t, "action_str": t.CharacterString},
            direction=foundation.Direction.Server_to_Client,
            is_manufacturer_specific=True,
        )

    def _emit(self, action: str, action_id: int) -> None:
        _LOGGER.debug("eMotion Air button action: %s (id=%s)", action, action_id)
        enum_value = ACTION_ENUM_BY_ID.get(action_id)
        if enum_value is not None:
            self._update_attribute(self.ATTR_LAST_ACTION, enum_value)
        self.listener_event(
            ZHA_SEND_EVENT,
            action,
            {"action_id": action_id, "action": action},
        )

    def handle_cluster_request(self, hdr, args, *, dst_addressing=None):
        """Handle the decoded BUTTON_ACTION command."""
        if hdr.command_id != 0x00:
            return

        action_id = 0
        action_str = None
        try:
            action_id = int(args[0])
            if len(args) > 1:
                action_str = args[1]
        except (IndexError, TypeError, ValueError):
            # fall back to attribute style access
            action_id = int(getattr(args, "action_id", 0) or 0)
            action_str = getattr(args, "action_str", None)

        if isinstance(action_str, (bytes, bytearray)):
            action_str = bytes(action_str).decode("utf-8", errors="ignore")

        action = ACTION_BY_ID.get(action_id) or (str(action_str) if action_str else None)
        if not action:
            return

        self._emit(action, action_id)

    def handle_message(self, hdr, args, *, dst_addressing=None):
        """Fallback for firmwares/stacks where the schema fails to decode."""
        try:
            super().handle_message(hdr, args, dst_addressing=dst_addressing)
        except TypeError:
            super().handle_message(hdr, args)


# ---------------------------------------------------------------------------
# Proprietary air config cluster (0xFC00).
# ---------------------------------------------------------------------------

AIR_CONFIG_CLUSTER_ID = 0xFC00
AIR_CONFIG_PROTOCOL_VERSION = 0x01
AIR_CONFIG_CMD_SET = 0x01
AIR_CONFIG_CMD_GET = 0x02
AIR_CONFIG_CMD_STATUS = 0x81
AIR_CONFIG_CMD_GET_RSP = 0x82
AIR_CONFIG_PAYLOAD_FMT = "<BHBBHBHHHH"


class RadarFrequency(t.enum8):
    """Radar sample rate."""

    Freq_0_5Hz = 0
    Freq_1Hz = 1
    Freq_2Hz = 2
    Freq_4Hz = 3
    Freq_8Hz = 4


class AirConfigCluster(CustomCluster):
    """Proprietary LinknLink air/radar configuration cluster (0xFC00)."""

    cluster_id = AIR_CONFIG_CLUSTER_ID
    name = "LinknLink Air Config"
    ep_attribute = "linknlink_air_config"

    MASK_BY_ATTR_ID = {
        0x0001: 1 << 0,
        0x0002: 1 << 1,
        0x0003: 1 << 2,
        0x0004: 1 << 3,
        0x0005: 1 << 4,
        0x0006: 1 << 5,
        0x0007: 1 << 6,
        0x0008: 1 << 7,
    }
    _FIELD_ORDER = (0x0001, 0x0002, 0x0003, 0x0004, 0x0005, 0x0006, 0x0007, 0x0008)

    class AttributeDefs(BaseAttributeDefs):
        """Read-only mirrors of current device parameters."""

        protocol_version = ZCLAttributeDef(id=0x0000, type=t.uint8_t, access="r")
        freq = ZCLAttributeDef(id=0x0001, type=RadarFrequency, access="rw")
        trith = ZCLAttributeDef(id=0x0002, type=t.uint8_t, access="rw")
        absence_timeout = ZCLAttributeDef(id=0x0003, type=t.uint16_t, access="rw")
        radar_enable = ZCLAttributeDef(id=0x0004, type=t.Bool, access="rw")
        lx_interval = ZCLAttributeDef(id=0x0005, type=t.uint16_t, access="rw")
        sht_interval = ZCLAttributeDef(id=0x0006, type=t.uint16_t, access="rw")
        lx_thread1 = ZCLAttributeDef(id=0x0007, type=t.uint16_t, access="rw")
        lx_thread2 = ZCLAttributeDef(id=0x0008, type=t.uint16_t, access="rw")
        cluster_revision = foundation.ZCL_CLUSTER_REVISION_ATTR

    class ServerCommandDefs(foundation.BaseCommandDefs):
        """Commands accepted by the device."""

        set_config = foundation.ZCLCommandDef(
            id=AIR_CONFIG_CMD_SET,
            schema={"payload": t.LVBytes},
            direction=foundation.Direction.Client_to_Server,
            is_manufacturer_specific=True,
        )
        get_config = foundation.ZCLCommandDef(
            id=AIR_CONFIG_CMD_GET,
            schema={},
            direction=foundation.Direction.Client_to_Server,
            is_manufacturer_specific=True,
        )

    class ClientCommandDefs(foundation.BaseCommandDefs):
        """Responses emitted by the device."""

        config_status = foundation.ZCLCommandDef(
            id=AIR_CONFIG_CMD_STATUS,
            schema={"payload": t.LVBytes},
            direction=foundation.Direction.Server_to_Client,
            is_manufacturer_specific=True,
        )
        get_config_rsp = foundation.ZCLCommandDef(
            id=AIR_CONFIG_CMD_GET_RSP,
            schema={"payload": t.LVBytes},
            direction=foundation.Direction.Server_to_Client,
            is_manufacturer_specific=True,
        )

    def _current(self, attr_id: int, default: int = 0) -> int:
        value = self._attr_cache.get(attr_id)
        return default if value is None else int(value)

    def _decode(self, data: bytes) -> None:
        if len(data) == struct.calcsize(AIR_CONFIG_PAYLOAD_FMT) + 1:
            status, data = data[0], data[1:]
            if status != foundation.Status.SUCCESS:
                _LOGGER.warning("eMotion Air config rejected: %s", status)
        if len(data) != struct.calcsize(AIR_CONFIG_PAYLOAD_FMT):
            return
        version, _mask, *values = struct.unpack(AIR_CONFIG_PAYLOAD_FMT, data)
        if version != AIR_CONFIG_PROTOCOL_VERSION:
            _LOGGER.warning("Unexpected air config version %s", version)
        self._update_attribute(0x0000, version)
        for attr_id, value in zip(self._FIELD_ORDER, values, strict=False):
            self._update_attribute(attr_id, value)

    def handle_cluster_request(self, hdr, args, *, dst_addressing=None):
        """Decode CONFIG_STATUS / GET_CONFIG_RSP from the device."""
        if hdr.command_id not in (AIR_CONFIG_CMD_STATUS, AIR_CONFIG_CMD_GET_RSP):
            return
        try:
            payload = args[0]
        except (IndexError, TypeError):
            payload = getattr(args, "payload", None)
        if payload is not None:
            self._decode(bytes(payload))

    async def read_attributes(self, attributes, allow_cache=False, only_cache=False, manufacturer=None, **kwargs):
        """Refresh the whole parameter block, then serve from cache."""
        if not only_cache:
            try:
                await super().command(AIR_CONFIG_CMD_GET, manufacturer=self.endpoint.manufacturer_id)
            except Exception:
                _LOGGER.debug("eMotion Air GET_CONFIG failed")
        return await super().read_attributes(
            attributes, allow_cache=True, only_cache=True, manufacturer=manufacturer, **kwargs
        )

    async def write_attributes(self, attributes, manufacturer=None, **kwargs):
        """Translate attribute writes into a SET_CONFIG command."""
        mask = 0
        values = {}
        for name_or_id, value in attributes.items():
            attr = self.find_attribute(name_or_id)
            bit = self.MASK_BY_ATTR_ID.get(attr.id)
            if bit is None:
                raise ValueError(f"{attr.name} is not writable on this cluster")
            mask |= bit
            values[attr.id] = int(value)
        if not mask:
            return []
        packed = struct.pack(
            AIR_CONFIG_PAYLOAD_FMT,
            AIR_CONFIG_PROTOCOL_VERSION,
            mask,
            *(values.get(i, self._current(i)) for i in self._FIELD_ORDER),
        )
        _LOGGER.debug("SET_CONFIG mask=0x%04x", mask)
        result = await super().command(AIR_CONFIG_CMD_SET, packed, manufacturer=self.endpoint.manufacturer_id)
        for attr_id, value in values.items():
            self._update_attribute(attr_id, value)
        return result


# v1 CustomDevice signatures were removed on purpose.
# On HA 2026.x they can win the match over QuirkBuilder v2 and then last_action /
# radar config entities never get created. Keep QuirkBuilder-only registration.

# ---------------------------------------------------------------------------
# Quirks v2 registration only.
#
# Match on manufacturer/model, replace clusters 0xFC01/0xFC00 with custom
# clusters, and expose last_action + radar config entities.
# ---------------------------------------------------------------------------
try:
    from zigpy.quirks.v2 import QuirkBuilder
    from zigpy.quirks.v2.homeassistant import EntityPlatform, EntityType
except ImportError:  # pragma: no cover - older zigpy
    QuirkBuilder = None

if QuirkBuilder is not None:
    _builder = (
        QuirkBuilder("LinknLink", "eMotion Air")
        .replaces(ButtonActionCluster, endpoint_id=1)
        .replaces(AirConfigCluster, endpoint_id=1)
        .enum(
            AirConfigCluster.AttributeDefs.freq.name,
            RadarFrequency,
            AirConfigCluster.cluster_id,
            endpoint_id=1,
            entity_type=EntityType.CONFIG,
            translation_key="radar_frequency",
            fallback_name="Radar frequency",
        )
        .number(
            AirConfigCluster.AttributeDefs.trith.name,
            AirConfigCluster.cluster_id,
            endpoint_id=1,
            min_value=1,
            max_value=10,
            step=1,
            entity_type=EntityType.CONFIG,
            translation_key="radar_sensitivity",
            fallback_name="Radar sensitivity",
        )
        .number(
            AirConfigCluster.AttributeDefs.absence_timeout.name,
            AirConfigCluster.cluster_id,
            endpoint_id=1,
            min_value=0,
            max_value=510,
            step=2,
            unit="s",
            entity_type=EntityType.CONFIG,
            translation_key="radar_absence_timeout",
            fallback_name="Radar absence timeout",
        )
        .switch(
            AirConfigCluster.AttributeDefs.radar_enable.name,
            AirConfigCluster.cluster_id,
            endpoint_id=1,
            entity_type=EntityType.CONFIG,
            translation_key="radar_enable",
            fallback_name="Radar enabled",
        )
        .number(
            AirConfigCluster.AttributeDefs.lx_interval.name,
            AirConfigCluster.cluster_id,
            endpoint_id=1,
            min_value=2,
            max_value=60000,
            step=1,
            unit="s",
            entity_type=EntityType.CONFIG,
            translation_key="illuminance_interval",
            fallback_name="Illuminance interval",
        )
        .number(
            AirConfigCluster.AttributeDefs.sht_interval.name,
            AirConfigCluster.cluster_id,
            endpoint_id=1,
            min_value=10,
            max_value=60000,
            step=1,
            unit="s",
            entity_type=EntityType.CONFIG,
            translation_key="temp_humidity_interval",
            fallback_name="Temperature/humidity interval",
        )
        .number(
            AirConfigCluster.AttributeDefs.lx_thread1.name,
            AirConfigCluster.cluster_id,
            endpoint_id=1,
            min_value=0,
            max_value=60000,
            step=1,
            unit="lx",
            entity_type=EntityType.CONFIG,
            translation_key="illuminance_threshold_low",
            fallback_name="Illuminance threshold low",
        )
        .number(
            AirConfigCluster.AttributeDefs.lx_thread2.name,
            AirConfigCluster.cluster_id,
            endpoint_id=1,
            min_value=0,
            max_value=60000,
            step=1,
            unit="lx",
            entity_type=EntityType.CONFIG,
            translation_key="illuminance_threshold_high",
            fallback_name="Illuminance threshold high",
        )
        .enum(
            ButtonActionCluster.AttributeDefs.last_action.name,
            ButtonAction,
            ButtonActionCluster.cluster_id,
            endpoint_id=1,
            entity_platform=EntityPlatform.SENSOR,
            entity_type=EntityType.STANDARD,
            translation_key="last_action",
            fallback_name="Last action",
        )
        .device_automation_triggers(
            {
                (SHORT_PRESS, BUTTON): {COMMAND: ACTION_SINGLE},
                (DOUBLE_PRESS, BUTTON): {COMMAND: ACTION_DOUBLE},
                (TRIPLE_PRESS, BUTTON): {COMMAND: ACTION_TRIPLE},
                (LONG_PRESS, BUTTON): {COMMAND: ACTION_HOLD},
            }
        )
    )
    _builder.add_to_registry()
