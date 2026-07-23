# Default behavior.py — UTF-8 콘솔/로그 (한글 깨짐 방지)
param(
    # 여러 대: -StbDevices "192.168.10.3,192.168.10.153" 또는 환경변수 STB_DEVICE_IPS
    [string]$StbDevices = "",
    [string]$SkipReboot = "",
    [string]$EscapeChannel = ""
)

$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = [System.Text.UTF8Encoding]::new($false)
chcp 65001 | Out-Null

$RepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path (Join-Path $RepoRoot "stb-rpa"))) {
    $RepoRoot = "D:\python_test\anypointmedia-QA"
}
Set-Location $RepoRoot

$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"

# 체크리스트 1~6: 동일 테스트 STB / 구글 테스트와 같은 mock·QA 엔드포인트 사용 OK
# SKIP_GOOGLE_CHECK=1  — 구글 편성 없을 때만 체크 3 생략
# CHECKLIST_ONLY=1     — 체크리스트 후 편성 모니터링 생략
# SKIP_REBOOT=1 (기본) — 재부팅·체크1(S/W ver.) 모두 스킵, 편성 모니터링만
# SKIP_REBOOT=0       — 시작 시 재부팅 1회 + Firmware/SDK/Agent 버전 확인
#   ※ .py 직접 실행 시 SKIP_REBOOT 미설정이면 대화형으로 y/N 물어봄 (N=스킵 기본)
# local API endpoint (구글/mock): http://{STB_LOCAL_API_HOST}/{모델명}
#   대화형: y → PC 호스트 + 기기별 모델명(UHD3/UHD4K…)
#   비대화형 예:
#     $env:APPLY_LOCAL_API_ENDPOINT="1"; $env:STB_LOCAL_API_MODEL="UHD3"
#     $env:STB_API_ENDPOINT="http://192.168.10.150/UHD3"
#     $env:APPLY_LOCAL_API_ENDPOINT="0"  # 질문/적용 모두 스킵
# VERSION_ONLY=1       — 재부팅+버전(1)만 후 종료
#
# STB 여러 대 (한 프로세스에서 동시 체크리스트·모니터링):
#   STB_DEVICE_IPS="192.168.10.3,192.168.10.153"
#   STB_DEVICE_IP="192.168.10.3 192.168.10.153"  (쉼표·공백·세미콜론 구분 가능)
#   .\run_default_behavior.ps1 -StbDevices "192.168.10.3,192.168.10.153"

if ($StbDevices) { $env:STB_DEVICE_IPS = $StbDevices }
if ($SkipReboot) { $env:SKIP_REBOOT = $SkipReboot }
if ($EscapeChannel) { $env:STB_ESCAPE_CHANNEL = $EscapeChannel }

# 편성표: TV 타겟 지상 채널 큐톤 시간표 (Drive Excel) — YYMMDD 모니터링 탭 ART(U+)
# https://docs.google.com/spreadsheets/d/1fGc1yW9gBoHhJSo57E81FIAeAhNQz2ol/
$ScheduleSheetId = "1fGc1yW9gBoHhJSo57E81FIAeAhNQz2ol"
if (-not $env:DRIVE_SCHEDULE_FILE_ID) { $env:DRIVE_SCHEDULE_FILE_ID = $ScheduleSheetId }
if (-not $env:SPREADSHEET_KEY) { $env:SPREADSHEET_KEY = $ScheduleSheetId }
if (-not $env:SCHEDULE_SOURCE) { $env:SCHEDULE_SOURCE = "drive" }
if (-not $env:SCHEDULE_SECTION) { $env:SCHEDULE_SECTION = "uplus" }
if (-not $env:SKIP_REBOOT) { $env:SKIP_REBOOT = "1" }
# STB_DEVICE_IP / STB_DEVICE_IPS 미설정 시 Python 에서 대수·IP 대화형 입력 (stb_multi 와 동일)
if (-not $env:STB_ESCAPE_CHANNEL) { $env:STB_ESCAPE_CHANNEL = "3" }
if (-not $env:STB_LOG_FILE) { $env:STB_LOG_FILE = "behavior_run.log" }

# 결과를 Google Chat 으로 전송. Default behavior.py 에도 동일 기본값 있음 (.py 단독 실행 OK).
#   끄기: $env:GOOGLE_CHAT_SPACE="0"
#   최초 1회: python stb-rpa/component/chat_notify.py login --client-id "..." --client-secret "..."
if (-not $env:GOOGLE_CHAT_SPACE) { $env:GOOGLE_CHAT_SPACE = "spaces/AAQA_7E-M1k" }

$terminalLog = Join-Path $RepoRoot "test_log\$(
    [System.IO.Path]::GetFileNameWithoutExtension($env:STB_LOG_FILE)
)_terminal.log"
$env:STB_TERMINAL_LOG = $terminalLog
New-Item -ItemType Directory -Force -Path (Split-Path $terminalLog) | Out-Null

$stbLabel = if ($env:STB_DEVICE_IPS) {
    $env:STB_DEVICE_IPS
} elseif ($env:STB_DEVICE_IP) {
    $env:STB_DEVICE_IP
} else {
    "(실행 시 대수·IP 입력)"
}
Write-Host "Terminal UTF-8 log: $terminalLog"
Write-Host "STB: $stbLabel  escape channel: $($env:STB_ESCAPE_CHANNEL)  SKIP_REBOOT: $($env:SKIP_REBOOT)"

# 동일 스크립트 중복 실행 방지 — 기존 python 프로세스 종료 후 1개만 기동
$behaviorScript = "Default behavior.py"
Get-CimInstance Win32_Process -Filter "name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like "*$behaviorScript*" } |
    ForEach-Object {
        Write-Host "기존 Default behavior 종료 PID $($_.ProcessId)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
Start-Sleep -Seconds 1

# term_print 가 STB_TERMINAL_LOG 에 UTF-8 직접 기록 — stdout 파이프 중복 기록 방지
python -u "stb-rpa\my.yoon_test\Default behavior.py"
