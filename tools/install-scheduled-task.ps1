# =====================================================================
# install-scheduled-task.ps1 — register the unattended OWCS pass with
# Windows Task Scheduler.
# ---------------------------------------------------------------------
# Run once, in an ELEVATED PowerShell (Task Scheduler registration needs
# admin). Re-running updates the existing task rather than creating a
# second one.
#
#   .\tools\install-scheduled-task.ps1 -OperatorName "Connor"
#   .\tools\install-scheduled-task.ps1 -At "03:30" -MaxJobs 2
#   .\tools\install-scheduled-task.ps1 -Remove
#
# What the task does: runs tools\owcs-auto-run.ps1 daily. Because a full
# broadcast is a multi-hour download, the default schedule is once a night
# rather than every few minutes — the match finder catches up on whatever
# appeared during the day in a single pass.
#
# The task runs ONLY when the machine is on AC power by default (a laptop
# should not burn its battery downloading a 4-hour VOD) and wakes the
# machine if it is asleep. Both are switches below.
# =====================================================================
[CmdletBinding()]
param(
    [string]$TaskName = "OWCS Comp Tracker — nightly auto-run",
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$Python = "python",
    [string]$At = "03:00",
    [int]$MaxJobs = 3,
    [string]$OperatorName = $env:USERNAME,
    [switch]$AllowOnBattery,
    [switch]$NoWake,
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

if ($Remove) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "removed scheduled task: $TaskName"
    } else {
        Write-Host "no such scheduled task: $TaskName"
    }
    exit 0
}

$script = Join-Path $PSScriptRoot "owcs-auto-run.ps1"
if (-not (Test-Path $script)) {
    throw "cannot find $script — run this from the repo's tools\ directory"
}

$argline = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", "`"$script`"",
    "-RepoRoot", "`"$RepoRoot`"",
    "-Python", "`"$Python`"",
    "-MaxJobs", $MaxJobs,
    "-OperatorName", "`"$OperatorName`""
) -join " "

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument $argline -WorkingDirectory $RepoRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $At

$settingsArgs = @{
    StartWhenAvailable          = $true   # a missed run (machine off) runs at next boot
    ExecutionTimeLimit          = (New-TimeSpan -Hours 8)
    MultipleInstances           = "IgnoreNew"
    RestartCount                = 2
    RestartInterval             = (New-TimeSpan -Minutes 15)
}
if ($AllowOnBattery) {
    $settingsArgs["AllowStartIfOnBatteries"] = $true
    $settingsArgs["DontStopIfGoingOnBatteries"] = $true
}
if (-not $NoWake) { $settingsArgs["WakeToRun"] = $true }
$settings = New-ScheduledTaskSettingsSet @settingsArgs

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Description (
        "Scans the verified OWCS broadcast channels, ingests new broadcasts " +
        "through the normal intake gate, and advances every tracked job as " +
        "far as its evidence allows. Source approval is never automatic; " +
        "every other gate applies the quality floors from " +
        "config/automation.yml (see: cli.py unattended-floors)."
    ) -Force | Out-Null

Write-Host "registered: $TaskName"
Write-Host "  runs      : daily at $At (max $MaxJobs job(s) per pass)"
Write-Host "  script    : $script"
Write-Host "  logs      : $RepoRoot\data\auto-run-logs\"
Write-Host ""
Write-Host "Test it now without waiting for the schedule:"
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`""
Write-Host "Or dry-run the script directly (doctor + job list, no work):"
Write-Host "  .\tools\owcs-auto-run.ps1 -WhatIfOnly"
