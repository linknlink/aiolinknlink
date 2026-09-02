from __future__ import annotations

import argparse
import importlib.util
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from aiolinknlink import TYPE_ULTRA1, UltraDevice, UltraEnvironmentState, UltraSession


def _load_validator() -> ModuleType:
    path = Path(__file__).parents[1] / "tools" / "validate_ultra1.py"
    spec = importlib.util.spec_from_file_location("validate_ultra1", path)
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


async def test_reauthentication_reuses_provisioning_key_and_proves_command() -> None:
    validator = _load_validator()
    key = b"0123456789abcdef"
    device = UltraDevice(
        id="020000000110",
        ip="198.51.100.8",
        port=80,
        mac="02:00:00:00:01:10",
        type_id=TYPE_ULTRA1,
    )
    session = UltraSession(device=device, session_key=key, auth_status="provided")
    state = UltraEnvironmentState(
        device_id=device.id,
        values={"occupancy": True, "target_count": 1},
        available_fields=frozenset({"occupancy", "target_count"}),
        received_at=datetime.now(UTC),
    )

    async def reauthenticate(current: UltraSession, *, local_key: bytes) -> None:
        assert local_key == key
        current.session_key = key
        current.auth_status = "provided"

    client = SimpleNamespace(
        reauthenticate=AsyncMock(side_effect=reauthenticate),
        get_environment_state=AsyncMock(return_value=state),
    )
    report: dict[str, object] = {}

    await validator._validate_reauthentication(report, client, session, key, 2, 0)

    assert report["reauthentication"] == "passed"
    assert report["reauthenticated_fields"] == ["occupancy", "target_count"]
    assert report["session_key_refreshed"] is False
    client.get_environment_state.assert_awaited_once_with(session)
