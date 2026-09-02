"""Run source-derived eMotion Ultra first-generation hardware checks."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import stat
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any, Protocol, TypeVar

from aiolinknlink import (
    DISPLAY_MODEL_ULTRA1,
    TYPE_ULTRA1,
    DeviceModel,
    UltraClient,
    UltraConnectionError,
    UltraDevice,
    UltraError,
    UltraPositionSubscription,
    UltraRadarStatus,
    UltraRadarZRange,
    UltraSession,
)
from aiolinknlink.protocol import emotion

Setter = Callable[..., Awaitable[UltraRadarStatus]]
T = TypeVar("T")


def _read_local_key_file(path: str | Path) -> bytes:
    """Read a provisioning key without exposing it in the process arguments."""
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


class _RadarController(Protocol):
    """Common radar operations used by direct and persistent UDP transports."""

    async def get_radar_status(self) -> UltraRadarStatus: ...

    async def set_radar_sensitivity(self, sensitivity: int) -> UltraRadarStatus: ...

    async def set_radar_trigger_speed(self, trigger_speed: int) -> UltraRadarStatus: ...

    async def set_radar_install_mode(self, install_mode: int) -> UltraRadarStatus: ...

    async def set_radar_height(self, height: int) -> UltraRadarStatus: ...

    async def set_radar_install_direction(self, install_direction: int) -> UltraRadarStatus: ...

    async def set_radar_z_range(self, minimum: float, maximum: float) -> UltraRadarStatus: ...

    async def set_radar_default_absence_delay(self, seconds: int) -> UltraRadarStatus: ...

    async def set_radar_zone_absence_delay(self, zone: int, seconds: int) -> UltraRadarStatus: ...


class _DirectRadarController:
    """Expose session-bound client operations through the controller interface."""

    def __init__(self, client: UltraClient, session: UltraSession) -> None:
        self._client = client
        self._session = session

    async def get_radar_status(self) -> UltraRadarStatus:
        return await self._client.get_radar_status(self._session)

    async def set_radar_sensitivity(self, sensitivity: int) -> UltraRadarStatus:
        return await self._client.set_radar_sensitivity(self._session, sensitivity)

    async def set_radar_trigger_speed(self, trigger_speed: int) -> UltraRadarStatus:
        return await self._client.set_radar_trigger_speed(self._session, trigger_speed)

    async def set_radar_install_mode(self, install_mode: int) -> UltraRadarStatus:
        return await self._client.set_radar_install_mode(self._session, install_mode)

    async def set_radar_height(self, height: int) -> UltraRadarStatus:
        return await self._client.set_radar_height(self._session, height)

    async def set_radar_install_direction(self, install_direction: int) -> UltraRadarStatus:
        return await self._client.set_radar_install_direction(self._session, install_direction)

    async def set_radar_z_range(self, minimum: float, maximum: float) -> UltraRadarStatus:
        return await self._client.set_radar_z_range(self._session, minimum, maximum)

    async def set_radar_default_absence_delay(self, seconds: int) -> UltraRadarStatus:
        return await self._client.set_radar_default_absence_delay(self._session, seconds)

    async def set_radar_zone_absence_delay(self, zone: int, seconds: int) -> UltraRadarStatus:
        return await self._client.set_radar_zone_absence_delay(self._session, zone, seconds)


class _RetryingRadarController:
    """Retry idempotent radar reads and writes on an unreliable local link."""

    def __init__(self, controller: _RadarController, attempts: int, interval: float) -> None:
        self._controller = controller
        self._attempts = attempts
        self._interval = interval

    async def get_radar_status(self) -> UltraRadarStatus:
        return await _retry_read(self._controller.get_radar_status, self._attempts, self._interval)

    async def set_radar_sensitivity(self, sensitivity: int) -> UltraRadarStatus:
        return await _retry_read(
            lambda: self._controller.set_radar_sensitivity(sensitivity), self._attempts, self._interval
        )

    async def set_radar_trigger_speed(self, trigger_speed: int) -> UltraRadarStatus:
        return await _retry_read(
            lambda: self._controller.set_radar_trigger_speed(trigger_speed), self._attempts, self._interval
        )

    async def set_radar_install_mode(self, install_mode: int) -> UltraRadarStatus:
        return await _retry_read(
            lambda: self._controller.set_radar_install_mode(install_mode), self._attempts, self._interval
        )

    async def set_radar_height(self, height: int) -> UltraRadarStatus:
        return await _retry_read(lambda: self._controller.set_radar_height(height), self._attempts, self._interval)

    async def set_radar_install_direction(self, install_direction: int) -> UltraRadarStatus:
        return await _retry_read(
            lambda: self._controller.set_radar_install_direction(install_direction), self._attempts, self._interval
        )

    async def set_radar_z_range(self, minimum: float, maximum: float) -> UltraRadarStatus:
        return await _retry_read(
            lambda: self._controller.set_radar_z_range(minimum, maximum), self._attempts, self._interval
        )

    async def set_radar_default_absence_delay(self, seconds: int) -> UltraRadarStatus:
        return await _retry_read(
            lambda: self._controller.set_radar_default_absence_delay(seconds), self._attempts, self._interval
        )

    async def set_radar_zone_absence_delay(self, zone: int, seconds: int) -> UltraRadarStatus:
        return await _retry_read(
            lambda: self._controller.set_radar_zone_absence_delay(zone, seconds), self._attempts, self._interval
        )


async def _retry_read(
    operation: Callable[[], Awaitable[T]],
    attempts: int,
    interval: float,
) -> T:
    """Retry an idempotent hardware read on an unreliable local link."""
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await operation()
        except (OSError, TimeoutError, UltraError, emotion.EmotionError) as err:
            last_error = err
            if attempt < attempts:
                await asyncio.sleep(interval)
    assert last_error is not None
    raise last_error


def _different_int(value: int, minimum: int, maximum: int) -> int:
    return value + 1 if value < maximum else value - 1


async def _exercise_scalar(
    report: dict[str, Any],
    name: str,
    setter: Setter,
    original: int | None,
    minimum: int,
    maximum: int,
) -> None:
    if original is None:
        report[name] = {"status": "unsupported"}
        return
    alternate = _different_int(original, minimum, maximum)
    try:
        await setter(alternate)
        report[name] = {"status": "changed_and_read_back"}
    except (OSError, TimeoutError, UltraError) as err:
        report[name] = {"status": "failed", "error": str(err) or type(err).__name__}
    finally:
        try:
            await setter(original)
        except (OSError, TimeoutError, UltraError) as err:
            report.setdefault(name, {})["restore"] = {
                "status": "failed",
                "error": str(err) or type(err).__name__,
            }
            raise
        report.setdefault(name, {})["restored"] = True


async def _exercise_z_range(
    report: dict[str, Any],
    controller: _RadarController,
    original: UltraRadarZRange | None,
) -> None:
    if original is None:
        report["z_range"] = {"status": "unsupported"}
        return
    if original.maximum - original.minimum <= 0.2:
        report["z_range"] = {"status": "skipped", "reason": "range too narrow"}
        return
    alternate_minimum = round(original.minimum + 0.1, 2)
    try:
        await controller.set_radar_z_range(alternate_minimum, original.maximum)
        report["z_range"] = {"status": "changed_and_read_back"}
    except (OSError, TimeoutError, UltraError) as err:
        report["z_range"] = {"status": "failed", "error": str(err) or type(err).__name__}
    finally:
        try:
            await controller.set_radar_z_range(original.minimum, original.maximum)
        except (OSError, TimeoutError, UltraError) as err:
            report.setdefault("z_range", {})["restore"] = {
                "status": "failed",
                "error": str(err) or type(err).__name__,
            }
            raise
        report.setdefault("z_range", {})["restored"] = True


async def _exercise_controls(
    controller: _RadarController,
    status: UltraRadarStatus,
) -> dict[str, Any]:
    report: dict[str, Any] = {}
    await _exercise_scalar(
        report,
        "sensitivity",
        controller.set_radar_sensitivity,
        status.sensitivity,
        0,
        2,
    )
    await _exercise_scalar(
        report,
        "trigger_speed",
        controller.set_radar_trigger_speed,
        status.trigger_speed,
        0,
        2,
    )
    await _exercise_scalar(
        report,
        "install_mode",
        controller.set_radar_install_mode,
        status.install_mode,
        0,
        1,
    )
    await _exercise_scalar(
        report,
        "height",
        controller.set_radar_height,
        status.height,
        0,
        0xFFFF,
    )
    await _exercise_scalar(
        report,
        "install_direction",
        controller.set_radar_install_direction,
        status.install_direction,
        0,
        1,
    )
    await _exercise_z_range(report, controller, status.z_range)
    await _exercise_scalar(
        report,
        "default_absence_delay",
        controller.set_radar_default_absence_delay,
        status.default_absence_delay,
        0,
        18 * 60 * 60,
    )
    for zone, original in enumerate(status.zone_absence_delays, start=1):
        if original is None:
            report[f"zone_{zone}_absence_delay"] = {"status": "unsupported"}
            continue
        alternate = _different_int(original, 0, 18 * 60 * 60)
        try:
            await controller.set_radar_zone_absence_delay(zone, alternate)
            report[f"zone_{zone}_absence_delay"] = {"status": "changed_and_read_back"}
        except (OSError, TimeoutError, UltraError) as err:
            report[f"zone_{zone}_absence_delay"] = {
                "status": "failed",
                "error": str(err) or type(err).__name__,
            }
        finally:
            try:
                await controller.set_radar_zone_absence_delay(zone, original)
            except (OSError, TimeoutError, UltraError) as err:
                report.setdefault(f"zone_{zone}_absence_delay", {})["restore"] = {
                    "status": "failed",
                    "error": str(err) or type(err).__name__,
                }
                raise
            report.setdefault(f"zone_{zone}_absence_delay", {})["restored"] = True
    return report


async def _validate_reauthentication(
    report: dict[str, Any],
    client: UltraClient,
    session: UltraSession,
    local_key: bytes | None,
    attempts: int,
    interval: float,
) -> None:
    """Restore credentials and prove them with a fresh device-state command."""
    old_key = session.session_key
    session.session_key = None
    try:
        await client.reauthenticate(session, local_key=local_key)
        state = await _retry_read(
            lambda: client.get_environment_state(session),
            attempts,
            interval,
        )
    except (OSError, TimeoutError, UltraError) as err:
        report["reauthentication"] = {
            "status": "failed",
            "error": str(err) or type(err).__name__,
        }
    else:
        report["reauthentication"] = (
            "passed" if session.auth_status in {"ok", "provided"} and session.session_key else "failed"
        )
        report["reauthenticated_fields"] = sorted(state.values)
    report["session_key_refreshed"] = bool(old_key and session.session_key and old_key != session.session_key)


async def validate(args: argparse.Namespace) -> dict[str, Any]:
    """Run one Ultra1 validation session and return a serializable report."""
    client = UltraClient(
        discovery_timeout=args.discovery_timeout,
        command_timeout=args.command_timeout,
        auth_timeout=args.auth_timeout,
        preferred_command_timeout=args.preferred_command_timeout,
    )
    discovery = "passed"
    try:
        device = await client.discover_host(args.host)
    except UltraConnectionError:
        if not args.mac:
            raise
        discovery = "failed_used_provisioning_identity"
        device = UltraDevice(
            id=args.mac.replace(":", "").replace("-", "").lower(),
            ip=args.host,
            port=80,
            mac=args.mac,
            type_id=TYPE_ULTRA1,
            name=DISPLAY_MODEL_ULTRA1,
            model=DISPLAY_MODEL_ULTRA1,
        )
    if device.profile is None or device.profile.model is not DeviceModel.EMOTION_ULTRA1:
        raise RuntimeError(f"unexpected device model: {device.model}")
    session = None
    auth_errors: list[str] = []
    for attempt in range(1, args.auth_attempts + 1):
        try:
            session = await client.connect(device, local_key=args.local_key)
        except UltraError as err:
            auth_errors.append(str(err) or type(err).__name__)
            if attempt < args.auth_attempts:
                await asyncio.sleep(args.auth_retry_interval)
        else:
            break
    if session is None:
        raise UltraConnectionError(auth_errors[-1] if auth_errors else "authentication failed")
    if args.command_device_type is not None:
        session.command_device_type = args.command_device_type
    report: dict[str, Any] = {
        "model": device.profile.display_name,
        "type_id": f"0x{device.type_id:04X}",
        "discovery": discovery,
        "authentication": session.auth_status,
        "target_speed": "unsupported_by_local_protocol",
        "authentication_attempts": len(auth_errors) + 1,
    }
    if args.command_device_type is not None:
        report["command_device_type_override"] = f"0x{args.command_device_type:04X}"

    try:
        list_response = await _retry_read(
            lambda: client.send_command(session, emotion.build_get_subdevice_list_frame()),
            args.read_attempts,
            args.read_retry_interval,
        )
        list_frame = emotion.parse_subdevice_frame(list_response)
        report["subdevice_list"] = emotion.parse_subdevice_json_payload(list_frame)
    except (OSError, TimeoutError, UltraError, emotion.EmotionError) as err:
        report["subdevice_list"] = {
            "status": "failed",
            "error": str(err) or type(err).__name__,
        }

    try:
        gateway_payload = await _retry_read(
            lambda: client.send_command(session, emotion.build_gateway_get_state_command()),
            args.read_attempts,
            args.read_retry_interval,
        )
        gateway_response = emotion.parse_gateway_state_response(gateway_payload)
        if gateway_response.gateway_state is not None:
            report["gateway_state"] = {
                "status": "passed",
                "command": gateway_response.command,
                "state": gateway_response.gateway_state.state,
                "attributes": gateway_response.gateway_state.attributes,
            }
        elif gateway_response.subdevice_frame is not None:
            report["gateway_state"] = {
                "status": "passed_subdevice_frame",
                "command": gateway_response.command,
                "subdevice": emotion.parse_subdevice_json_payload(gateway_response.subdevice_frame),
            }
        else:
            report["gateway_state"] = {"status": "failed", "error": "empty gateway response"}
    except (OSError, TimeoutError, UltraError, emotion.EmotionError) as err:
        report["gateway_state"] = {
            "status": "failed",
            "error": str(err) or type(err).__name__,
        }

    environment_samples: list[dict[str, int | float | bool]] = []
    positions: list[dict[str, float]] = []

    try:
        initial_state = await _retry_read(
            lambda: client.get_environment_state(session),
            args.read_attempts,
            args.read_retry_interval,
        )
    except (OSError, TimeoutError, UltraError) as err:
        report["environment_initial_read"] = {
            "status": "failed",
            "error": str(err) or type(err).__name__,
        }
    else:
        environment_samples.append(dict(initial_state.values))
        report["environment_initial_read"] = "passed"

    def _on_position(update: Any) -> None:
        for target in update.targets:
            positions.append(
                {
                    "x": target.x,
                    "y": target.y,
                    "z": target.z,
                    "distance": round(target.distance, 3),
                }
            )

    runtime_capabilities = await client.get_runtime_capabilities(session)
    report["runtime_capabilities"] = sorted(capability.value for capability in runtime_capabilities)
    report["radar_generation"] = "60_ghz"
    controller: _RadarController = _DirectRadarController(client, session)
    subscription = UltraPositionSubscription(
        client,
        session,
        callback=_on_position,
        subscription_timeout=60,
        renew_interval=40,
        position_ttl=30,
    )
    await subscription.start()
    try:
        try:
            await subscription.wait_confirmed(args.command_timeout * 4)
        except TimeoutError:
            subscription_state = subscription.state
            report["local_udp_subscription"] = {
                "status": "failed",
                "error": subscription_state.last_error or "confirmation timed out",
                "local_port": subscription_state.local_port,
            }
            await subscription.stop()
        else:
            controller = subscription
            report["local_udp_subscription"] = "confirmed"

        controller = _RetryingRadarController(
            controller,
            args.read_attempts,
            args.read_retry_interval,
        )

        try:
            radar_status = await controller.get_radar_status()
        except (OSError, TimeoutError, UltraError) as err:
            report["radar_status"] = {
                "status": "failed",
                "error": str(err) or type(err).__name__,
            }
        else:
            report["radar_status"] = asdict(radar_status)
            if args.exercise_controls:
                report["controls"] = await _exercise_controls(
                    controller,
                    radar_status,
                )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + args.observe_seconds
        while True:
            try:
                environment_state = await _retry_read(
                    lambda: client.get_environment_state(session),
                    args.read_attempts,
                    args.read_retry_interval,
                )
            except (OSError, TimeoutError, UltraError) as err:
                report["environment_poll_error"] = str(err) or type(err).__name__
            else:
                environment_samples.append(dict(environment_state.values))
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            await asyncio.sleep(min(args.sample_interval, remaining))
    finally:
        await subscription.stop()

    await _validate_reauthentication(
        report,
        client,
        session,
        args.local_key,
        args.read_attempts,
        args.read_retry_interval,
    )

    fields = sorted({field for sample in environment_samples for field in sample})
    report["environment"] = {
        "sample_count": len(environment_samples),
        "fields_seen": fields,
        "distinct_values": {
            field: len({json.dumps(sample.get(field)) for sample in environment_samples}) for field in fields
        },
        "latest": environment_samples[-1] if environment_samples else {},
    }
    report["position"] = {
        "target_samples": len(positions),
        "has_finite_xyz": any(all(math.isfinite(target[axis]) for axis in ("x", "y", "z")) for target in positions),
        "samples": positions[:5],
    }
    return report


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--mac")
    parser.add_argument(
        "--local-key-file",
        dest="local_key",
        type=_read_local_key_file,
        help="mode-0600 file containing the 16-byte local key as 32 hexadecimal characters",
    )
    parser.add_argument("--command-device-type", type=lambda value: int(value, 0))
    parser.add_argument("--observe-seconds", type=float, default=30)
    parser.add_argument("--sample-interval", type=float, default=2)
    parser.add_argument("--discovery-timeout", type=float, default=5)
    parser.add_argument("--command-timeout", type=float, default=5)
    parser.add_argument("--preferred-command-timeout", type=float, default=15)
    parser.add_argument("--auth-timeout", type=float, default=15)
    parser.add_argument("--auth-attempts", type=int, default=3)
    parser.add_argument("--auth-retry-interval", type=float, default=2)
    parser.add_argument("--read-attempts", type=int, default=3)
    parser.add_argument("--read-retry-interval", type=float, default=1)
    parser.add_argument("--exercise-controls", action="store_true")
    return parser.parse_args()


def main() -> None:
    """Run the command-line hardware validator."""
    report = asyncio.run(validate(_parse_args()))
    print(json.dumps(report, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
