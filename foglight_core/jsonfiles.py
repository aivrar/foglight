"""Bounded JSON loading for packaged configuration and local catalogs."""

from __future__ import annotations

import json
from pathlib import Path


def load_bounded_json(path: str | Path, *, max_bytes: int = 2 * 1024 * 1024):
    if max_bytes < 1:
        raise ValueError("JSON file cap must be positive")
    source = Path(path)
    if source.stat().st_size > max_bytes:
        raise ValueError(f"JSON file exceeds {max_bytes} bytes")
    with source.open("rb") as handle:
        raw = handle.read(max_bytes + 1)
    if len(raw) > max_bytes:
        raise ValueError(f"JSON file exceeds {max_bytes} bytes")
    return json.loads(raw.decode("utf-8"))
