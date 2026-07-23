import os
import subprocess
import threading
import time
from datetime import datetime

NOT_READY_TO_PLAY_AD_NEEDLE = "not yet ready to play target ad"
AD_SYNC_BROADCAST_ACTION = "tv.anypoint.sdk.AD_SYNC"
AD_SYNC_PACKAGE = os.environ.get("AD_SYNC_PACKAGE", "tv.anypoint.uplus.tvg.app")
AD_SYNC_RECOVERY_COOLDOWN_SEC = int(os.environ.get("AD_SYNC_RECOVERY_COOLDOWN_SEC", "90"))

_ad_sync_recovery_at = {}
_ad_sync_recovery_lock = threading.Lock()


def _current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def send_ad_sync_broadcast(device: str) -> bool:
    """am broadcast -a tv.anypoint.sdk.AD_SYNC -p tv.anypoint.uplus.tvg.app"""
    cmd = [
        "adb",
        "-s",
        device,
        "shell",
        "am",
        "broadcast",
        "-a",
        AD_SYNC_BROADCAST_ACTION,
        "-p",
        AD_SYNC_PACKAGE,
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:
        print(f"{_current_time_str()} [{device}] [AD_SYNC] 전송 실패: {e}")
        return False
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()[:200]
        print(f"{_current_time_str()} [{device}] [AD_SYNC] 실패 (rc={result.returncode}): {err}")
        return False
    out = (result.stdout or "").strip()
    if out:
        print(f"{_current_time_str()} [{device}] [AD_SYNC] {out[:200]}")
    return True


def on_log_line_ad_not_ready_recovery(device, line):
    """logcat 에 'not yet ready to play target ad' 감지 시 AD_SYNC 브로드캐스트."""
    if NOT_READY_TO_PLAY_AD_NEEDLE not in line.lower():
        return

    with _ad_sync_recovery_lock:
        last = _ad_sync_recovery_at.get(device, 0)
        elapsed = time.time() - last
        if elapsed < AD_SYNC_RECOVERY_COOLDOWN_SEC:
            print(
                f"{_current_time_str()} [{device}] [광고 미준비] AD_SYNC 쿨다운 "
                f"({int(elapsed)}/{AD_SYNC_RECOVERY_COOLDOWN_SEC}초) — 스킵"
            )
            return
        _ad_sync_recovery_at[device] = time.time()

    snippet = line.strip()[:220]
    print(f"{_current_time_str()} [{device}] [광고 미준비] {snippet} → AD_SYNC 전송")

    def _run():
        send_ad_sync_broadcast(device)

    threading.Thread(target=_run, daemon=True).start()
