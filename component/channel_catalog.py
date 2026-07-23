"""LGU 채널 카탈로그 — receive cue 의 ProgramProviderChannel.id 매핑."""

from __future__ import annotations

import json
import os
import re
from typing import Any

_DEFAULT_CATALOG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "lgu_channel_catalog.json",
)
_DEFAULT_ALIASES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data",
    "channel_name_aliases.json",
)

_CHANNEL_ID_RE = re.compile(
    r"ProgramProviderChannel\s*\([^)]*?\bid\s*=\s*(\d+)",
    re.IGNORECASE,
)

_catalog_by_id: dict[int, dict[str, Any]] | None = None
_catalog_path_used: str | None = None
_aliases_by_name: dict[str, list[str]] | None = None
_aliases_path_used: str | None = None


def get_catalog_path() -> str:
    return os.environ.get("LGU_CHANNEL_CATALOG_PATH", _DEFAULT_CATALOG_PATH).strip()


def get_aliases_path() -> str:
    return os.environ.get("LGU_CHANNEL_ALIASES_PATH", _DEFAULT_ALIASES_PATH).strip()


def load_channel_name_aliases(path: str | None = None, force_reload: bool = False) -> dict[str, list[str]]:
    """편성 채널명 → 카탈로그 title/tags 후보."""
    global _aliases_by_name, _aliases_path_used
    path = path or get_aliases_path()
    if not force_reload and _aliases_by_name is not None and _aliases_path_used == path:
        return _aliases_by_name

    _aliases_by_name = {}
    _aliases_path_used = path
    if not os.path.isfile(path):
        return _aliases_by_name

    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    if isinstance(payload, dict):
        for key, values in payload.items():
            if not key:
                continue
            aliases = [str(v) for v in (values or []) if v]
            _aliases_by_name[str(key)] = aliases
    return _aliases_by_name


def load_channel_catalog(path: str | None = None, force_reload: bool = False) -> dict[int, dict]:
    """id → 채널 레코드. 파일 없으면 빈 dict."""
    global _catalog_by_id, _catalog_path_used
    path = path or get_catalog_path()
    if not force_reload and _catalog_by_id is not None and _catalog_path_used == path:
        return _catalog_by_id

    _catalog_by_id = {}
    _catalog_path_used = path
    if not os.path.isfile(path):
        return _catalog_by_id

    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    for row in payload.get("data") or []:
        cid = row.get("id")
        if cid is not None:
            _catalog_by_id[int(cid)] = row
    return _catalog_by_id


def lookup_channel(channel_id: int | str | None) -> dict[str, Any] | None:
    if channel_id is None:
        return None
    try:
        key = int(channel_id)
    except (TypeError, ValueError):
        return None
    return load_channel_catalog().get(key)


def parse_program_provider_channel_id(line: str) -> int | None:
    """receive cue 줄에서 ProgramProviderChannel.id 추출."""
    m = _CHANNEL_ID_RE.search(line)
    return int(m.group(1)) if m else None


def _normalize_title(text: str) -> str:
    s = re.sub(r"[\s\-_]+", "", (text or "").lower())
    # 편성 시트 표기 ↔ 카탈로그 title (채널A 플러스 ↔ 채널A+)
    s = s.replace("플러스", "+").replace("plus", "+")
    return s


def parse_register_cue_pp_id(line: str) -> int | None:
    """register cue 줄의 ppId (= 카탈로그 채널 id)."""
    m = re.search(r"ppId\s*=\s*(\d+)", line, re.IGNORECASE)
    return int(m.group(1)) if m else None


def resolve_expected_catalog_ids(channel_name: str | None) -> set[int]:
    """
    gspread 편성 채널명 → 카탈로그 id 후보.
    STB 채널 번호(322)와 카탈로그 id(998)는 별개 — title/tags 및 channel_name_aliases 로 매칭.
    """
    if not channel_name:
        return set()
    catalog = load_channel_catalog()
    if not catalog:
        return set()

    names_to_match: list[str] = [channel_name]
    aliases = load_channel_name_aliases().get(channel_name) or []
    names_to_match.extend(aliases)

    exact_ids: set[int] = set()
    # (id, title_norm 길이) — 짧은 title 이 긴 편성명에 끼어드는 것 방지
    fuzzy_candidates: list[tuple[int, int]] = []
    seen_norms: set[str] = set()
    for name in names_to_match:
        name_norm = _normalize_title(name)
        if not name_norm or name_norm in seen_norms:
            continue
        seen_norms.add(name_norm)
        for cid, row in catalog.items():
            title = row.get("title") or ""
            title_norm = _normalize_title(title)
            tag_norms = [_normalize_title(str(t)) for t in (row.get("tags") or [])]
            if name_norm == title_norm or name_norm in tag_norms:
                exact_ids.add(int(cid))
                continue
            if name_norm in title_norm and len(title_norm) >= len(name_norm) * 0.6:
                fuzzy_candidates.append((int(cid), len(title_norm)))
            elif title_norm in name_norm and len(name_norm) >= len(title_norm) * 0.75:
                fuzzy_candidates.append((int(cid), len(title_norm)))

    if exact_ids:
        return exact_ids
    if not fuzzy_candidates:
        return set()
    best_len = max(length for _, length in fuzzy_candidates)
    return {cid for cid, length in fuzzy_candidates if length == best_len}


def cue_id_matches_slot(cue_channel_id: int | None, expected_ids: set[int]) -> bool:
    """편성 채널과 cue 의 ppId/ProgramProviderChannel.id 일치 여부."""
    if not expected_ids:
        return False
    if cue_channel_id is None:
        return False
    return int(cue_channel_id) in expected_ids


def format_channel_ref(channel_id: int | None, line: str | None = None) -> str:
    """로그용: id=998 캐리TV (forKids) 또는 id 미상."""
    if channel_id is None and line:
        channel_id = parse_program_provider_channel_id(line)
    if channel_id is None:
        return "채널 id 미파싱"
    row = lookup_channel(channel_id)
    if not row:
        return f"채널 id={channel_id} (카탈로그 없음)"
    title = row.get("title") or "?"
    kids = row.get("forKids")
    return f"채널 id={channel_id} {title} (forKids={kids})"
