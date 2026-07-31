# =====================================================================
# owcs-auto-run.ps1 — one unattended pass of the OWCS pipeline.
# ---------------------------------------------------------------------
# This is what Windows Task Scheduler runs (see install-scheduled-task.ps1).
# It does exactly what an operator would do by hand, in order:
#
#   1. worker-doctor  — refuse the pass EARLY and loudly if the machine
#      cannot do the work (missing yt-dlp/ffmpeg/disk). A scheduled run
#      that silently does nothing for a week is worse than one that fails.
#   2. auto-run       — scan the verified channels for new broadcasts,
#      queue the likely ones through the normal intake gate, then advance
#      every tracked job as far as its evidence allows.
#
# It never approves a SOURCE (that gate has no automatic path, by design),
# and every other gate still applies its quality floors — run
# `unattended-floors` to see them.
#
# Everything is logged, one file per day, so a morning glance answers
# "what did it do last night?".
# =====================================================================
[CmdletBinding()]
param(
    # Repo root. Defaults to this script's parent directory.
    [string]$RepoRoot = (Split-Path -Parent $PSScriptRoot),

    # Python to run the pipeline with. A venv's python.exe works here.
    [string]$Python = "python",

    # Most jobs advanced in one pass. Each can mean a multi-hour download,
    # so the default is deliberately small.
    [int]$MaxJobs = 3,

    # Your name/handle — recorded on every automatic acceptance, so the
    # audit trail names a responsible human even when nobody typed.
    [string]$OperatorName = $env:USERNAME,

    # Where the daily logs go.
    [string]$LogDir = (Join-Path (Split-Path -Parent $PSScriptRoot) "data\auto-run-logs"),

    # Skip the channel scan and only advance jobs already tracked.
    [switch]$NoScan,

    # Report what WOULD happen: the doctor plus a job listing, no work.
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Stop"
Set-Location $RepoRoot

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
$stamp = Get-Date -Format "yyyy-MM-dd"
$log = Join-Path $LogDir "auto-run-$stamp.log"

function Write-Log {
    param([string]$Message)
    $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $Message
    Write-Host $line
    Add-Content -Path $log -Value $line
}

Write-Log "=== auto-run start (repo: $RepoRoot) ==="

# --- 1. Can this machine do the work at all? -------------------------
Write-Log "checking the download stack (worker-doctor)..."
& $Python "pipeline\automation\cli.py" worker-doctor 2>&1 |
    Tee-Object -FilePath $log -Append | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Log "ABORT: worker-doctor reported a missing dependency. Fix it and "
    Write-Log "       re-run; nothing was downloaded or changed."
    exit 1
}
Write-Log "worker-doctor: ok"

if ($WhatIfOnly) {
    Write-Log "--WhatIfOnly: listing tracked jobs, doing no work"
    & $Python "pipeline\automation\cli.py" link-status 2>&1 |
        Tee-Object -FilePath $log -Append
    Write-Log "=== auto-run end (what-if) ==="
    exit 0
}

# --- 2. The pass -----------------------------------------------------
$autoArgs = @(
    "pipeline\automation\cli.py", "auto-run",
    "--unattended",
    "--auto-accept",
    "--accepted-by", $OperatorName,
    "--requested-by", $OperatorName,
    "--max-jobs", $MaxJobs
)
if ($NoScan) { $autoArgs += "--no-scan" }

Write-Log ("running: {0} {1}" -f $Python, ($autoArgs -join " "))
& $Python @autoArgs 2>&1 | Tee-Object -FilePath $log -Append
$code = $LASTEXITCODE

if ($code -ne 0) {
    Write-Log "auto-run exited $code — see the log above for the failing stage"
} else {
    Write-Log "auto-run finished"
}

# Keep 30 days of logs; a scheduled job should not grow without bound.
Get-ChildItem $LogDir -Filter "auto-run-*.log" |
    Sort-Object LastWriteTime -Descending |
    Select-Object -Skip 30 |
    Remove-Item -Force -ErrorAction SilentlyContinue

Write-Log "=== auto-run end ==="
exit $code
