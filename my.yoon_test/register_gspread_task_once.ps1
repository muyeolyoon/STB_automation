# Register one-time Windows scheduled task: run stb_multi_gspread_sheet_read.py at 2026-06-25 08:30
$ErrorActionPreference = "Stop"

$RepoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (-not (Test-Path (Join-Path $RepoRoot "stb-rpa"))) {
    $RepoRoot = "D:\python_test\anypointmedia-QA"
}

$taskName = "STB_GSpread_Sheet_Read_Once"
$runner = Join-Path $RepoRoot "stb-rpa\my.yoon_test\run_stb_multi_gspread.ps1"
$runDate = "2026/06/25"
$runTime = "08:30"
$taskCommand = "powershell.exe -ExecutionPolicy Bypass -NoProfile -File `"$runner`""

if (-not (Test-Path $runner)) {
    throw "Runner not found: $runner"
}

schtasks /create /tn $taskName /tr $taskCommand /sc once /st $runTime /sd $runDate /it /f
if ($LASTEXITCODE -ne 0) {
    throw "schtasks failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Registered: $taskName"
Write-Host "Run at:     $runDate $runTime (interactive, logged-on user only)"
Write-Host "Command:    $taskCommand"
Write-Host ""
schtasks /query /tn $taskName /v /fo LIST | Select-String "TaskName|Next Run Time|Status|Task To Run"
