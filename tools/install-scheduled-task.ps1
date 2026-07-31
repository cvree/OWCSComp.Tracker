<#
.SYNOPSIS
    Register the nightly unattended OWCS pass as a Windows scheduled task.

.DESCRIPTION
    Run this ONCE, from an elevated PowerShell. It registers a task that
    invokes `tools\owcs-auto-run.ps1` on a schedule, with settings chosen so
    the pass can never become a nuisance:

      * AC power only, and it stops if the machine switches to battery;
      * it does not wake the machine — if the box is off, the pass is simply
        skipped and the next one picks the work up;
      * a 4-hour execution limit, so a wedged download cannot hold the lock
        for a week;
      * missed runs are started late rather than skipped, which is what you
        want on a laptop that is not always on at 03:00.

    The task runs as the invoking user (not SYSTEM) so it inherits the same
    Python, PATH and credentials an interactive run uses. A task that works
    only when you run it by hand is worse than no task.

.PARAMETER OperatorName
    Recorded on everything the pass accepts.

.PARAMETER At
    Daily start time, 24h 'HH:mm'. Default 03:00.

.PARAMETER Gates
    Which gates the nightly pass may open. Defaults to all five. Start with
    a subset if you would rather bring them up one at a time.

.PARAMETER Unregister
    Remove the task instead of creating it.

.EXAMPLE
    .\tools\install-scheduled-task.ps1 -OperatorName "Connor"

.EXAMPLE
    .\tools\install-scheduled-task.ps1 -OperatorName "Connor" -Gates templates
#>
[CmdletBinding()]
param(
    [string] $OperatorName = $env:USERNAME,
    [string] $At = '03:00',
    # Comma-separated, passed straight through to the runner. See the note
    # in owcs-auto-run.ps1: `-File` cannot bind a real array, so the runner
    # takes a string and splits it. Keeping the same shape here means the
    # command this registers is the command you can also type by hand.
    [string] $Gates = 'unattended',
    [string] $TaskName = 'OWCS Comp Tracker - nightly unattended pass',
    [switch] $Unregister
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Runner   = Join-Path $PSScriptRoot 'owcs-auto-run.ps1'

$identity = [Security.Principal.WindowsPrincipal]::new(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Registering a scheduled task needs an elevated PowerShell. Right-click -> Run as administrator, then re-run this."
}

if ($Unregister) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Host "Removed scheduled task '$TaskName'."
    exit 0
}

if (-not (Test-Path $Runner)) { throw "runner not found: $Runner" }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
    "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Runner`" " +
    "-OperatorName `"$OperatorName`" -Gates `"$Gates`"") -WorkingDirectory $RepoRoot

$trigger = New-ScheduledTaskTrigger -Daily -At $At

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries:$false `
    -DontStopIfGoingOnBatteries:$false `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4) `
    -MultipleInstances IgnoreNew `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 30)

$principal = New-ScheduledTaskPrincipal `
    -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Settings $settings -Principal $principal -Force | Out-Null

Write-Host ""
Write-Host "Registered '$TaskName'."
Write-Host "  runs      : daily at $At (AC power only, skipped on battery)"
Write-Host "  gates     : $Gates"
Write-Host "  operator  : $OperatorName"
Write-Host "  logs      : $(Join-Path $RepoRoot 'data\auto-run-logs')"
Write-Host ""
Write-Host "See exactly what it would do, without touching anything:"
Write-Host "  .\tools\owcs-auto-run.ps1 -WhatIfOnly"
Write-Host ""
Write-Host "Run it once now to confirm it works end to end:"
Write-Host "  Start-ScheduledTask -TaskName `"$TaskName`""
