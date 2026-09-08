#!/usr/bin/env python3
"""Synchronize the client library into the HACS integration package."""

from __future__ import annotations

import shutil
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src" / "aiolinknlink"
TARGET = ROOT / "custom_components" / "linknlink" / "aiolinknlink"


def main() -> None:
    """Replace the bundled library with the current source checkout."""
    if not SOURCE.is_dir():
        raise SystemExit(f"source library does not exist: {SOURCE}")
    if TARGET.exists():
        shutil.rmtree(TARGET)
    shutil.copytree(SOURCE, TARGET)
    print(f"Synchronized {SOURCE} -> {TARGET}")


if __name__ == "__main__":
    main()
