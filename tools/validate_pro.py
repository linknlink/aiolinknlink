"""Run source-derived eMotionPro hardware checks."""

from __future__ import annotations

import argparse
import asyncio
import json
import stat
from pathlib import Path
from typing import Any

from aiolinknlink import (
    TYPE_EMOTION_PRO_RADAR,
    DeviceModel,
    UltraClient,
    UltraConnectionError,
    UltraProtocolError,
)


def _read_local_key_file(path: str | Path) -> bytes:
    """Read a provisioning key without exposing it in process arguments."""
    path = Path(path)
    try:
        mode = stat.S_IMODE(path.stat().st_mode)
        value = path.read_bytes().strip()
    except OSError as err:
        raise argparse.ArgumentTypeError(f"cannot read local key file: {err}") from err
    if mode & 0o077:
        raise argparse.ArgumentTypeError("local key file must not be accessible by group or other users")
    try:
        key = bytes.fromhex(value.decode("ascii"))
    except (UnicodeDecodeError, ValueError) as err:
        raise argparse.ArgumentTypeError("local key file must contain exactly 32 hexadecimal characters") from err
    if len(key) != 16:
        raise argparse.ArgumentTypeError("local key file must contain exactly 32 hexadecimal characters")
    return key


async def validate(args: argparse.Namespace) -> dict[str, Any]:
    """Validate discovery, authentication, state, and optional restored control."""
    client = UltraClient(
        discovery_timeout=args.discovery_timeout,
        command_timeout=args.command_timeout,
        auth_timeout=args.auth_timeout,
    )
    discovery = "specified_host"
    try:
        device = await client.discover_host(args.host)
    except UltraConnectionError:
        device = None
        for attempt in range(args.broadcast_attempts):
            matches = [candidate for candidate in await client.discover() if candidate.ip == args.host]
            if matches:
                device = matches[0]
                break
            if attempt + 1 < args.broadcast_attempts:
                await asyncio.sleep(args.discovery_retry_interval)
        if device is None:
            raise
        discovery = "broadcast_only"
    if device.profile is None or device.profile.model is not DeviceModel.EMOTION_PRO:
        raise RuntimeError(f"unexpected device model: {device.model}")
    session = await client.connect(device, local_key=args.local_key)
    state = await client.get_environment_state(session)
    report: dict[str, Any] = {
        "model": device.profile.display_name,
        "type_id": f"0x{device.type_id:04X}",
        "discovery": discovery,
        "authentication": session.auth_status,
        "auth_device_type": f"0x{session.auth_device_type:04X}",
        "state": dict(state.values),
        "capabilities": sorted(capability.value for capability in device.capabilities),
    }

    if args.exercise_absence_delay:
        original = state.values.get("absence_delay")
        if isinstance(original, bool) or not isinstance(original, int):
            raise UltraProtocolError("device did not report an absence delay to restore")
        step = 1 if device.type_id == TYPE_EMOTION_PRO_RADAR else 60
        alternate = original + step if original + step <= 18 * 60 * 60 else original - step
        control: dict[str, Any] = {}
        try:
            changed = await client.set_pro_absence_delay(session, alternate)
            control["changed"] = changed.values.get("absence_delay") == alternate
        finally:
            restored = await client.set_pro_absence_delay(session, original)
            control["restored"] = restored.values.get("absence_delay") == original
        report["absence_delay_control"] = control

    session.session_key = None
    await client.reauthenticate(session, local_key=args.local_key)
    reauthenticated_state = await client.get_environment_state(session)
    report["reauthentication"] = session.auth_status
    report["reauthenticated_state"] = dict(reauthenticated_state.values)
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument(
        "--local-key-file",
        dest="local_key",
        type=_read_local_key_file,
        help="mode-0600 file containing the 16-byte local key as 32 hexadecimal characters",
    )
    parser.add_argument("--discovery-timeout", type=float, default=8)
    parser.add_argument("--command-timeout", type=float, default=8)
    parser.add_argument("--auth-timeout", type=float, default=12)
    parser.add_argument("--broadcast-attempts", type=int, default=3)
    parser.add_argument("--discovery-retry-interval", type=float, default=1)
    parser.add_argument("--exercise-absence-delay", action="store_true")
    return parser.parse_args()


def main() -> None:
    """Run the hardware validator and print a non-identifying report."""
    print(json.dumps(asyncio.run(validate(_parse_args())), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
