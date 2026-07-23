import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple


DEFAULT_ADB = "adb"


@dataclass(frozen=True)
class AdbResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int


def _run(args: List[str], timeout_s: float = 10.0) -> AdbResult:
    # Windows: adb 실행 시 콘솔 창이 잠깐 뜨며 깜빡이는 것 방지
    kwargs = {"capture_output": True, "text": True, "timeout": timeout_s}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        cp = subprocess.run(args, **kwargs)
        return AdbResult(
            ok=(cp.returncode == 0),
            stdout=(cp.stdout or "").strip(),
            stderr=(cp.stderr or "").strip(),
            returncode=cp.returncode,
        )
    except FileNotFoundError:
        return AdbResult(
            ok=False,
            stdout="",
            stderr="adb를 찾을 수 없습니다. Android SDK platform-tools 설치 후 PATH에 adb를 추가하세요.",
            returncode=127,
        )
    except subprocess.TimeoutExpired:
        return AdbResult(
            ok=False,
            stdout="",
            stderr=f"명령 타임아웃({timeout_s}s): {' '.join(args)}",
            returncode=124,
        )


def adb_devices(adb_path: str = DEFAULT_ADB) -> Tuple[List[str], AdbResult]:
    """
    `adb devices` 결과에서 state가 'device' 인 디바이스만 반환합니다.
    반환: (device_ids, raw_result)
    """
    res = _run([adb_path, "devices"], timeout_s=10.0)
    if not res.ok:
        return [], res

    device_ids: List[str] = []
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line or line.lower().startswith("list of devices"):
            continue
        # e.g. "192.168.0.10:5555 device"
        parts = re.split(r"\s+", line)
        if len(parts) >= 2 and parts[1] == "device":
            device_ids.append(parts[0])
    return device_ids, res


def adb_connect(device_id_or_ip: str, adb_path: str = DEFAULT_ADB) -> AdbResult:
    """
    IP:PORT 형태면 `adb connect` 수행.
    USB 시리얼(콜론 없음)일 경우 connect가 필요 없어서 ok로 처리합니다.
    """
    device_id_or_ip = (device_id_or_ip or "").strip()
    if not device_id_or_ip:
        return AdbResult(ok=False, stdout="", stderr="device id/ip가 비었습니다.", returncode=2)

    if ":" not in device_id_or_ip:
        return AdbResult(ok=True, stdout="USB/serial 디바이스로 간주(연결 생략).", stderr="", returncode=0)

    res = _run([adb_path, "connect", device_id_or_ip], timeout_s=10.0)
    output = (res.stdout + "\n" + res.stderr).strip().lower()
    ok = res.ok and ("connected to" in output or "already connected" in output or "connected" in output)
    return AdbResult(ok=ok, stdout=res.stdout, stderr=res.stderr, returncode=res.returncode)


def adb_keyevent(device_id: str, keycode: int, adb_path: str = DEFAULT_ADB) -> AdbResult:
    return _run([adb_path, "-s", device_id, "shell", "input", "keyevent", str(int(keycode))], timeout_s=10.0)


def adb_keyevent_many(device_ids: Iterable[str], keycode: int, adb_path: str = DEFAULT_ADB) -> List[Tuple[str, AdbResult]]:
    results: List[Tuple[str, AdbResult]] = []
    for d in device_ids:
        d = (d or "").strip()
        if not d:
            continue
        results.append((d, adb_keyevent(d, keycode, adb_path=adb_path)))
    return results


def adb_shell(device_id: str, command: str, adb_path: str = DEFAULT_ADB, timeout_s: float = 15.0) -> AdbResult:
    """디바이스에서 shell 명령 실행. command는 한 줄로 (쉘 메타문자 주의)."""
    return _run([adb_path, "-s", device_id, "shell", command], timeout_s=timeout_s)


def get_input_device_ids_for_remote(device_id: str, adb_path: str = DEFAULT_ADB) -> List[int]:
    """
    dumpsys input 결과에서 리모컨/IR/키패드로 보이는 입력 디바이스 ID 목록 반환.
    (외부 리모컨 차단 시 block-source 할 대상 후보)
    """
    res = adb_shell(device_id, "dumpsys input", adb_path=adb_path, timeout_s=10.0)
    if not res.ok:
        return []
    ids: List[int] = []
    # 예: "  3: device_name" 또는 "  Device 3: name"
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        # "3: something" 형태
        if ":" in line:
            head, _ = line.split(":", 1)
            head = head.replace("Device", "").strip()
            if head.isdigit():
                ids.append(int(head))
    # 중복 제거, 순서 유지
    seen: set = set()
    unique: List[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            unique.append(i)
    return unique


def set_external_remote_blocked(
    device_id: str,
    block: bool,
    adb_path: str = DEFAULT_ADB,
    custom_block_cmd: Optional[str] = None,
    custom_unblock_cmd: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    외부 리모컨(IR/키패드) 입력 차단 또는 허용.
    block=True: UI 제어 중에는 외부 리모컨으로 조정되지 않게 차단.
    block=False: 외부 리모컨 다시 허용.
    custom_block_cmd / custom_unblock_cmd 가 있으면 해당 shell 명령 사용 (기기별 setprop 등).
    지원 여부는 기기/OS에 따라 다름. 실패 시 메시지 반환.
    반환: (성공 여부, 메시지)
    """
    if block and (custom_block_cmd or "").strip():
        res = adb_shell(device_id, (custom_block_cmd or "").strip(), adb_path=adb_path, timeout_s=10.0)
        return res.ok, res.stdout or res.stderr or ("차단됨" if res.ok else "실패")
    if not block and (custom_unblock_cmd or "").strip():
        res = adb_shell(device_id, (custom_unblock_cmd or "").strip(), adb_path=adb_path, timeout_s=10.0)
        return res.ok, res.stdout or res.stderr or ("허용됨" if res.ok else "실패")

    cmd_action = "block-source" if block else "unblock-source"
    device_ids = get_input_device_ids_for_remote(device_id, adb_path=adb_path)

    def try_cmd(cmd_suffix: str) -> Tuple[bool, str]:
        """cmd_suffix: 'default', ''(공백 없음), 또는 숫자."""
        cmd = f"cmd input {cmd_action}" + (f" {cmd_suffix}" if cmd_suffix else "")
        res = adb_shell(device_id, cmd.strip(), adb_path=adb_path, timeout_s=5.0)
        if res.ok:
            return True, "차단됨" if block else "허용됨"
        err = (res.stderr or res.stdout or "").strip()
        return False, err

    # 1) device_id 목록이 있으면 각 ID로 시도
    if device_ids:
        for iid in device_ids:
            ok, msg = try_cmd(str(iid))
            if ok:
                return True, msg
        last_err = msg
    else:
        last_err = ""

    # 2) 인자 없이 block-source/unblock-source 시도 (일부 기기)
    ok, msg = try_cmd("")
    if ok:
        return True, msg
    if msg and "unknown command" not in msg.lower() and "not found" not in msg.lower():
        last_err = msg

    # 3) "default" 시도
    ok, msg = try_cmd("default")
    if ok:
        return True, msg
    if msg:
        last_err = msg

    hint = " 기기별로 '차단 시:'/ '허용 시:'에 setprop 등 명령을 입력해 보세요."
    return False, (last_err.strip() or "기기에서 이 명령을 지원하지 않습니다.") + hint


def send_channel_number(
    device_ids: Iterable[str],
    channel: str,
    adb_path: str = DEFAULT_ADB,
    digit_delay_s: float = 0.2,
    confirm_ok_for_len_leq_2: bool = True,
    ok_keycode: int = 23,  # DPAD_CENTER
) -> List[Tuple[str, AdbResult]]:
    channel = (channel or "").strip()
    if not channel.isdigit():
        return [(d, AdbResult(ok=False, stdout="", stderr="채널은 숫자만 입력 가능합니다.", returncode=2)) for d in device_ids]

    keyevent_map = {
        "0": 7,
        "1": 8,
        "2": 9,
        "3": 10,
        "4": 11,
        "5": 12,
        "6": 13,
        "7": 14,
        "8": 15,
        "9": 16,
    }

    device_ids_list = [d.strip() for d in device_ids if (d or "").strip()]
    results: List[Tuple[str, AdbResult]] = []

    for digit in channel:
        keycode = keyevent_map.get(digit)
        if keycode is None:
            continue
        results.extend(adb_keyevent_many(device_ids_list, keycode, adb_path=adb_path))
        time.sleep(max(0.0, digit_delay_s))

    if confirm_ok_for_len_leq_2 and len(channel) <= 2:
        time.sleep(max(0.0, digit_delay_s))
        results.extend(adb_keyevent_many(device_ids_list, ok_keycode, adb_path=adb_path))

    return results

