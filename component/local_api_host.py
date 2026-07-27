"""PC LAN IP 자동 감지 — local mock API host (STB_LOCAL_API_HOST)."""

from __future__ import annotations

import os
import re
import socket
import subprocess
from typing import Iterable

# ipconfig (EN/KO): "IPv4 Address" / "IPv4 주소"
_IPCONFIG_IPV4_RE = re.compile(
    r"IPv4(?:\s+Address|\s*주소)[^:]*:\s*(\d{1,3}(?:\.\d{1,3}){3})",
    re.I,
)
_FALLBACK_HOST = "192.168.10.150"


def _is_usable_lan_ip(ip: str) -> bool:
    if not ip or ip.startswith("127."):
        return False
    parts = ip.split(".")
    if len(parts) != 4:
        return False
    try:
        nums = [int(p) for p in parts]
    except ValueError:
        return False
    if any(n < 0 or n > 255 for n in nums):
        return False
    a, b = nums[0], nums[1]
    # RFC1918 + link-local 제외(APIPA)
    if a == 10:
        return True
    if a == 192 and b == 168:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    return False


def _subnet24(ip: str) -> str | None:
    parts = ip.split(".")
    if len(parts) != 4:
        return None
    return ".".join(parts[:3])


def _ips_from_ipconfig() -> list[str]:
    try:
        # Windows: chcp 무관하게 텍스트에서 IPv4 줄만 추출
        r = subprocess.run(
            ["ipconfig"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            shell=False,
        )
        text = (r.stdout or "") + "\n" + (r.stderr or "")
    except Exception:
        return []
    if not text.strip():
        # 코드페이지 이슈 대비: 시스템 기본 인코딩
        try:
            r = subprocess.run(
                ["ipconfig"],
                capture_output=True,
                timeout=15,
                shell=False,
            )
            text = (r.stdout or b"").decode("cp949", errors="replace")
        except Exception:
            return []
    found: list[str] = []
    for m in _IPCONFIG_IPV4_RE.finditer(text):
        ip = m.group(1)
        if _is_usable_lan_ip(ip) and ip not in found:
            found.append(ip)
    return found


def _ip_via_udp_route() -> str | None:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip if _is_usable_lan_ip(ip) else None
    except OSError:
        return None


def _pick_preferred(
    candidates: Iterable[str],
    *,
    prefer_peer: str | None = None,
) -> str | None:
    ips = [ip for ip in candidates if _is_usable_lan_ip(ip)]
    if not ips:
        return None
    peer = (prefer_peer or "").strip()
    # adb serial 이 IP:port 이면 포트 제거
    if peer and ":" in peer and not peer.count(":") > 1:
        # 192.168.10.10:5555
        host_part = peer.rsplit(":", 1)[0]
        if _is_usable_lan_ip(host_part):
            peer = host_part
    if peer and _is_usable_lan_ip(peer):
        want = _subnet24(peer)
        same = [ip for ip in ips if _subnet24(ip) == want]
        if same:
            return same[0]
    # 랩 관례 192.168.10.x 우선
    lab = [ip for ip in ips if ip.startswith("192.168.10.")]
    if lab:
        return lab[0]
    return ips[0]


def detect_local_api_host(
    *,
    prefer_peer: str | None = None,
    fallback: str = _FALLBACK_HOST,
) -> str:
    """
    PC mock API 호스트 IP.
    우선순위: STB_LOCAL_API_HOST → ipconfig LAN → UDP 라우트 → fallback.
    prefer_peer: STB IP(같은 /24 선호).
    """
    env = os.environ.get("STB_LOCAL_API_HOST", "").strip()
    if env:
        return env.split("://")[-1].split("/")[0].split(":")[0]

    picked = _pick_preferred(_ips_from_ipconfig(), prefer_peer=prefer_peer)
    if picked:
        return picked

    via_udp = _ip_via_udp_route()
    if via_udp:
        return via_udp

    return (fallback or _FALLBACK_HOST).strip()
