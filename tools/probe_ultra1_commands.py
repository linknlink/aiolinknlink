"""Probe first-generation Ultra read commands across DNA header variants."""

from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from aiolinknlink import (
    DISPLAY_MODEL_ULTRA1,
    TYPE_ULTRA1,
    TYPE_ULTRA2_LAN,
    UltraClient,
    UltraDevice,
    UltraError,
    derive_radar_did,
)
from aiolinknlink.protocol import dna, emotion


def _parse_response(payload: bytes) -> dict[str, Any]:
    try:
        frame = emotion.parse_subdevice_frame(payload)
    except emotion.EmotionError:
        try:
            gateway = emotion.parse_gateway_state_response(payload)
        except emotion.EmotionError:
            return {"raw_length": len(payload), "raw_prefix": payload[:32].hex()}
        if gateway.gateway_state is not None:
            return {
                "gateway_command": gateway.command,
                "state": gateway.gateway_state.state,
                "attributes": gateway.gateway_state.attributes,
            }
        if gateway.subdevice_frame is not None:
            frame = gateway.subdevice_frame
        else:
            return {"gateway_command": gateway.command}
    try:
        decoded = emotion.parse_subdevice_json_payload(frame)
    except emotion.EmotionError:
        decoded = {"payload_length": len(frame.payload), "payload_prefix": frame.payload[:32].hex()}
    return {"subdevice_command": f"0x{frame.command_type:04X}", "payload": decoded}


async def _probe(args: argparse.Namespace) -> list[dict[str, Any]]:
    client = UltraClient(
        discovery_timeout=args.discovery_timeout,
        command_timeout=args.command_timeout,
        auth_timeout=args.auth_timeout,
    )
    device = UltraDevice(
        id=args.mac.replace(":", "").replace("-", "").lower(),
        ip=args.host,
        port=80,
        mac=args.mac,
        type_id=TYPE_ULTRA1,
        name=DISPLAY_MODEL_ULTRA1,
        model=DISPLAY_MODEL_ULTRA1,
    )
    commands = {
        "gateway_state": emotion.build_gateway_get_state_command(),
        "subdevice_list": emotion.build_get_subdevice_list_frame(),
    }
    results: list[dict[str, Any]] = []
    device_types = (args.device_type,) if args.device_type is not None else (TYPE_ULTRA1, TYPE_ULTRA2_LAN)
    message_types = (args.message_type,) if args.message_type is not None else (dna.MESSAGE_TYPE_COMMAND, 0x03E9)
    for device_type in device_types:
        for message_type in message_types:
            row: dict[str, Any] = {
                "device_type": f"0x{device_type:04X}",
                "message_type": f"0x{message_type:04X}",
            }
            session = None
            auth_errors: list[str] = []
            for attempt in range(args.auth_attempts):
                try:
                    session = await client.connect(device)
                except (OSError, UltraError) as err:
                    auth_errors.append(str(err) or type(err).__name__)
                    if attempt + 1 < args.auth_attempts:
                        await asyncio.sleep(args.auth_retry_interval)
                else:
                    break
            if session is None:
                row["authentication"] = {
                    "status": "failed",
                    "error": auth_errors[-1] if auth_errors else "authentication failed",
                }
                results.append(row)
                continue
            row["authentication"] = "passed"
            row["authentication_attempts"] = len(auth_errors) + 1
            session.command_device_type = device_type
            session.command_message_type = message_type
            listed_dids: list[str] = []
            for name, command in commands.items():
                try:
                    response = await client.send_command(session, command, try_all=False)
                except (OSError, UltraError) as err:
                    row[name] = {"status": "failed", "error": str(err) or type(err).__name__}
                else:
                    parsed = _parse_response(response)
                    row[name] = {"status": "passed", "response": parsed}
                    if name == "subdevice_list":
                        payload = parsed.get("payload")
                        if isinstance(payload, dict) and isinstance(items := payload.get("list"), list):
                            listed_dids = [
                                did
                                for item in items
                                if isinstance(item, dict) and isinstance(did := item.get("did"), str)
                            ]
            listed_status: dict[str, Any] = {}
            for did in listed_dids:
                try:
                    response = await client.send_command(
                        session,
                        emotion.build_get_status_frame(did),
                        try_all=False,
                    )
                except (OSError, UltraError) as err:
                    listed_status[did] = {"status": "failed", "error": str(err) or type(err).__name__}
                else:
                    listed_status[did] = {"status": "passed", "response": _parse_response(response)}
            row["listed_subdevices"] = listed_status
            try:
                response = await client.send_command(
                    session,
                    emotion.build_get_status_frame(derive_radar_did(args.mac)),
                    try_all=False,
                )
            except (OSError, UltraError) as err:
                row["radar_state"] = {"status": "failed", "error": str(err) or type(err).__name__}
            else:
                row["radar_state"] = {"status": "passed", "response": _parse_response(response)}
            results.append(row)
    return results


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--mac", required=True)
    parser.add_argument("--discovery-timeout", type=float, default=2)
    parser.add_argument("--command-timeout", type=float, default=2)
    parser.add_argument("--auth-timeout", type=float, default=5)
    parser.add_argument("--auth-attempts", type=int, default=3)
    parser.add_argument("--auth-retry-interval", type=float, default=1)
    parser.add_argument("--device-type", type=lambda value: int(value, 0))
    parser.add_argument("--message-type", type=lambda value: int(value, 0))
    return parser.parse_args()


def main() -> None:
    """Run the read-only command matrix."""
    print(json.dumps(asyncio.run(_probe(_parse_args())), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
