"""Resolve STB platform id and load platforms/channel_map_*.json configs."""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

# Aliases → canonical id used as platforms/channel_map_<id>.json
_PLATFORM_ALIASES = {
    "uplus": "uplus",
    "u+": "uplus",
    "lgu": "uplus",
    "lg_uplus": "uplus",
    "art": "uplus",
    "skb": "skb",
    "bigad": "skb",
    "kt": "kt",
}

DEFAULT_PLATFORM = "uplus"
_PLATFORMS_DIR = Path(__file__).resolve().parent.parent / "platforms"


def normalize_platform(name: str | None) -> str:
    if not name:
        return DEFAULT_PLATFORM
    key = str(name).strip().lower()
    if key not in _PLATFORM_ALIASES:
        known = ", ".join(sorted(set(_PLATFORM_ALIASES.values())))
        raise ValueError(f"Unknown platform {name!r}. Use one of: {known}")
    return _PLATFORM_ALIASES[key]


def resolve_platform(explicit: str | None = None) -> str:
    """Pick platform: explicit arg → STB_PLATFORM → PLATFORM → default uplus."""
    if explicit:
        return normalize_platform(explicit)
    env = os.environ.get("STB_PLATFORM") or os.environ.get("PLATFORM")
    return normalize_platform(env)


def platforms_dir() -> Path:
    return _PLATFORMS_DIR


def channel_map_path(platform: str | None = None) -> Path:
    """Path to platforms/channel_map_<id>.json for the resolved platform."""
    pid = resolve_platform(platform)
    return _PLATFORMS_DIR / f"channel_map_{pid}.json"


@lru_cache(maxsize=8)
def _load_platform_config_cached(pid: str) -> dict[str, Any]:
    path = _PLATFORMS_DIR / f"channel_map_{pid}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Platform channel map not found: {path}")
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid platform config (not an object): {path}")
    channels = data.get("channels") or {}
    if not isinstance(channels, dict):
        raise ValueError(f"Invalid channels map in {path}")
    data = dict(data)
    data["channels"] = {str(k): int(v) for k, v in channels.items()}
    data["id"] = data.get("id") or pid
    data["schedule_section"] = data.get("schedule_section") or pid
    return data


def load_platform_config(platform: str | None = None) -> dict[str, Any]:
    """Load platforms/channel_map_<id>.json. None → STB_PLATFORM / PLATFORM / uplus."""
    return _load_platform_config_cached(resolve_platform(platform))


def clear_platform_config_cache() -> None:
    _load_platform_config_cached.cache_clear()


def get_schedule_section(platform: str | None = None) -> str:
    """Return schedule_loader section (skb|uplus).

    KT has no sheet block of its own — channel_map_kt.json points
    schedule_section at uplus or skb while channels stay KT-specific.
    """
    return str(load_platform_config(platform)["schedule_section"])


def get_channel_map(platform: str | None = None) -> dict[str, int]:
    return dict(load_platform_config(platform)["channels"])
