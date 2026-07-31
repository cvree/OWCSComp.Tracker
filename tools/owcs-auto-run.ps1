<#
.SYNOPSIS
    One unattended pass: doctor -> scan -> queue -> advance every open job.

.DESCRIPTION
    This is the whole hands-off loop, in the order that fails loudest first.

      1. worker-doctor. A pass that CANNOT work must say so instead of
         quietly doing nothing for six hours. If yt-dlp, ffmpeg or the job
         database is missing, the run stops here with a non-zero exit and a
         log that names the missing piece — a silent no-op is the one
         outcome an unattended system must never produce.
      2. find-matches. Scans the verified official channels for new
         broadcasts and records candidates in the ledger.
      3. Queue. Every discovered link is ingested; registry channels are
         authorized on the spot, everything else waits on the source gate.
      4. Advance. Each open job is driven through `autopilot --unattended`,
         which clears each gate ONLY when its metrics do. A job that stops
         at a held gate is a normal outcome, not an error.

    Nothing here loosens a gate. The floors that decide every approval are
    printed at the top of each log (`unattended-floors`), so a log alone is
    enough to reconstruct why a given night approved what it did.

.PARAMETER WhatIfOnly
    Print the plan and the live floors, then exit without touching a job.
    Run this first.

.PARAMETER OperatorName
    Recorded on anything this pass accepts, alongside the machine verdict.

.PARAMETER Gates
    Which gates may open. Defaults to all five ('unattended'). Pass a subset
    like 'source','layout' to bring them up one at a time — the recommended
    way to start.

.PARAMETER MaxJobs
    Safety cap on jobs advanced in one pass (default 10).

.EXAMPLE
    .\tools\owcs-auto-run.ps1 -WhatIfOnly

.EXAMPLE
    .\tools\owcs-auto-run.ps1 -Gates templates,layout
#>
[CmdletBinding()]
param(
    [switch] $WhatIfOnly,
    [string] $OperatorName = $env:USERNAME,
    [ValidateSet('unattended', 'source', 'layout', 'templates', 'detect', 'publish')]
    [string[]] $Gates = @('unattended'),
    [int] $MaxJobs = 10,
    [switch] $IgnoreBattery
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Cli      = Join-Path $RepoRoot 'pipeline\automation\cli.py'
$LogDir   = Join-Path $RepoRoot 'data\auto-run-logs'
$LockFile = Join-Path $LogDir '.running.lock'
$Stamp    = Get-Date -Format 'yyyy-MM-dd_HHmmss'
$LogFile  = Join-Path $LogDir "auto-run_$Stamp.log"

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

function Write-Log {
    param([string] $Message, [string] $Level = 'INFO')
    $line = "{0} [{1}] {2}" -f (Get-Date -Format 'HH:mm:ss'), $Level, $Message
    Write-Host $line
    Add-Content -Path $LogFile -Value $line -Encoding utf8
}

function Invoke-Cli {
    <# Run one CLI subcommand, tee its output into the log, return the exit
       code. Never throws on a non-zero exit: a held gate is a legitimate
       outcome and must not abort the pass for the remaining jobs. #>
    param([string[]] $CliArgs)
    Write-Log ("> python cli.py " + ($CliArgs -join ' '))
    $output = & python $Cli @CliArgs 2>&1
    $code = $LASTEXITCODE
    foreach ($line in $output) { Add-Content -Path $LogFile -Value "    $line" -Encoding utf8 }
    if ($code -ne 0) { Write-Log "exit code $code" 'WARN' }
    return $code
}

# ---------------------------------------------------------------- preflight
Write-Log "OWCS unattended pass starting (operator: $OperatorName)"
Write-Log "repo: $RepoRoot"

# A previous pass still running means the machine woke up before the last
# one finished. Two concurrent passes would fight over the job locks and the
# media cache, so this one steps aside rather than racing.
if (Test-Path $LockFile) {
    $held = Get-Content $LockFile -Raw -ErrorAction SilentlyContinue
    $age  = (Get-Date) - (Get-Item $LockFile).LastWriteTime
    if ($age.TotalHours -lt 12) {
        Write-Log "a previous pass is still running (started $held, $([int]$age.TotalMinutes)m ago) — skipping this one" 'WARN'
        exit 0
    }
    Write-Log "stale lock from $held ($([int]$age.TotalHours)h old) — taking over" 'WARN'
}

# Downloading and decoding a VOD on battery drains a laptop fast, and the
# nightly pass is never urgent enough to justify it.
if (-not $IgnoreBattery) {
    $battery = Get-CimInstance -ClassName Win32_Battery -ErrorAction SilentlyContinue
    if ($battery -and $battery.BatteryStatus -eq 1) {
        Write-Log "running on battery — skipping (pass -IgnoreBattery to override)" 'WARN'
        exit 0
    }
}

$gateFlags = @()
foreach ($g in $Gates) { $gateFlags += "--auto-$g" -replace '--auto-unattended', '--unattended' }

Write-Log "gates for this pass: $($gateFlags -join ' ')"
Write-Log "--- floors in force ---"
Invoke-Cli (@('unattended-floors') + $gateFlags) | Out-Null

if ($WhatIfOnly) {
    Write-Log "WhatIfOnly: the pass would now run worker-doctor, find-matches, then advance up to $MaxJobs job(s) with $($gateFlags -join ' ')"
    Write-Log "nothing was touched. Log: $LogFile"
    exit 0
}

Set-Content -Path $LockFile -Value (Get-Date -Format 'o') -Encoding utf8

try {
    # 1. Doctor first — a pass that cannot work fails loudly.
    Write-Log "--- 1/4 worker-doctor ---"
    if ((Invoke-Cli @('worker-doctor')) -ne 0) {
        Write-Log "worker-doctor FAILED — this pass could not have done real work, so it stops here instead of reporting a quiet success" 'ERROR'
        exit 1
    }

    # 2. Scan for new broadcasts.
    Write-Log "--- 2/4 find-matches ---"
    Invoke-Cli @('find-matches') | Out-Null

    # 3/4. Advance every open job.
    Write-Log "--- 3/4 open jobs ---"
    # `list-jobs --json` prints a bare array of job rows serialized straight
    # off the dataclass, so the keys are snake_case (job_key, not jobKey).
    $jobsJson = & python $Cli list-jobs --json 2>&1 | Out-String
    $jobs = @()
    try {
        $jobs = @($jobsJson | ConvertFrom-Json | Where-Object {
            $_.state -notin @('PUBLISHED', 'FAILED_PERMANENT', 'IGNORED', 'CANCELLED')
        })
    } catch {
        Write-Log "could not parse list-jobs output as JSON: $_" 'ERROR'
        exit 1
    }
    Write-Log "$($jobs.Count) open job(s)"

    Write-Log "--- 4/4 advance ---"
    $advanced = 0
    foreach ($job in $jobs) {
        if ($advanced -ge $MaxJobs) {
            Write-Log "reached -MaxJobs $MaxJobs — leaving $($jobs.Count - $advanced) job(s) for the next pass"
            break
        }
        $key = $job.job_key
        Write-Log "advancing $key (state $($job.state))"
        Invoke-Cli (@('autopilot', '--job', $key, '--auto-accept',
                      '--accepted-by', $OperatorName) + $gateFlags) | Out-Null
        $advanced++
    }

    Write-Log "pass complete: $advanced job(s) advanced. Log: $LogFile"
}
finally {
    Remove-Item $LockFile -ErrorAction SilentlyContinue
    # Keep the last 30 logs; an unattended system that fills a disk with its
    # own logs has found a novel way to stop working.
    Get-ChildItem $LogDir -Filter 'auto-run_*.log' |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 30 |
        Remove-Item -ErrorAction SilentlyContinue
}
