"""Tests for the eMotion Ultra client."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from aiolinknlink import (
    TYPE_ULTRA2,
    UltraAuthError,
    UltraClient,
    UltraDevice,
)
from aiolinknlink.protocol import dna, emotion

DEVICE = UltraDevice(
    id="e04b410167bb",
    ip="192.168.1.8",
    port=80,
    mac="e0:4b:41:01:67:bb",
    type_id=TYPE_ULTRA2,
)


async def test_connect_and_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    session_key = b"0123456789abcdef"
    auth_response = b"\x00" * 4 + session_key + b"\x00" * 12
    gateway_response = b'\x26\x00\x00\x00{"rssi":-43,"lb_online1":1}'
    empty_list = emotion.build_subdevice_frame(emotion.CMD_SUBDEVICE_LIST_RESPONSE, {"list": []})
    send = AsyncMock(side_effect=[auth_response, gateway_response, empty_list])
    monkeypatch.setattr(dna, "send_encrypted", send)

    client = UltraClient(command_timeout=0.1)
    session = await client.connect(DEVICE)
    state = await client.refresh(session)

    assert session.session_key == session_key
    assert state.online is True
    assert state.values["wifi_rssi"] == -43
    assert send.await_count == 3


async def test_connect_requires_mac() -> None:
    client = UltraClient()
    with pytest.raises(UltraAuthError, match="missing mac"):
        await client.connect(UltraDevice(id="device", ip="192.168.1.8", port=80))


async def test_control_builds_subdevice_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    client = UltraClient()
    session = await _session(client, monkeypatch)
    send = AsyncMock(return_value=b"ok")
    monkeypatch.setattr(dna, "send_encrypted", send)

    await client.control(session, "power", "radar-1", {"state": "on"})

    command = send.await_args.args[3]
    frame = emotion.parse_subdevice_frame(command)
    payload = emotion.parse_subdevice_json_payload(frame)
    assert frame.command_type == emotion.CMD_SET_STATUS
    assert payload == {"did": "radar-1", "power": True}


async def _session(client: UltraClient, monkeypatch: pytest.MonkeyPatch):
    session_key = b"0123456789abcdef"
    response = b"\x00" * 4 + session_key + b"\x00" * 12
    monkeypatch.setattr(dna, "send_encrypted", AsyncMock(return_value=response))
    return await client.connect(DEVICE)
