import subprocess
import os

# 실행 디렉토리
script_dir = os.path.join(os.getcwd(), "stb-rpa", "skb")

scripts = [
    "stb_certification.py",
    "stb_crc.py",
    "stb_cue_basic_motion.py",
    "stb_cue_before_ads.py",
    "stb_cue.py",
    "stb_cue_impression_log.py",
    "stb_cue_playing_ads.py",
    # "stb_normal_end_cue.py"
]

print(f"현재 작업 디렉토리: {os.getcwd()}")
print(f"실행 스크립트 디렉토리: {script_dir}")

for script in scripts:
    print(f"\n 실행 중: {script}")
    result = subprocess.run(
        ["python", script],
        capture_output=True,
        text=True,
        cwd=script_dir 
    )

    print(f"{script} 결과:\n{result.stdout}")
    if result.stderr:
        print(f"[ERROR] {script} 오류:\n{result.stderr}")

    if result.returncode != 0:
        print(f"[STOP] {script}에서 오류 발생. 이후 실행 중단.")
        break

print("\n[INFO] 모든 스크립트 실행 완료.")
