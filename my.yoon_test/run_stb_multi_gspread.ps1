# stb_multi_gspread_sheet_read.py — UTF-8 콘솔 (STB IP·이탈채널은 Python 대화형 입력)
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

# STB_DEVICE_IP / STB_DEVICE_IPS / STB_ESCAPE_CHANNEL 미설정 시 Python 에서 대화형 입력
# 비대화형 예: $env:STB_DEVICE_IPS = "192.168.10.153,192.168.10.154"; $env:STB_ESCAPE_CHANNEL = "3"

python -u "stb-rpa\my.yoon_test\stb_multi_gspread_sheet_read.py"
