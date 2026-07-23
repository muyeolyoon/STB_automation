"""OBS WebSocket 화면 캡처 및 UI 텍스트 확인 (예: 우측 상단 '광고 방송').

필요:
  - OBS Studio → 도구 → WebSocket 서버 설정
  - v5 기본 포트 4455 (obsws-python) / 일부 환경은 v4 호환 4444 (obswebsocket legacy)
  - pip install obsws-python obswebsocket
  - OCR(선택): pip install pillow pytesseract + Tesseract 한국어(kor) 데이터
"""

from __future__ import annotations

import base64
import os
import re
import socket
from datetime import datetime
from typing import Any, Literal

OBS_WS_PORTS = (4455, 4444)


def probe_obs_websocket(host: str = "127.0.0.1", port: int = 4455, timeout: float = 2.0) -> tuple[bool, str]:
    """OBS WebSocket 포트 열림 여부 (TCP)."""
    host = (host or "127.0.0.1").strip() or "127.0.0.1"
    try:
        port = int(port)
    except (TypeError, ValueError):
        port = 4455
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True, f"{host}:{port} TCP 연결 가능"
    except ConnectionRefusedError:
        return False, (
            f"{host}:{port} 연결 거부 — OBS 실행·WebSocket 서버 활성화를 확인하세요."
        )
    except OSError as e:
        return False, f"{host}:{port} 접속 실패: {e}"


def _try_v5_client(host: str, port: int, password: str, timeout: int = 6):
    from obsws_python import ReqClient

    cl = ReqClient(host=host, port=port, password=password or "", timeout=timeout)
    cl.get_version()
    return cl


def _try_v4_client(host: str, port: int, password: str):
    from obswebsocket import obsws

    cl = obsws(host, port, password or "", legacy=True)
    cl.connect()
    return cl


def resolve_obs_connection(
    host: str = "127.0.0.1",
    port: int | None = None,
    password: str = "",
    *,
    try_legacy_passwords: bool = True,
) -> dict[str, Any]:
    """
  OBS WebSocket API 자동 선택.
  Returns: host, port, password, api ('v5'|'v4'), message
    """
    host = (host or "127.0.0.1").strip() or "127.0.0.1"
    password = password or ""
    ports: list[int] = []
    if port is not None:
        ports.append(int(port))
    for p in OBS_WS_PORTS:
        if p not in ports:
            ports.append(p)

    errors: list[str] = []

    for p in ports:
        if not probe_obs_websocket(host, p)[0]:
            continue
        # 4444는 보통 v4 호환(4.9.x) — v5 클라이언트로 붙이면 긴 타임아웃만 발생
        if p != 4444:
            try:
                cl = _try_v5_client(host, p, password)
                cl.disconnect()
                return {
                    "host": host,
                    "port": p,
                    "password": password,
                    "api": "v5",
                    "message": f"{host}:{p} WebSocket v5 연결 OK",
                }
            except Exception as e:
                errors.append(f"v5@{p}: {type(e).__name__}")

        legacy_passwords: list[str] = []
        if password:
            legacy_passwords.append(password)
        elif try_legacy_passwords:
            legacy_passwords.extend(["123456", ""])
        else:
            legacy_passwords.append("")

        for pw in legacy_passwords:
            try:
                cl = _try_v4_client(host, p, pw)
                cl.disconnect()
                note = f" (비밀번호 {'설정됨' if pw else '없음'})"
                return {
                    "host": host,
                    "port": p,
                    "password": pw,
                    "api": "v4",
                    "message": f"{host}:{p} WebSocket v4 호환 연결 OK{note}",
                }
            except Exception as e:
                errors.append(f"v4@{p}: {type(e).__name__}")

    detail = "; ".join(errors[:4]) if errors else "열린 포트 없음"
    return {
        "host": host,
        "port": port or OBS_WS_PORTS[0],
        "password": password,
        "api": None,
        "message": (
            f"OBS WebSocket API 연결 실패 ({detail}). "
            f"스크립트는 v5(4455) 또는 v4(4444)를 시도합니다. "
            "OBS → 도구 → WebSocket 서버 설정에서 포트·비밀번호 확인, "
            "또는 $env:OBS_PORT / $env:OBS_PASSWORD 설정."
        ),
    }


def format_obs_connection_error(exc: BaseException, host: str, port: int, api: str | None = None) -> str:
    if isinstance(exc, ConnectionRefusedError):
        return (
            f"OBS WebSocket({host}:{port}) 연결 거부 — "
            "OBS가 꺼져 있거나 해당 포트에 서버가 없습니다."
        )
    name = type(exc).__name__
    if "Timeout" in name:
        return (
            f"OBS WebSocket({host}:{port}) 응답 없음 — "
            "포트는 열려 있으나 프로토콜/비밀번호가 맞지 않을 수 있습니다. "
            "OBS 설정 포트(4444/4455)와 $env:OBS_PASSWORD 를 확인하세요."
        )
    if api:
        return f"OBS 캡처 실패 ({api}): {exc}"
    return f"OBS 캡처 실패: {exc}"


class OBSScreenCapture:
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 4455,
        password: str = "",
        api: Literal["v5", "v4"] | None = None,
    ):
        self.host = (host or "127.0.0.1").strip() or "127.0.0.1"
        self.port = int(port)
        self.password = password or ""
        self.api: Literal["v5", "v4"] | None = api

    def _ensure_api(self):
        if self.api:
            return
        resolved = resolve_obs_connection(self.host, self.port, self.password)
        if not resolved.get("api"):
            raise RuntimeError(resolved.get("message", "OBS 연결 실패"))
        self.api = resolved["api"]
        self.port = int(resolved["port"])
        self.password = resolved.get("password") or self.password

    def _client_v5(self):
        from obsws_python import ReqClient

        return ReqClient(
            host=self.host,
            port=self.port,
            password=self.password,
            timeout=15,
        )

    def _client_v4(self):
        from obswebsocket import obsws

        cl = obsws(self.host, self.port, self.password, legacy=True)
        cl.connect()
        return cl

    def resolve_source_name(self, source_name: str | None = None) -> str:
        if source_name and source_name.strip():
            return source_name.strip()
        self._ensure_api()
        if self.api == "v5":
            cl = self._client_v5()
            try:
                resp = cl.get_current_program_scene()
                name = getattr(resp, "current_program_scene_name", None) or getattr(
                    resp, "scene_name", None
                )
                if not name:
                    raise RuntimeError("현재 Program Scene 이름을 가져오지 못했습니다.")
                return str(name)
            finally:
                cl.disconnect()
        cl = self._client_v4()
        try:
            from obswebsocket import requests as obs_requests

            resp = cl.call(obs_requests.GetCurrentScene())
            name = resp.datain.get("name") if hasattr(resp, "datain") else None
            if not name:
                raise RuntimeError("현재 Scene 이름을 가져오지 못했습니다.")
            return str(name)
        finally:
            cl.disconnect()

    def capture_png(
        self,
        save_path: str,
        source_name: str | None = None,
        width: int = 1920,
        height: int = 1080,
    ) -> str:
        self._ensure_api()
        src = source_name.strip() if source_name and source_name.strip() else None
        if not src:
            src = self.resolve_source_name()

        if self.api == "v5":
            cl = self._client_v5()
            try:
                resp = cl.get_source_screenshot(src, "png", width, height)
                raw = getattr(resp, "image_data", None) or getattr(resp, "imageData", None)
                if not raw:
                    raise RuntimeError("OBS screenshot 응답에 image_data가 없습니다.")
                data = base64.b64decode(raw)
            finally:
                cl.disconnect()
        else:
            from obswebsocket import requests as obs_requests

            cl = self._client_v4()
            try:
                resp = cl.call(
                    obs_requests.TakeSourceScreenshot(
                        sourceName=src,
                        embedPictureFormat="png",
                        imageWidth=width,
                        imageHeight=height,
                    )
                )
                img = resp.datain.get("img") if hasattr(resp, "datain") else None
                if not img:
                    raise RuntimeError("OBS v4 screenshot 응답에 img가 없습니다.")
                if "," in str(img):
                    b64 = str(img).split(",", 1)[1]
                else:
                    b64 = str(img)
                data = base64.b64decode(b64)
            finally:
                cl.disconnect()

        folder = os.path.dirname(os.path.abspath(save_path))
        os.makedirs(folder, exist_ok=True)
        with open(save_path, "wb") as f:
            f.write(data)
        return save_path


def crop_top_right(image, width_ratio: float = 0.55, height_ratio: float = 0.22):
    from PIL import Image

    w, h = image.size
    left = int(w * (1.0 - width_ratio))
    bottom = int(h * height_ratio)
    return image.crop((left, 0, w, bottom))


def crop_badge_tight(image):
    """우측 상단 '광고방송' 뱃지 — 좁은 코너 (반투명 흰 글씨·어두운 배경)."""
    from PIL import Image

    w, h = image.size
    left = int(w * 0.78)
    bottom = max(40, int(h * 0.10))
    return image.crop((left, 0, w, bottom))


def crop_badge_mid(image):
    """우측 상단 뱃지 — 중간 폭 (STB·해상도 차이 대비)."""
    from PIL import Image

    w, h = image.size
    left = int(w * 0.70)
    bottom = max(48, int(h * 0.15))
    return image.crop((left, 0, w, bottom))


def crop_top_bar(image, height_ratio: float = 0.18):
    """상단 전체 — 어두운 광고 화면에서 밝은 뱃지·워터마크가 좌측에 있을 때."""
    from PIL import Image

    w, h = image.size
    bottom = max(32, int(h * height_ratio))
    return image.crop((0, 0, w, bottom))


def badge_region_visibility(image) -> dict[str, Any]:
    """우측 상단 — '광고방송'(어두운 배경+밝은 글씨) 시인성.

    거의 흰 letterbox/오탐 프레임은 visible=False.
    """
    out: dict[str, Any] = {
        "visible": False,
        "score": 0.0,
        "mean": 0.0,
        "dark_ratio": 0.0,
        "bright_ratio": 0.0,
        "note": "",
    }
    try:
        crop = crop_badge_mid(image)
        pixels = list(crop.convert("L").getdata())
        if not pixels:
            out["note"] = "빈 crop"
            return out
        n = len(pixels)
        mean = sum(pixels) / n
        dark_ratio = sum(1 for p in pixels if p < 90) / n
        bright_ratio = sum(1 for p in pixels if p >= 170) / n
        ordered = sorted(pixels)
        lo = ordered[max(0, n // 20)]
        hi = ordered[min(n - 1, n * 19 // 20)]
        contrast = float(hi - lo)
        out["mean"] = round(mean, 1)
        out["dark_ratio"] = round(dark_ratio, 3)
        out["bright_ratio"] = round(bright_ratio, 3)
        if mean >= 200 and dark_ratio < 0.08:
            out["note"] = f"흰/밝은 프레임 (mean={mean:.0f})"
            return out
        # 대부분 흰 픽셀이면 글씨 구분 불가
        if bright_ratio >= 0.75 and mean >= 200:
            out["note"] = f"과다 밝음 bright={bright_ratio:.2f} mean={mean:.0f}"
            return out
        score = (dark_ratio * 0.55) + (min(bright_ratio, 0.35) * 0.9) + (
            min(contrast, 180) / 180.0 * 0.35
        )
        if mean > 160:
            score *= max(0.0, 1.0 - (mean - 160) / 100.0)
        out["score"] = round(float(score), 3)
        out["visible"] = bool(
            score >= 0.32
            and dark_ratio >= 0.15
            and contrast >= 50
            and mean < 195
            and bright_ratio < 0.70
        )
        out["note"] = (
            f"시인성 {'OK' if out['visible'] else '부족'} "
            f"score={out['score']} mean={mean:.0f}"
        )
        return out
    except Exception as e:
        out["note"] = f"visibility 실패: {e}"
        return out


def make_ad_broadcast_chat_preview(image, *, scale: int = 3):
    """Chat 첨부용: 우측 상단 crop 확대 + 대비 강조 (원본 톤 유지)."""
    from PIL import ImageEnhance, ImageOps

    crop = crop_badge_mid(image)
    ac = ImageOps.autocontrast(crop.convert("RGB"), cutoff=1)
    ac = ImageEnhance.Contrast(ac).enhance(1.35)
    ac = ImageEnhance.Sharpness(ac).enhance(1.2)
    return _resize_up(ac, max(2, int(scale)))


def save_ad_broadcast_chat_preview(image, capture_path: str) -> dict[str, Any]:
    """*_chat.png 저장 + 시인성 메타. path는 성공 시에만 설정."""
    meta = badge_region_visibility(image)
    meta["path"] = None
    if not capture_path:
        return meta
    try:
        root, ext = os.path.splitext(capture_path)
        out_path = f"{root}_chat{ext or '.png'}"
        preview = make_ad_broadcast_chat_preview(image)
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        preview.save(out_path)
        meta["path"] = out_path
    except Exception as e:
        meta["note"] = f"{meta.get('note') or ''}; save 실패: {e}".strip("; ")
    return meta


def _resize_up(image, factor: int):
    from PIL import Image

    w, h = image.size
    if factor <= 1:
        return image
    return image.resize(
        (max(1, w * factor), max(1, h * factor)),
        Image.Resampling.LANCZOS,
    )


def _bright_text_mask(gray, threshold: int = 150):
    return gray.point(lambda p, t=threshold: 255 if p >= t else 0, mode="L")


def _white_text_mask(image, threshold: int = 175):
    """반투명 흰 뱃지 — RGB 밝은 픽셀만 추출."""
    from PIL import Image

    rgb = image.convert("RGB")
    pixels = rgb.load()
    out = Image.new("L", rgb.size, 0)
    op = out.load()
    for y in range(rgb.size[1]):
        for x in range(rgb.size[0]):
            pr, pg, pb = pixels[x, y]
            if pr >= threshold and pg >= threshold and pb >= threshold:
                op[x, y] = 255
    return out


def _sky_subtract_l(image):
    """밝은 하늘(푸른) vs 흰 '광고방송' — min(RGB)에서 파란 편향을 빼 대비 확보."""
    from PIL import Image

    rgb = image.convert("RGB")
    pixels = rgb.load()
    out = Image.new("L", rgb.size, 0)
    op = out.load()
    for y in range(rgb.size[1]):
        for x in range(rgb.size[0]):
            pr, pg, pb = pixels[x, y]
            ach = min(pr, pg, pb)
            blue_bias = max(0, pb - pr)
            op[x, y] = max(0, min(255, ach - blue_bias))
    return out


def _ad_broadcast_tpl_paths() -> list[str]:
    data_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "data")
    )
    names = (
        "ad_broadcast_badge_tpl_gray.png",
        "ad_broadcast_badge_tpl2_gray.png",
    )
    return [
        os.path.join(data_dir, n)
        for n in names
        if os.path.isfile(os.path.join(data_dir, n))
    ]


def _badge_match_prep(crop, mode: str = "ss"):
    from PIL import ImageOps

    if mode == "gray":
        return ImageOps.autocontrast(ImageOps.grayscale(crop), cutoff=2)
    return ImageOps.autocontrast(_sky_subtract_l(crop), cutoff=2)


def match_ad_broadcast_badge(image) -> dict[str, Any]:
    """
    우측 상단 '광고방송' 템플릿 매칭 (밝은 하늘·흰 글씨에서 Tesseract 대비).
    Returns ok, score, matched_region, matched_variant, note.
    """
    out: dict[str, Any] = {
        "ok": False,
        "score": 0.0,
        "matched_region": None,
        "matched_variant": None,
        "note": "",
    }
    try:
        import cv2
        import numpy as np
        from PIL import ImageOps  # noqa: F401
    except ImportError:
        out["note"] = "opencv 미설치 — 템플릿 매칭 스킵"
        return out

    tpl_paths = _ad_broadcast_tpl_paths()
    if not tpl_paths:
        out["note"] = "광고방송 템플릿 파일 없음"
        return out

    try:
        # 넓은 top_right+낮은 threshold 는 키즈 채널 로고/생일 배너 오탐이 난다.
        threshold = float(os.environ.get("AD_BROADCAST_MATCH_THRESHOLD", "0.70"))
    except ValueError:
        threshold = 0.70

    templates: list[tuple[str, Any]] = []
    for path in tpl_paths:
        try:
            from PIL import Image

            arr = np.array(Image.open(path).convert("L"), dtype=np.float32)
            if arr.size > 0:
                templates.append((os.path.basename(path), arr))
        except Exception:
            continue
    if not templates:
        out["note"] = "광고방송 템플릿 로드 실패"
        return out

    # top_right 제외: KBS Kids 생일 파티 등 우측 상단 장식과 오탐.
    regions = (
        ("badge_tight", crop_badge_tight(image)),
        ("badge_mid", crop_badge_mid(image)),
    )
    scales = [round(0.80 + i * 0.05, 2) for i in range(10)]  # 0.80..1.25
    best_score = -1.0
    best_meta: tuple[str, str] | None = None

    for region_name, crop in regions:
        for mode in ("ss", "gray"):
            try:
                hay = np.array(_badge_match_prep(crop, mode), dtype=np.float32)
            except Exception:
                continue
            hh, hw = hay.shape[:2]
            for tpl_name, tpl in templates:
                th, tw = tpl.shape[:2]
                for scale in scales:
                    sw, sh = int(tw * scale), int(th * scale)
                    if sw < 16 or sh < 10 or sh >= hh or sw >= hw:
                        continue
                    interp = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
                    scaled = cv2.resize(tpl, (sw, sh), interpolation=interp)
                    try:
                        corr = float(
                            cv2.minMaxLoc(
                                cv2.matchTemplate(
                                    hay, scaled, cv2.TM_CCOEFF_NORMED
                                )
                            )[1]
                        )
                    except Exception:
                        continue
                    if corr > best_score:
                        best_score = corr
                        best_meta = (region_name, f"tpl:{tpl_name}/{mode}@{scale}")

    out["score"] = max(0.0, best_score)
    if best_meta and best_score >= threshold:
        # 흰 letterbox 오탐 차단 — 템플릿 score만으로 PASS 금지
        vis = badge_region_visibility(image)
        out["visibility"] = vis
        if not vis.get("visible"):
            out["ok"] = False
            out["matched_region"] = best_meta[0]
            out["matched_variant"] = best_meta[1]
            out["note"] = (
                f"템플릿 score={best_score:.3f} but 시인성 부족 "
                f"({vis.get('note') or '흰 배경'})"
            )
        else:
            out["ok"] = True
            out["matched_region"] = best_meta[0]
            out["matched_variant"] = best_meta[1]
            out["note"] = (
                f"템플릿 매칭 score={best_score:.3f}, {vis.get('note')}"
            )
    elif best_score >= 0:
        out["note"] = f"템플릿 미달 score={best_score:.3f} (need>={threshold})"
    return out


def _ocr_preprocess_variants(crop, *, badge: bool = False):
    """밝은 배경·어두운 배경 광고 모두 — 대비·반전·이진화·확대 후 OCR."""
    from PIL import ImageOps

    variants: list[tuple[str, Any]] = []
    try:
        gray = ImageOps.grayscale(crop)
        ac = ImageOps.autocontrast(gray, cutoff=1)
        if not badge:
            variants.extend(
                [
                    ("raw", crop),
                    ("gray", gray),
                    ("autocontrast", ac),
                    ("invert", ImageOps.invert(ac)),
                ]
            )
            thresh = 155
            bw = ac.point(lambda p, t=thresh: 255 if p > t else 0, mode="L")
            variants.append(("thresh", bw))
            variants.append(("thresh_inv", ImageOps.invert(bw)))
            big = _resize_up(ac, 2)
            variants.append(("invert_2x", ImageOps.invert(big)))
            return variants

        sky = ImageOps.autocontrast(_sky_subtract_l(crop), cutoff=2)
        for scale in (3, 4):
            sky_big = _resize_up(sky, scale)
            variants.append((f"sky_{scale}x", sky_big))
            variants.append((f"sky_inv_{scale}x", ImageOps.invert(sky_big)))
            big = _resize_up(ac, scale)
            variants.append((f"invert_{scale}x", ImageOps.invert(big)))
            variants.append(
                (f"acinv_{scale}x", ImageOps.invert(ImageOps.autocontrast(big, cutoff=2)))
            )
            variants.append((f"bright_{scale}x", _bright_text_mask(big, 140)))
            for white_th in (200, 220, 240):
                white = _resize_up(_white_text_mask(crop, white_th), scale)
                variants.append((f"white{white_th}_{scale}x", white))
    except Exception:
        pass
    return variants


def _configure_tesseract():
    """Windows: Program Files Tesseract + 프로젝트 tessdata(kor)."""
    import pytesseract

    exe = os.environ.get("TESSERACT_CMD", r"C:\Program Files\Tesseract-OCR\tesseract.exe")
    if os.path.isfile(exe):
        pytesseract.pytesseract.tesseract_cmd = exe
    if not os.environ.get("TESSDATA_PREFIX"):
        local = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "data", "tessdata")
        )
        if os.path.isdir(local) and os.path.isfile(os.path.join(local, "kor.traineddata")):
            os.environ["TESSDATA_PREFIX"] = local


def _tesseract_on_image(
    img,
    *,
    psm_modes: tuple[int, ...] = (7, 6, 13),
    phrase: str = "",
    langs: tuple[str, ...] = ("kor", "kor+eng", "eng"),
) -> str:
    import pytesseract

    _configure_tesseract()
    fallback = ""
    for psm in psm_modes:
        cfg = f"--psm {psm} -c preserve_interword_spaces=1"
        for lang in langs:
            try:
                text = pytesseract.image_to_string(img, lang=lang, config=cfg).strip()
            except Exception:
                continue
            if not text:
                continue
            if phrase and text_contains_phrase(text, phrase):
                return text
            if not fallback and lang == "kor":
                fallback = text
            elif not fallback:
                fallback = text
    return fallback


def ocr_image_region(image) -> tuple[str | None, str]:
    """Returns (ocr_text, note). ocr_text None if OCR unavailable."""
    try:
        from PIL import Image  # noqa: F401
        import pytesseract  # noqa: F401
    except ImportError:
        return None, "OCR 미사용 (pip install pillow pytesseract)"

    result = ocr_phrase_on_image(image, "")
    if not result.get("ocr_available"):
        return None, result.get("note") or "OCR 실패"
    return result.get("ocr_text") or "", ""


_AD_BROADCAST_PHRASE = "광고방송"


def ocr_phrase_on_image(image, phrase: str) -> dict[str, Any]:
    """
    우측 상단 + 상단 바 × 여러 전처리로 phrase 검색.
    '광고방송'은 밝은 하늘·흰 글씨에서 Tesseract가 자주 실패 → 템플릿 매칭 우선.
    """
    out: dict[str, Any] = {
        "ok": False,
        "ocr_available": False,
        "ocr_text": "",
        "matched_region": None,
        "matched_variant": None,
        "note": "",
    }
    want_phrase = bool((phrase or "").strip())
    norm_phrase = re.sub(r"\s+", "", phrase or "")

    # 광고방송: OpenCV 템플릿 먼저 (1초 내, 밝은 하늘에서도 안정)
    if want_phrase and norm_phrase == _AD_BROADCAST_PHRASE:
        matched = match_ad_broadcast_badge(image)
        out["note"] = matched.get("note") or ""
        if matched.get("ok"):
            out["ok"] = True
            out["ocr_available"] = True
            out["ocr_text"] = (
                f"{_AD_BROADCAST_PHRASE} ({matched.get('note') or 'tpl'})"
            )
            out["matched_region"] = matched.get("matched_region")
            out["matched_variant"] = matched.get("matched_variant")
            return out

    try:
        from PIL import Image  # noqa: F401
        import pytesseract  # noqa: F401
    except ImportError:
        if not out["note"]:
            out["note"] = "OCR 미사용 (pip install pillow pytesseract)"
        return out

    # 광고방송 OCR 폴백: 뱃지 crop 만·짧은 시도 (전체 조합은 분 단위)
    if want_phrase and norm_phrase == _AD_BROADCAST_PHRASE:
        regions = [
            ("badge_tight", crop_badge_tight(image), True),
            ("badge_mid", crop_badge_mid(image), True),
        ]
    else:
        regions = [
            ("badge_tight", crop_badge_tight(image), True),
            ("badge_mid", crop_badge_mid(image), True),
            ("top_right", crop_top_right(image), False),
            ("top_bar", crop_top_bar(image), False),
        ]
    chunks: list[str] = []

    for region_name, crop, is_badge in regions:
        psm_modes = (7, 6) if is_badge else (7, 6, 13)
        ocr_langs = ("kor",) if is_badge else ("kor", "kor+eng", "eng")
        for variant_name, variant_img in _ocr_preprocess_variants(crop, badge=is_badge):
            try:
                text = _tesseract_on_image(
                    variant_img,
                    psm_modes=psm_modes,
                    phrase=phrase if want_phrase else "",
                    langs=ocr_langs,
                ).strip()
            except Exception as e:
                out["note"] = f"OCR 실패: {e}"
                continue
            if not text:
                continue
            out["ocr_available"] = True
            chunks.append(text)
            if want_phrase and text_contains_phrase(text, phrase):
                out["ok"] = True
                out["matched_region"] = region_name
                out["matched_variant"] = variant_name
                out["ocr_text"] = text
                return out

    out["ocr_text"] = " | ".join(dict.fromkeys(chunks))[:500]
    if want_phrase and text_contains_phrase(out["ocr_text"], phrase):
        out["ok"] = True
    elif out["ocr_available"] and not want_phrase:
        out["ok"] = True
    return out


# Tesseract 흔한 오인식: 방→밥/밤/반, 송→슨/숭/출, 광→망/과, 밝은 배경→방송 일부만 인식
_AD_BROADCAST_OCR_RE = re.compile(
    r"[광망과].{0,3}고.{0,3}[방반밤밥비셔해].{0,3}[송슨숭출별8]?"
)
_AD_BROADCAST_TAIL_RE = re.compile(r"[방반셔해과비][송출별8]?")


def text_contains_phrase(haystack: str, phrase: str) -> bool:
    if not haystack or not phrase:
        return False
    norm_h = re.sub(r"\s+", "", haystack)
    norm_p = re.sub(r"\s+", "", phrase)
    if norm_p in norm_h:
        return True
    if norm_p == _AD_BROADCAST_PHRASE:
        if _AD_BROADCAST_OCR_RE.search(norm_h):
            return True
        if "광고방" in norm_h:
            return True
        if len(norm_h) <= 12 and re.search(r".{0,4}방송", norm_h):
            return True
        if "광고" in norm_h:
            tail = norm_h.split("광고", 1)[-1]
            core = re.sub(r"[\s_\W\d@!※]+", "", norm_h)
            # 우측 뱃지: 반투명 흰 글씨·밝은 배경에서 '광고'만 잡히는 경우
            if core == "광고" or (core.startswith("광고") and len(core) <= 5):
                return True
            if _AD_BROADCAST_TAIL_RE.search(tail):
                return True
    return False


def check_phrase_on_screen(
    obs: OBSScreenCapture,
    save_path: str,
    phrase: str,
    source_name: str | None = None,
) -> dict[str, Any]:
    """
    OBS 캡처 → 우측 상단 crop → OCR로 phrase 포함 여부.
    Returns dict: ok, path, ocr_text, message, ocr_available
    """
    result: dict[str, Any] = {
        "ok": False,
        "path": None,
        "ocr_text": None,
        "message": "",
        "ocr_available": False,
    }
    try:
        path = obs.capture_png(save_path, source_name=source_name)
        result["path"] = path
    except ImportError:
        result["message"] = "obsws-python / obswebsocket 미설치"
        return result
    except Exception as e:
        obs._ensure_api()
        result["message"] = format_obs_connection_error(
            e, obs.host, obs.port, obs.api
        )
        return result

    try:
        from PIL import Image

        img = Image.open(path)
    except ImportError:
        result["message"] = "Pillow 미설치 — 캡처만 저장됨"
        return result
    except Exception as e:
        result["message"] = f"이미지 열기 실패: {e}"
        return result

    ocr_result = ocr_phrase_on_image(img, phrase)
    chat_meta = save_ad_broadcast_chat_preview(img, path)
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
    if result["ok"]:
        extra = ""
        if result.get("ocr_variant"):
            extra = f" [{result['ocr_region']}/{result['ocr_variant']}]"
        result["message"] = f"'{phrase}' 문구 확인{extra}"
    else:
        note = result.get("visibility_note") or ocr_result.get("note") or ""
        result["message"] = (
            f"'{phrase}' 미검출 (상단·우측 OCR)"
            + (f" — {note}" if note else "")
        )
    return result


def default_capture_path(base_dir: str, tag: str = "ad_broadcast") -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return os.path.join(base_dir, "obs_captures", f"{tag}_{ts}.png")
