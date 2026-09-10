"""Checks for the self-contained HACS package."""

from __future__ import annotations

import hashlib
import json
import tomllib
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

    def package_files(root: Path) -> list[Path]:
        return sorted(
            path.relative_to(root)
            for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        )

    source_files = package_files(SOURCE)
    target_files = package_files(TARGET)
    assert target_files == source_files
    for relative_path in source_files:
        assert _file_hash(SOURCE / relative_path) == _file_hash(TARGET / relative_path)


def test_manifest_and_library_versions_match() -> None:
    """The HACS manifest and bundled library release must stay aligned."""
    manifest = json.loads((ROOT / "custom_components" / "linknlink" / "manifest.json").read_text(encoding="utf-8"))
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert manifest["version"] == pyproject["project"]["version"]
