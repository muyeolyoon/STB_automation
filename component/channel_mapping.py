# component/channel_mapping.py
"""Channel name → number lookup, backed by platforms/channel_map_*.json."""

from __future__ import annotations

from component.platform_config import get_channel_map

# Backward-compatible export: default platform (STB_PLATFORM / uplus) map
CHANNEL_MAP = get_channel_map()


def get_channel_number(name: str, platform: str | None = None):
    """Return STB channel number for display name, or None if unmapped.

    platform: optional skb|uplus|kt (aliases ok). If omitted, uses
    STB_PLATFORM / PLATFORM env, else uplus.
    """
    return get_channel_map(platform).get(name)


def reload_channel_map(platform: str | None = None) -> dict:
    """Refresh CHANNEL_MAP after env change or JSON edit (tests / REPL)."""
    global CHANNEL_MAP
    from component.platform_config import clear_platform_config_cache

    clear_platform_config_cache()
    CHANNEL_MAP = get_channel_map(platform)
    return CHANNEL_MAP
