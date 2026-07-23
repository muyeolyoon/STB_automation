"""
mitmproxy 스크립트: 지정한 URL 접두사를 다른 접두사로 치환해 업스트림 요청을 보냅니다.

사용 예 (PC에서, 셋탑이 같은 LAN에 있어야 함):

  pip install mitmproxy

  set URL_REMAP_FROM=https://api.prod.example.com
  set URL_REMAP_TO=https://192.168.10.50:8443
  mitmdump -s stb-rpa/tools/mitm_url_remap.py --listen-port 8080

셋탑에 프록시 설정 (ADB, USB 또는 이미 adb connect 된 상태):

  adb -s 192.168.10.8:5555 shell settings put global http_proxy <PC_LAN_IP>:8080

종료 후 프록시 해제:

  adb shell settings put global http_proxy :0

HTTPS는 셋탑에 mitmproxy CA 인증서 설치가 필요합니다. 앱이 SSL pinning을 쓰면 이 방식은 동작하지 않습니다.
"""
from __future__ import annotations

import os
from urllib.parse import urlparse

# 환경변수로 덮어쓰기 (mitmproxy -s 로드 시 프로세스 env 기준)
FROM_PREFIX = os.environ.get("URL_REMAP_FROM", "").rstrip("/")
TO_PREFIX = os.environ.get("URL_REMAP_TO", "").rstrip("/")


def _apply_parsed(flow, p) -> None:
    req = flow.request
    req.scheme = p.scheme or "https"
    hostname = p.hostname
    if not hostname:
        return
    req.host = hostname
    port = p.port
    if port is None:
        port = 443 if req.scheme == "https" else 80
    req.port = port
    path = p.path or "/"
    if p.query:
        req.path = f"{path}?{p.query}"
    else:
        req.path = path


def request(flow) -> None:
    if not FROM_PREFIX or not TO_PREFIX:
        return
    url = flow.request.pretty_url
    if not url.startswith(FROM_PREFIX):
        return
    rest = url[len(FROM_PREFIX) :]
    if rest and not rest.startswith(("/", "?", "#")):
        # 접두사가 경계 없이 잘린 경우(설정 실수)에는 건드리지 않음
        return
    new_url = TO_PREFIX + rest
    p = urlparse(new_url)
    _apply_parsed(flow, p)
