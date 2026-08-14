#!/usr/bin/env python3
"""Validate the custom component and optionally exercise a real iBG read."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import os
import sys
from pathlib import Path


def _parse_args() -> argparse.Namespace:
    """Parse smoke-test command line arguments."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--host", help="Optional iBG IPv4/hostname for a read-only live test")
    return parser.parse_args()


async def _live_test(host: str) -> dict[str, object]:
    """Perform a read-only discovery and state read against one iBG."""
    from aiolinknlink import IbgClient

    client = IbgClient(discovery_timeout=3, auth_timeout=5, command_timeout=5)
    gateway = await client.discover_host(host)
    local_key_hex = os.environ.get("LINKNLINK_IBG_LOCAL_KEY", "")
    local_key = bytes.fromhex(local_key_hex) if local_key_hex else None
    session = await client.connect(gateway, local_key=local_key)
    devices = await client.list_subdevices(session)
    states = await client.read_supported_states(session, devices)
    return {
        "gateway_model": gateway.model,
        "gateway_type": gateway.type_id,
        "subdevice_count": len(devices),
        "online_count": sum(device.online for device in devices),
        "supported_count": len(states),
        "available_count": sum(state is not None for state in states.values()),
        "state_fields": sorted({field for state in states.values() if state is not None for field in state.values}),
    }


def main() -> None:
    """Validate HA imports and optionally run the live gateway check."""
    args = _parse_args()
    source = args.source.resolve()
    sys.path.insert(0, str(source / "src"))
    sys.path.insert(0, str(source))

    component = source / "custom_components" / "linknlink"
    for path in (
        component / "manifest.json",
        component / "strings.json",
        component / "translations" / "zh-Hans.json",
    ):
        json.loads(path.read_text())

    for module in (
        "custom_components.linknlink",
        "custom_components.linknlink.config_flow",
        "custom_components.linknlink.coordinator",
        "custom_components.linknlink.entity",
        "custom_components.linknlink.sensor",
        "custom_components.linknlink.binary_sensor",
        "custom_components.linknlink.event",
        "custom_components.linknlink.switch",
    ):
        importlib.import_module(module)

    result: dict[str, object] = {"component_import": "ok"}
    if args.host:
        result["live"] = asyncio.run(_live_test(args.host))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
