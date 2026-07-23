#!/usr/bin/env python3
"""여러 API 페이지(JSON)를 하나의 lgu_channel_catalog.json 으로 병합."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "lgu_channel_catalog.json"


def load_multi_json(text: str) -> list[dict]:
    """파일 안에 {..}{..} 형태로 이어진 JSON 루트 여러 개 파싱."""
    text = text.strip()
    if not text:
        return []
    dec = json.JSONDecoder()
    objs: list[dict] = []
    i = 0
    n = len(text)
    while i < n:
        while i < n and text[i] in " \t\r\n":
            i += 1
        if i >= n:
            break
        obj, end = dec.raw_decode(text, i)
        if isinstance(obj, dict):
            objs.append(obj)
        i = end
    return objs


def merge_pages(pages: list[dict]) -> dict:
    by_id: dict[int, dict] = {}
    for page in pages:
        for row in page.get("data") or []:
            cid = row.get("id")
            if cid is None:
                continue
            by_id[int(cid)] = row
    data = sorted(by_id.values(), key=lambda r: int(r["id"]), reverse=True)
    return {"totalRows": len(data), "data": data}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "inputs",
        nargs="*",
        help="JSON 파일 또는 디렉터리(내부 *.json). 미지정 시 stdin.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=_DEFAULT_OUT,
        help=f"출력 경로 (기본: {_DEFAULT_OUT})",
    )
    args = parser.parse_args()

    pages: list[dict] = []
    paths: list[Path] = []
    if args.inputs:
        for arg in args.inputs:
            p = Path(arg)
            if p.is_dir():
                paths.extend(sorted(p.glob("*.json")))
            elif p.is_file():
                paths.append(p)
            else:
                print(f"skip (not found): {p}", file=sys.stderr)
    else:
        text = sys.stdin.read()
        pages.extend(load_multi_json(text))
        paths = []

    for path in paths:
        raw = path.read_text(encoding="utf-8")
        pages.extend(load_multi_json(raw))

    if not pages:
        print("No JSON pages loaded.", file=sys.stderr)
        return 1

    merged = merge_pages(pages)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(merged, ensure_ascii=False, indent=4) + "\n",
        encoding="utf-8",
    )
    print(
        f"Wrote {args.output} — {merged['totalRows']} channels "
        f"(from {len(pages)} page(s), {len(merged['data'])} unique ids)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
