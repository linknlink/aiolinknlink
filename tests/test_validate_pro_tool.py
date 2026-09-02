from __future__ import annotations

import argparse
import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aiolinknlink import TYPE_EMOTION_PRO_RADAR, UltraDevice
from aiolinknlink.models import UltraEnvironmentState, UltraSession


def _load_validator() -> ModuleType:
    path = Path(__file__).parents[1] / "tools" / "validate_pro.py"
    spec = importlib.util.spec_from_file_location("validate_pro", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_read_local_key_file(tmp_path: Path) -> None:
    validator = _load_validator()
    path = tmp_path / "local-key"
    path.write_text("30313233343536373839616263646566\n", encoding="ascii")
    path.chmod(0o600)

    assert validator._read_local_key_file(str(path)) == b"0123456789abcdef"


@pytest.mark.parametrize(
    ("contents", "mode", "message"),
    [
        ("00", 0o600, "32 hexadecimal"),
        ("not-hex", 0o600, "32 hexadecimal"),
        ("30313233343536373839616263646566", 0o640, "group or other"),
    ],
)
def test_read_local_key_file_rejects_invalid_input(
    tmp_path: Path,
    contents: str,
    mode: int,
    message: str,
) -> None:
    validator = _load_validator()
    path = tmp_path / "local-key"
    path.write_text(contents, encoding="ascii")
    path.chmod(mode)

    with pytest.raises(argparse.ArgumentTypeError, match=message):
        validator._read_local_key_file(path)


async def test_validate_proves_reauthentication_with_fresh_state_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator = _load_validator()
    device = UltraDevice(
        id="020000000110",
        ip="198.51.100.8",
        port=80,
        mac="02:00:00:00:01:10",
        type_id=TYPE_EMOTION_PRO_RADAR,
    )
    session = UltraSession(
        device=device,
        session_key=b"0123456789abcdef",
        auth_device_type=0xE3AC,
        auth_status="provided",
    )
    initial = UltraEnvironmentState(
        device_id=device.id,
        values={"occupancy": False, "absence_delay": 60},
        available_fields=frozenset({"occupancy", "absence_delay"}),
        received_at=datetime.now(UTC),
    )
    after_reauthentication = UltraEnvironmentState(
        device_id=device.id,
        values={"occupancy": True, "absence_delay": 60},
        available_fields=frozenset({"occupancy", "absence_delay"}),
        received_at=datetime.now(UTC),
    )

    async def reauthenticate(current: UltraSession, *, local_key: bytes) -> None:
        assert local_key == b"0123456789abcdef"
        current.session_key = b"0123456789abcdef"
        current.auth_status = "provided"

    client = SimpleNamespace(
        discover_host=AsyncMock(return_value=device),
        connect=AsyncMock(return_value=session),
        get_environment_state=AsyncMock(side_effect=[initial, after_reauthentication]),
        reauthenticate=AsyncMock(side_effect=reauthenticate),
    )
    monkeypatch.setattr(validator, "UltraClient", lambda **_kwargs: client)

    report = await validator.validate(
        argparse.Namespace(
            host=device.ip,
            local_key=b"0123456789abcdef",
            discovery_timeout=1,
            command_timeout=1,
            auth_timeout=1,
            broadcast_attempts=1,
            discovery_retry_interval=0,
            exercise_absence_delay=False,
        )
    )

    assert report["reauthentication"] == "provided"
    assert report["reauthenticated_state"] == {"occupancy": True, "absence_delay": 60}
    assert client.get_environment_state.await_count == 2
