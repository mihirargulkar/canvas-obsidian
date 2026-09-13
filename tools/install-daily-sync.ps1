# Install (or remove) a daily background sync via Task Scheduler on Windows.
#
#   powershell -ExecutionPolicy Bypass -File tools\install-daily-sync.ps1
#   powershell -ExecutionPolicy Bypass -File tools\install-daily-sync.ps1 -At 18:00
#   powershell -ExecutionPolicy Bypass -File tools\install-daily-sync.ps1 -Uninstall
#
# Runs `python -m canvas_vault.sync --quiet`, which writes to the log only when
# something actually changed. Logs: cache\sync.log (and cache\sync.err).
# (macOS/Linux: tools/install-daily-sync.sh)
param([string]$At = "07:30", [switch]$Uninstall)
$ErrorActionPreference = "Stop"

$Repo = Split-Path $PSScriptRoot -Parent
$Name = "canvas-obsidian daily sync"

if ($Uninstall) {
  if (Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $Name -Confirm:$false
    Write-Host "removed: $Name"
  } else { Write-Host "not installed" }
  exit 0
}

$py = Join-Path $Repo ".venv\Scripts\pythonw.exe"      # pythonw: no console flash
if (-not (Test-Path $py)) { $py = Join-Path $Repo ".venv\Scripts\python.exe" }
if (-not (Test-Path $py)) { throw "no virtualenv at $Repo\.venv - run setup.ps1 first" }

# cmd /c so stdout/stderr can be redirected to the same log files the mac job uses
$log = Join-Path $Repo "cache\sync.log"
$err = Join-Path $Repo "cache\sync.err"
New-Item -ItemType Directory -Force -Path (Join-Path $Repo "cache") | Out-Null

$action  = New-ScheduledTaskAction -Execute "cmd.exe" `
  -Argument "/c `"`"$py`" -m canvas_vault.sync --quiet >> `"$log`" 2>> `"$err`"`"" `
  -WorkingDirectory $Repo
$trigger = New-ScheduledTaskTrigger -Daily -At $At
# StartWhenAvailable: a laptop asleep at 07:30 still syncs when it wakes, which
# is the normal case for a student machine rather than the exception.
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
  -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries `
  -ExecutionTimeLimit (New-TimeSpan -Hours 2)

Register-ScheduledTask -TaskName $Name -Action $action -Trigger $trigger `
  -Settings $settings -Description "Sync Canvas courses into the local Obsidian vault" `
  -Force | Out-Null

Write-Host "installed: daily sync at $At"
Write-Host "  logs:     $log   (written only when something changed)"
Write-Host "  run now:  Start-ScheduledTask -TaskName '$Name'"
Write-Host "  remove:   powershell -ExecutionPolicy Bypass -File tools\install-daily-sync.ps1 -Uninstall"
