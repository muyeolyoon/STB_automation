"""재부팅/logcat에서 deviceId·deviceTypeId·uuid를 읽어 Apache mock auth를 패치.

흐름 (열무.xlsx 명령어 탭 참고):
  1) AddrAD 로그로 현재 API가 U+ 운영(art-*.anypoint.tv)인지 확인
  2) 아니면 CHANGE_API_ENDPOINT → https://art-api.anypoint.tv 후 재부팅
  3) 운영 auth 로그에서 identity 추출 → htdocs/{model}/v3/device/auth 패치

사용 예:
  python stb-rpa/component/auth_mock_patch.py
  python stb-rpa/component/auth_mock_patch.py --device 192.168.10.3
  python stb-rpa/component/auth_mock_patch.py --no-reboot --timeout 90
  python stb-rpa/component/auth_mock_patch.py --skip-ensure-prod   # 운영 전환 생략

환경변수:
  STB_DEVICE_IP / STB_DEVICE_IPS — 대상 기기
  STB_LOCAL_API_HOST — auth endpoints.* 에 넣을 PC IP
                       (미설정 시 ipconfig 등으로 자동 감지)
  STB_HTDOCS_ROOT — 기본 C:\\Apache24\\htdocs
  AUTH_PATCH_REBOOT=1 — 시작 시 adb reboot
  STB_PROD_API_ENDPOINT — 기본 https://art-api.anypoint.tv (U+ 운영)
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

# deviceTypeId → htdocs 모델 폴더 (lab 기준)
DEVICE_TYPE_TO_MODEL = {
    20: "UHD4K",
    23: "UHD2",
    24: "UHD3",
    25: "SBB",
    26: "SBB2",
    28: "UHD4T",
    54: "UHD5K",
    55: "UHD5M",
}

# 열무.xlsx > 명령어 탭
CHANGE_TEST_PROPERTY_ACTION = "tv.anypoint.agent.app.CHANGE_TEST_PROPERTY"
# U+ : 스테이지 없음 → 운영 art-api.anypoint.tv
DEFAULT_UPLUS_PROD_ENDPOINT = "https://art-api.anypoint.tv"
# SKB 참고 (이 스크립트 기본 경로는 U+)
SKB_STAGE_ENDPOINT = "https://apm-api-stage.anypoint.tv"
SKB_PROD_ENDPOINT = "https://skb-api.anypoint.tv"

FIELD_RES = {
    "deviceId": re.compile(r'"?deviceId"?\s*[:=]\s*"?(-?\d+)"?', re.I),
    "deviceTypeId": re.compile(r'"?deviceTypeId"?\s*[:=]\s*"?(-?\d+)"?', re.I),
    "uuid": re.compile(
        r'(?:previous uuid|["\']?uuid["\']?)\s*[:=]\s*"?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"?',
        re.I,
    ),
    "accessToken": re.compile(
        r'"?accessToken"?\s*[:=]\s*"?([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"?',
        re.I,
    ),
}

# AddrAD 인증 라인 우선 (블루투스 GATT uuid 등 오탐 방지)
ADDRAD_HINT = re.compile(r"AddrAD|AnypointAD|DeviceManager\.|AnypointAppManager", re.I)
PLACEHOLDER_UUID = re.compile(r"^(81818181-|00000000-|ffffffff-)", re.I)

# logcat HTTP 호스트로 환경 판별
ENV_URL_RE = re.compile(
    r"https?://([^\s/\"'<>]+)",
    re.I,
)
LOCAL_HOST_RE = re.compile(
    r"^(?:192\.168\.|10\.|172\.(?:1[6-9]|2\d|3[01])\.|127\.|localhost)",
    re.I,
)


def _is_trusted_line(line: str) -> bool:
    return bool(ADDRAD_HINT.search(line))


def _accept_value(key: str, val: str | int) -> bool:
    if key == "uuid" and isinstance(val, str) and PLACEHOLDER_UUID.match(val):
        return False
    return True


def _try_absorb(found: dict, line: str, *, require_addrad: bool) -> None:
    if require_addrad and not _is_trusted_line(line):
        return
    for key, cre in FIELD_RES.items():
        if key in found:
            continue
        m = cre.search(line)
        if not m:
            continue
        raw = m.group(1)
        val: str | int = int(raw) if key in ("deviceId", "deviceTypeId") else raw
        if not _accept_value(key, val):
            continue
        found[key] = val
        print(f"  감지 {key}={found[key]}")


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "y", "on")


def list_adb_devices() -> list[str]:
    out = subprocess.check_output(["adb", "devices"], text=True, errors="ignore")
    devices = []
    for line in out.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devices.append(parts[0])
    return devices


def wait_for_device(serial: str | None, timeout_sec: float = 120) -> str:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        devices = list_adb_devices()
        if serial:
            if serial in devices:
                return serial
        elif len(devices) == 1:
            return devices[0]
        elif len(devices) > 1:
            raise SystemExit(
                f"ADB 기기가 {len(devices)}대입니다. --device 로 지정하세요: {devices}"
            )
        time.sleep(2)
    raise SystemExit("ADB 기기 대기 타임아웃 — 연결 후 다시 실행하세요.")


def adb(serial: str, *args: str, timeout: float | None = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["adb", "-s", serial, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=timeout,
    )


def reboot_and_wait(serial: str, boot_timeout: float = 180) -> None:
    print(f"[{serial}] adb reboot …")
    adb(serial, "reboot", timeout=60)
    time.sleep(5)
    deadline = time.time() + boot_timeout
    while time.time() < deadline:
        devices = list_adb_devices()
        if serial in devices:
            # boot completed?
            p = adb(serial, "shell", "getprop", "sys.boot_completed", timeout=10)
            if p.stdout.strip() == "1":
                print(f"[{serial}] boot completed")
                time.sleep(8)  # SDK auth 로그 여유
                return
        time.sleep(3)
    raise SystemExit(f"[{serial}] 재부팅 후 대기 타임아웃")


def _classify_host(host: str) -> str | None:
    host = (host or "").strip().lower()
    if not host:
        return None
    if LOCAL_HOST_RE.match(host):
        return "local"
    if "apm-api-stage" in host:
        return "stage"
    if host.startswith("skb-api.") or "skb-api.anypoint" in host:
        return "skb_prod"
    # U+ 운영: art-api / art-device-state / art-creative 등
    if host.startswith("art-") and host.endswith(".anypoint.tv"):
        return "uplus_prod"
    if host.endswith("anypoint.tv") and "stage" not in host:
        return "cloud_other"
    return None


def detect_api_env(serial: str) -> dict:
    """AddrAD HTTP URL로 현재 API 환경 추정.

    returns: {env, evidence, hosts}
      env: uplus_prod | local | stage | skb_prod | unknown
    """
    hosts: list[str] = []
    evidence: list[str] = []
    try:
        p = adb(serial, "logcat", "-d", "-t", "5000", timeout=40)
    except subprocess.TimeoutExpired:
        return {"env": "unknown", "evidence": [], "hosts": []}
    for line in (p.stdout or "").splitlines():
        if not ADDRAD_HINT.search(line):
            continue
        for m in ENV_URL_RE.finditer(line):
            host = m.group(1).split(":")[0]
            kind = _classify_host(host)
            if not kind:
                continue
            hosts.append(host)
            if len(evidence) < 5:
                evidence.append(f"{kind}:{host}")
    # 우선순위: local이 보이면 local(의도적 mock), 아니면 prod 계열
    kinds = {_classify_host(h) for h in hosts}
    kinds.discard(None)
    if "local" in kinds:
        env = "local"
    elif "uplus_prod" in kinds:
        env = "uplus_prod"
    elif "stage" in kinds:
        env = "stage"
    elif "skb_prod" in kinds:
        env = "skb_prod"
    elif "cloud_other" in kinds:
        env = "cloud_other"
    else:
        env = "unknown"
    return {"env": env, "evidence": evidence, "hosts": sorted(set(hosts))}


def send_change_api_endpoint(serial: str, endpoint: str) -> bool:
    """명령어 탭: CHANGE_API_ENDPOINT (변경 후 재부팅 권장)."""
    endpoint = endpoint.rstrip("/")
    cmd = [
        "shell",
        "am",
        "broadcast",
        "-a",
        CHANGE_TEST_PROPERTY_ACTION,
        "--es",
        "change.command",
        "CHANGE_API_ENDPOINT",
        "--es",
        "api.endpoint",
        endpoint,
    ]
    p = adb(serial, *cmd, timeout=30)
    ok = p.returncode == 0
    print(f"[{serial}] CHANGE_API_ENDPOINT → {endpoint}  ok={ok}")
    if p.stdout:
        print(f"  stdout: {p.stdout.strip()[:200]}")
    if p.stderr:
        print(f"  stderr: {p.stderr.strip()[:200]}")
    return ok


def ensure_uplus_prod(
    serial: str,
    *,
    prod_endpoint: str,
    force: bool = False,
) -> dict:
    """운영이 아니면 art-api로 전환. 전환 시 재부팅 필요 여부 반환."""
    info = detect_api_env(serial)
    print(f"[{serial}] API 환경 감지: {info['env']}  evidence={info['evidence'][:3]}")
    if info["env"] == "uplus_prod" and not force:
        print(f"[{serial}] 이미 U+ 운영(art-*) - endpoint 유지")
        return {"switched": False, "env": info["env"], "detect": info}
    if info["env"] == "unknown" and not force:
        # 증거 없으면 운영으로 맞춤 (auth 추출 신뢰도)
        print(f"[{serial}] 환경 불명 -> 운영으로 전환: {prod_endpoint}")
    elif info["env"] != "uplus_prod":
        print(f"[{serial}] {info['env']} -> 운영 전환: {prod_endpoint}")
    send_change_api_endpoint(serial, prod_endpoint)
    return {"switched": True, "env": info["env"], "detect": info}


def scrape_identity_from_logcat(
    serial: str, timeout_sec: float = 120
) -> dict:
    """logcat에서 deviceId / deviceTypeId / uuid / accessToken 수집."""
    found: dict = {}
    # 버퍼 비우지 않음 — 인증 직후 로그만 놓칠 수 있음. 재부팅 직후면 충분.
    proc = subprocess.Popen(
        ["adb", "-s", serial, "logcat", "-v", "brief"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )
    deadline = time.time() + timeout_sec
    assert proc.stdout is not None
    try:
        while time.time() < deadline:
            if all(k in found for k in ("deviceId", "deviceTypeId", "uuid")):
                break
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    break
                continue
            _try_absorb(found, line, require_addrad=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    return found


def scrape_identity_from_dump(serial: str) -> dict:
    """재부팅 없이 최근 logcat 일부에서 검색 (전체 -d 는 버퍼 과다로 타임아웃 날 수 있음)."""
    found: dict = {}
    # 최근 N줄만
    for args in (
        ["logcat", "-d", "-t", "8000"],
        ["logcat", "-d", "-t", "3000", "-s", "AddrAD:*"],
    ):
        try:
            p = adb(serial, *args, timeout=45)
        except subprocess.TimeoutExpired:
            continue
        for line in (p.stdout or "").splitlines():
            _try_absorb(found, line, require_addrad=True)
        if all(k in found for k in ("deviceId", "deviceTypeId", "uuid")):
            break
    return found


def local_ip_guess(fallback: str) -> str:
    """STB_LOCAL_API_HOST 또는 ipconfig/라우트 자동 감지."""
    try:
        from component.local_api_host import detect_local_api_host

        return detect_local_api_host(fallback=fallback)
    except Exception:
        host = os.environ.get("STB_LOCAL_API_HOST", "").strip()
        if host:
            return host.split("://")[-1].split("/")[0]
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except OSError:
            return fallback


def auth_path(htdocs: Path, model: str) -> Path:
    return htdocs / model / "v3" / "device" / "auth"


def patch_auth_file(
    path: Path,
    identity: dict,
    *,
    model: str,
    local_host: str,
    rewrite_endpoints: bool,
) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"auth 파일 없음: {path}")
    backup = path.with_suffix(path.suffix + f".bak.{int(time.time())}")
    shutil.copy2(path, backup)
    data = json.loads(path.read_text(encoding="utf-8"))
    before = {
        "deviceId": data.get("deviceId"),
        "deviceTypeId": data.get("deviceTypeId"),
        "uuid": data.get("uuid"),
        "accessToken": data.get("accessToken"),
    }
    data["deviceId"] = identity["deviceId"]
    data["deviceTypeId"] = identity["deviceTypeId"]
    data["uuid"] = identity["uuid"]
    if identity.get("accessToken"):
        data["accessToken"] = identity["accessToken"]

    if rewrite_endpoints and isinstance(data.get("endpoints"), dict):
        base = f"http://{local_host}/{model}"
        ep = data["endpoints"]
        for key in (
            "auth",
            "adSyncResult",
            "appLog",
            "event",
            "stateLog",
            "impressionLog",
        ):
            if key in ep:
                ep[key] = base
        if "proxyAdLog" in ep:
            ep["proxyAdLog"] = base.rstrip("/") + "/"
        # requestAds 는 구글 mock 경로를 쓰는 경우가 많아 유지 (없으면 base)
        if not ep.get("requestAds"):
            ep["requestAds"] = base

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"backup": str(backup), "before": before, "after": {
        "deviceId": data["deviceId"],
        "deviceTypeId": data["deviceTypeId"],
        "uuid": data["uuid"],
        "accessToken": data.get("accessToken"),
    }}


def resolve_serial(cli_device: str | None) -> str | None:
    if cli_device:
        return cli_device.strip()
    raw = os.environ.get("STB_DEVICE_IPS") or os.environ.get("STB_DEVICE_IP") or ""
    parts = [p.strip() for p in re.split(r"[,;\s]+", raw) if p.strip()]
    if len(parts) == 1:
        return parts[0]
    if len(parts) > 1:
        raise SystemExit(f"기기 여러 대: {parts} — --device 로 하나 지정")
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="logcat identity → Apache mock auth 패치")
    ap.add_argument("--device", default=None, help="adb serial / IP:port")
    ap.add_argument("--reboot", action="store_true", help="시작 시 adb reboot")
    ap.add_argument("--no-reboot", action="store_true", help="재부팅 없이 덤프/실시간만")
    ap.add_argument("--timeout", type=float, default=120, help="logcat 수집 초")
    ap.add_argument("--model", default=None, help="htdocs 모델 폴더 강제 (기본: deviceTypeId 매핑)")
    ap.add_argument(
        "--htdocs",
        default=os.environ.get("STB_HTDOCS_ROOT", r"C:\Apache24\htdocs"),
    )
    ap.add_argument(
        "--local-host",
        default=None,
        help="endpoints 에 넣을 PC IP (기본: STB_LOCAL_API_HOST 또는 자동감지)",
    )
    ap.add_argument(
        "--keep-endpoints",
        action="store_true",
        help="auth 안 endpoints.* 를 건드리지 않음",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="파일 쓰지 않고 감지 결과만 출력",
    )
    ap.add_argument(
        "--skip-ensure-prod",
        action="store_true",
        help="운영 endpoint 확인/전환 생략",
    )
    ap.add_argument(
        "--force-prod",
        action="store_true",
        help="이미 운영이어도 art-api 로 다시 CHANGE_API_ENDPOINT",
    )
    ap.add_argument(
        "--prod-endpoint",
        default=os.environ.get("STB_PROD_API_ENDPOINT", DEFAULT_UPLUS_PROD_ENDPOINT),
        help=f"U+ 운영 API (기본 {DEFAULT_UPLUS_PROD_ENDPOINT})",
    )
    args = ap.parse_args(argv)

    do_reboot = args.reboot or (_env_truthy("AUTH_PATCH_REBOOT") and not args.no_reboot)
    if not args.no_reboot and not args.reboot and not _env_truthy("AUTH_PATCH_REBOOT"):
        # 기본: 재부팅 해서 인증 로그를 새로 받기
        do_reboot = True

    print("ADB 기기 대기 중… (연결해 주세요)")
    serial = wait_for_device(resolve_serial(args.device), timeout_sec=300)
    print(f"대상 기기: {serial}")

    # 1) 운영 환경 확인 (열무 명령어: 스테이지>운영 → art-api.anypoint.tv)
    switched_to_prod = False
    if not args.skip_ensure_prod:
        ensure = ensure_uplus_prod(
            serial,
            prod_endpoint=args.prod_endpoint,
            force=args.force_prod,
        )
        switched_to_prod = bool(ensure.get("switched"))
        if switched_to_prod:
            do_reboot = True  # 명령어 탭: endpoint 변경 후 재부팅
    else:
        det = detect_api_env(serial)
        print(f"[{serial}] --skip-ensure-prod  env={det['env']}  evidence={det['evidence'][:3]}")

    if do_reboot:
        reboot_and_wait(serial)
        # 재부팅 후 다시 한 번 운영인지 확인
        if not args.skip_ensure_prod:
            after = detect_api_env(serial)
            print(
                f"[{serial}] 재부팅 후 API 환경: {after['env']}  "
                f"evidence={after['evidence'][:3]}"
            )
            if after["env"] != "uplus_prod":
                print(
                    f"[{serial}] 경고: 아직 운영 증거가 약함. "
                    "auth 추출은 진행하되 값 신뢰도 확인 필요"
                )
        print(f"logcat 실시간 수집 ({args.timeout:.0f}s)…")
        identity = scrape_identity_from_logcat(serial, timeout_sec=args.timeout)
    else:
        print("logcat 덤프 검색…")
        identity = scrape_identity_from_dump(serial)
        if not all(k in identity for k in ("deviceId", "deviceTypeId", "uuid")):
            print(f"덤프 부족 {identity} — 실시간 수집 ({args.timeout:.0f}s)…")
            more = scrape_identity_from_logcat(serial, timeout_sec=args.timeout)
            identity.update({k: v for k, v in more.items() if k not in identity})

    missing = [k for k in ("deviceId", "deviceTypeId", "uuid") if k not in identity]
    if missing:
        print(f"실패: 미감지 필드 {missing}. 현재={identity}")
        print("힌트: 운영(art-api) 확인 후 재부팅, 또는 --timeout 늘리기")
        return 2

    dtype = int(identity["deviceTypeId"])
    model = args.model or DEVICE_TYPE_TO_MODEL.get(dtype)
    if not model:
        print(f"deviceTypeId={dtype} 에 대한 모델 매핑 없음. --model 지정 필요")
        return 3

    local_host = args.local_host or local_ip_guess("192.168.10.150")
    path = auth_path(Path(args.htdocs), model)
    print(f"모델={model}  auth={path}")
    print(f"identity={identity}")
    print(f"local_host={local_host}  rewrite_endpoints={not args.keep_endpoints}")
    if switched_to_prod:
        print("참고: 기기는 지금 U+ 운영 endpoint 상태일 수 있음 (local 복귀는 별도)")

    if args.dry_run:
        print("dry-run — 파일 변경 없음")
        return 0

    result = patch_auth_file(
        path,
        identity,
        model=model,
        local_host=local_host,
        rewrite_endpoints=not args.keep_endpoints,
    )
    print(f"backup: {result['backup']}")
    print(f"before: {result['before']}")
    print(f"after:  {result['after']}")
    print("완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
