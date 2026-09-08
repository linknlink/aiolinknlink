"""Checks for the self-contained HACS package."""

from __future__ import annotations

import hashlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "aiolinknlink"
TARGET = ROOT / "custom_components" / "linknlink" / "aiolinknlink"


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def test_bundled_client_library_matches_source() -> None:
    """Every source client file must be present and byte-identical in HACS."""
    source_files = sorted(path.relative_to(SOURCE) for path in SOURCE.rglob("*") if path.is_file())
    target_files = sorted(path.relative_to(TARGET) for path in TARGET.rglob("*") if path.is_file())
    assert target_files == source_files
    for relative_path in source_files:
        assert _file_hash(SOURCE / relative_path) == _file_hash(TARGET / relative_path)
