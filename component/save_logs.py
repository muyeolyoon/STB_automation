import os
import threading
import subprocess
import re
from datetime import datetime, timedelta

def is_recent_impression_log(log_line: str, current_time: datetime, threshold_seconds: int = 5) -> bool:
    try:
        match = re.match(r'(\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)', log_line)
        if not match:
            return False
        log_time_str = match.group(1)
        log_time = datetime.strptime(f"{datetime.now().year}-{log_time_str}", "%Y-%m-%d %H:%M:%S.%f")
        return log_time >= current_time - timedelta(seconds=threshold_seconds)
    except Exception as e:
        print(f"로그 시간 파싱 실패: {e}")
        return False

def today_date():
    return datetime.now().strftime("%Y%m%d")

def current_time_str():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]

def get_unique_filename(log_dir, base_name):
    base, ext = os.path.splitext(base_name)
    i = 1
    unique_name = base_name
    while os.path.exists(os.path.join(log_dir, unique_name)):
        unique_name = f"{base}_{i}{ext}"
        i += 1
    return unique_name

def sanitize_device_name(device):
    return device.replace(":", "_").replace(".", "_")


_active_log_processes = []
_active_log_lock = threading.Lock()


def stop_all_device_logs():
    """실행 중인 adb logcat 캡처 프로세스를 모두 종료 (로그 파일 확정)."""
    with _active_log_lock:
        procs = list(_active_log_processes)
        _active_log_processes.clear()
    for proc in procs:
        try:
            if proc.poll() is None:
                proc.terminate()
        except Exception:
            pass


def _line_matches_log_filters(line, filters):
    if not filters:
        return True
    lower = line.lower()
    return any(part.lower() in lower for part in filters)


def save_log_from_device(
    device, log_path, filters=None, on_impression_detected=None, on_log_line=None
):
    # Windows findstr 파이프는 버퍼링/데드락으로 빈 파일이 될 수 있어 Python에서 필터링
    command = ["adb", "-s", device, "logcat", "-v", "time"]

    with open(log_path, "w", encoding="utf-8") as f:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="ignore",
            bufsize=1,
        )
        with _active_log_lock:
            _active_log_processes.append(process)
        try:
            assert process.stdout is not None
            for line in process.stdout:
                if not line:
                    continue

                if on_log_line:
                    on_log_line(device, line)

                if _line_matches_log_filters(line, filters):
                    f.write(line)
                    f.flush()

                if on_impression_detected and "impression log size" in line:
                    log_time_match = re.match(
                        r"(\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d+)", line
                    )
                    if log_time_match:
                        log_time_str = log_time_match.group(1)
                        log_time = datetime.strptime(
                            f"{datetime.now().year}-{log_time_str}",
                            "%Y-%m-%d %H:%M:%S.%f",
                        )
                        if log_time >= datetime.now() - timedelta(seconds=10):
                            on_impression_detected(device, line)
        except Exception as e:
            print(f"[{device}] 로그 저장 중 오류 발생: {e}")
        finally:
            if process.poll() is None:
                process.terminate()

def save_multiple_devices_logs(
    devices,
    log_dir,
    filters=None,
    on_impression_detected=None,
    log_filename=None,
    on_log_line=None,
):
    os.makedirs(log_dir, exist_ok=True)

    if log_filename is None:
        log_filename = input("저장할 로그 파일명을 입력하세요 (예: mylog.log): ").strip()
    else:
        log_filename = log_filename.strip()
    if not log_filename.endswith(".log"):
        log_filename += ".log"

    date_prefix = today_date()

    threads = []
    filenames = {}

    for device in devices:
        device_name = sanitize_device_name(device)
        full_filename = f"{date_prefix}_{device_name}_{log_filename}"
        unique_filename = get_unique_filename(log_dir, full_filename)
        full_path = os.path.join(log_dir, unique_filename)

        t = threading.Thread(
            target=save_log_from_device,
            args=(device, full_path, filters, on_impression_detected, on_log_line),
        )
        t.daemon = True
        t.start()
        threads.append(t)

        filenames[device] = unique_filename
        print(f"[{device}] 로그 저장 시작 -> {unique_filename}")

    return threads, filenames


def print_impression_log_counts(counts):
    """impression log size 등장 횟수 출력.

    1대: 헤더와 횟수를 한 줄, 마지막 로그 시간은 다음 줄.
    여러 대: 헤더만 첫 줄, IP별로 횟수·마지막 로그 시간을 다음 줄부터.
    """
    if not counts:
        print("\n디바이스별 impression log size 등장 횟수: 0회")
        return

    if len(counts) == 1:
        info = next(iter(counts.values()))
        print(f"\n디바이스별 impression log size 등장 횟수: {info['count']}회")
        if info.get("last_time"):
            print(f"마지막 로그 시간: {info['last_time']}")
        return

    print("\n디바이스별 impression log size 등장 횟수:")
    for device, info in counts.items():
        print(f"  {device}: {info['count']}회")
        if info.get("last_time"):
            print(f"  마지막 로그 시간: {info['last_time']}")
