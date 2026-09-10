#!/usr/bin/env python3
"""Validate the repository layout required by the LinknLink HACS package."""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "linknlink"
BUNDLED_LIBRARY = COMPONENT / "aiolinknlink"


def _load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        raise SystemExit(f"invalid JSON: {path}: {err}") from err
    if not isinstance(value, dict):
        raise SystemExit(f"JSON root must be an object: {path}")
    return value


def main() -> int:
    """Check required files and manifest metadata."""
    hacs = _load_json(ROOT / "hacs.json")
    if hacs.get("content_in_root") is not False:
        raise SystemExit("hacs.json must set content_in_root to false")

    manifest = _load_json(COMPONENT / "manifest.json")
    required_manifest_keys = {
        "domain",
        "name",
        "version",
        "config_flow",
        "documentation",
        "issue_tracker",
        "integration_type",
        "iot_class",
    }
    missing = sorted(required_manifest_keys - manifest.keys())
    if missing:
        raise SystemExit(f"manifest.json is missing keys: {', '.join(missing)}")
    if manifest["domain"] != "linknlink":
        raise SystemExit("manifest domain must be linknlink")
    if not isinstance(manifest["version"], str) or not manifest["version"]:
        raise SystemExit("manifest version must be a non-empty string")

    try:
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project_version = pyproject["project"]["version"]
    except (KeyError, OSError, tomllib.TOMLDecodeError) as err:
        raise SystemExit(f"invalid project version in pyproject.toml: {err}") from err
    if not isinstance(project_version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", project_version):
        raise SystemExit("pyproject.toml project.version must be a stable x.y.z version")
    if manifest["version"] != project_version:
        raise SystemExit(
            f"manifest version {manifest['version']} does not match pyproject.toml version {project_version}"
        )

    release_ref = os.environ.get("GITHUB_REF_NAME", "")
    if release_ref.startswith("v") and release_ref[1:] != manifest["version"]:
        raise SystemExit(f"release tag {release_ref} does not match manifest version {manifest['version']}")

    for relative_path in (
        "custom_components/linknlink/__init__.py",
        "custom_components/linknlink/config_flow.py",
        "custom_components/linknlink/strings.json",
        "custom_components/linknlink/translations/en.json",
        "custom_components/linknlink/translations/zh-Hans.json",
        "custom_components/linknlink/aiolinknlink/__init__.py",
    ):
        if not (ROOT / relative_path).is_file():
            raise SystemExit(f"required HACS file is missing: {relative_path}")

    if not BUNDLED_LIBRARY.is_dir():
        raise SystemExit(f"bundled client library is missing: {BUNDLED_LIBRARY}")
    print(f"HACS layout is valid for LinknLink {manifest['version']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
