"""STB 화면 캡처 — ADB screencap (일부 기기는 su root 필요)."""

from __future__ import annotations

import os
import subprocess
import time
from typing import Any

from component.obs_capture import ocr_phrase_on_image, save_ad_broadcast_chat_preview


def _adb_cmd(device: str, *args: str) -> list[str]:
    return ["adb", "-s", device, *args]


def capture_png_via_adb(
    device: str,
    save_path: str,
    *,
    use_su: bool = True,
) -> str:
    """
    PNG 저장. LGU STB 등은 shell screencap 불가 → su 0 screencap 필요.
    """
    device = device.strip()
    os.makedirs(os.path.dirname(os.path.abspath(save_path)), exist_ok=True)
    remote = f"/sdcard/qa_cap_{int(time.time() * 1000)}.png"
    attempts: list[tuple[str, list[str]]] = []
    if use_su:
        attempts.append(("su", _adb_cmd(device, "shell", "su", "0", "screencap", "-p", remote)))
    attempts.append(("shell", _adb_cmd(device, "shell", "screencap", "-p", remote)))

    last_err = ""
    for label, cmd in attempts:
        cap = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
        if cap.returncode != 0:
            last_err = (cap.stderr or cap.stdout or f"rc={cap.returncode}").strip()
            continue
        pull = subprocess.run(
            _adb_cmd(device, "pull", remote, save_path),
            capture_output=True,
            text=True,
            errors="replace",
        )
        if pull.returncode != 0:
            last_err = (pull.stderr or pull.stdout or "pull failed").strip()
            continue
        size = os.path.getsize(save_path) if os.path.isfile(save_path) else 0
        # 정상 1080p PNG는 ~수백KB+ · 10KB 대는 깨진/빈 캡처
        if size < 50000:
            last_err = f"캡처 파일 너무 작음 ({size} bytes, {label})"
            continue
        subprocess.run(
            _adb_cmd(device, "shell", "rm", "-f", remote),
            capture_output=True,
        )
        return save_path

    raise RuntimeError(f"ADB screencap 실패 ({device}): {last_err or 'unknown'}")


def analyze_phrase_on_image_path(
    save_path: str,
    phrase: str,
    *,
    capture_method: str = "file",
    device: str | None = None,
) -> dict[str, Any]:
    """이미 저장된 PNG에 대해 우측 상단 OCR + Chat 시인성 평가."""
    result: dict[str, Any] = {
        "ok": False,
        "path": save_path,
        "ocr_text": None,
        "message": "",
        "ocr_available": False,
        "capture_method": capture_method,
    }
    if device:
        result["device"] = device
    if not save_path or not os.path.isfile(save_path):
        result["message"] = f"캡처 파일 없음: {save_path}"
        return result

    try:
        from PIL import Image

        img = Image.open(save_path)
    except ImportError:
        result["message"] = "Pillow 미설치 — 캡처만 저장됨"
        return result
    except Exception as e:
        result["message"] = f"이미지 열기 실패: {e}"
        return result

    ocr_result = ocr_phrase_on_image(img, phrase)
    chat_meta = save_ad_broadcast_chat_preview(img, save_path)
    result["badge_visible"] = bool(chat_meta.get("visible"))
    result["visibility_score"] = float(chat_meta.get("score") or 0.0)
    result["visibility_note"] = chat_meta.get("note") or ""
    if chat_meta.get("visible") and chat_meta.get("path"):
        result["chat_path"] = chat_meta["path"]
    if not ocr_result.get("ocr_available"):
        result["message"] = ocr_result.get("note") or "OCR 불가 — 저장된 캡처로 수동 확인"
        return result

    result["ocr_available"] = True
    result["ocr_text"] = (ocr_result.get("ocr_text") or "").strip()
    result["ok"] = bool(ocr_result.get("ok"))
    result["ocr_region"] = ocr_result.get("matched_region")
    result["ocr_variant"] = ocr_result.get("matched_variant")
    if result["ok"] and not result.get("badge_visible"):
        result["ok"] = False
    method_label = "ADB" if capture_method == "adb" else (
        "OBS" if capture_method == "obs" else capture_method
    )
    if result["ok"]:
        extra = ""
        if result.get("ocr_variant"):
            extra = f" [{result['ocr_region']}/{result['ocr_variant']}]"
        result["message"] = f"'{phrase}' 문구 확인 ({method_label}){extra}"
    else:
        note = result.get("visibility_note") or ocr_result.get("note") or ""
        result["message"] = (
            f"'{phrase}' 미검출 ({method_label} 상단·우측)"
            + (f" — {note}" if note else "")
        )
    return result


def check_phrase_on_device(
    device: str,
    save_path: str,
    phrase: str,
) -> dict[str, Any]:
    """ADB 캡처 → 우측 상단 crop → OCR."""
    result: dict[str, Any] = {
        "ok": False,
        "path": None,
        "ocr_text": None,
        "message": "",
        "ocr_available": False,
        "capture_method": "adb",
        "device": device,
    }
    try:
        result["path"] = capture_png_via_adb(device, save_path)
    except Exception as e:
        result["message"] = f"ADB 캡처 실패: {e}"
        return result

    analyzed = analyze_phrase_on_image_path(
        result["path"], phrase, capture_method="adb", device=device
    )
    analyzed["path"] = result["path"]
    analyzed["capture_method"] = "adb"
    analyzed["device"] = device
    return analyzed


def adb_capture_path(base_dir: str, tag: str = "ad_broadcast") -> str:
    ts = time.strftime("%Y%m%d_%H%M%S")
    return os.path.join(base_dir, "adb_captures", f"{tag}_{ts}.png")
