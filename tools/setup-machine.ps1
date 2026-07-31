<#
.SYNOPSIS
    Make this PC able to run the pipeline: install what worker-doctor needs.

.DESCRIPTION
    `worker-doctor` tells you what is missing. This installs it.

      * Python packages from requirements.txt (opencv, numpy, ...)
      * yt-dlp (pip, kept on the nightly build so YouTube changes don't
        strand you on a stale extractor)
      * ffmpeg + ffprobe (winget, falling back to a user-local download)

    Everything is idempotent: a component already present is reported and
    skipped, never reinstalled. Nothing here needs administrator rights —
    winget installs to the user scope and pip to the user site — which is
    deliberate, because a setup step that demands elevation is one more
    reason the machine ends up half-configured.

    Finishes by running worker-doctor so you see the actual state rather
    than this script's opinion of it.

.PARAMETER SkipPython
    Leave Python packages alone (useful inside a managed venv).

.PARAMETER WhatIfOnly
    Report what is missing and what would be installed. Changes nothing.

.EXAMPLE
    .\tools\setup-machine.ps1

.EXAMPLE
    .\tools\setup-machine.ps1 -WhatIfOnly
#>
[CmdletBinding()]
param(
    [switch] $SkipPython,
    [switch] $WhatIfOnly
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Cli      = Join-Path $RepoRoot 'pipeline\automation\cli.py'

function Write-Step { param([string] $m) Write-Host "`n=== $m ===" -ForegroundColor Cyan }
function Write-Ok   { param([string] $m) Write-Host "  [ok]   $m" -ForegroundColor Green }
function Write-Miss { param([string] $m) Write-Host "  [need] $m" -ForegroundColor Yellow }
function Write-Bad  { param([string] $m) Write-Host "  [fail] $m" -ForegroundColor Red }

function Test-Command {
    param([string] $Name)
    # Get-Command is the portable probe; `where.exe` is not on non-Windows
    # and `which` is not on Windows.
    return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Get-PythonExe {
    foreach ($candidate in @('python', 'python3', 'py')) {
        if (Test-Command $candidate) { return $candidate }
    }
    return $null
}

# ------------------------------------------------------------------ python
Write-Step 'Python'
$python = Get-PythonExe
if (-not $python) {
    Write-Bad 'No Python on PATH.'
    Write-Host '    Install Python 3.11+ from https://www.python.org/downloads/'
    Write-Host '    (tick "Add python.exe to PATH" in the installer), then re-run this.'
    exit 1
}
$pyVersion = & $python --version 2>&1
Write-Ok "$python -> $pyVersion"

if (-not $SkipPython) {
    Write-Step 'Python packages'
    $req = Join-Path $RepoRoot 'requirements.txt'
    if (Test-Path $req) {
        if ($WhatIfOnly) {
            Write-Miss "would run: $python -m pip install -r requirements.txt"
        } else {
            & $python -m pip install --quiet --upgrade pip
            & $python -m pip install --quiet -r $req
            if ($LASTEXITCODE -ne 0) { Write-Bad "pip install failed (exit $LASTEXITCODE)" }
            else { Write-Ok 'requirements.txt satisfied' }
        }
    } else {
        Write-Miss "no requirements.txt at $req"
    }

    # yt-dlp changes fast because YouTube does; the nightly build is the
    # one that still works when the stable release has been broken by a
    # site change. This is the same version the download ladder expects.
    Write-Step 'yt-dlp'
    if ($WhatIfOnly) {
        Write-Miss 'would run: pip install -U --pre yt-dlp'
    } else {
        & $python -m pip install --quiet -U --pre yt-dlp
        if ($LASTEXITCODE -eq 0) { Write-Ok "yt-dlp $(& yt-dlp --version 2>&1)" }
        else { Write-Bad 'yt-dlp install failed' }
    }
}

# ------------------------------------------------------------------ ffmpeg
Write-Step 'ffmpeg / ffprobe'
$haveFfmpeg  = Test-Command 'ffmpeg'
$haveFfprobe = Test-Command 'ffprobe'

if ($haveFfmpeg -and $haveFfprobe) {
    Write-Ok 'ffmpeg and ffprobe already on PATH'
} elseif ($WhatIfOnly) {
    Write-Miss 'would install ffmpeg (winget install Gyan.FFmpeg)'
} elseif (Test-Command 'winget') {
    Write-Host '  installing ffmpeg via winget (user scope, no elevation)...'
    & winget install --id Gyan.FFmpeg --scope user --accept-source-agreements `
        --accept-package-agreements --silent
    # winget updates PATH for NEW shells only, so this one still cannot see
    # it — say so rather than letting the doctor below look like a failure.
    Write-Host '  winget finished. ffmpeg lands on PATH for NEW shells;' -ForegroundColor Yellow
    Write-Host '  close this window and open a fresh one before the nightly task runs.' -ForegroundColor Yellow
} else {
    Write-Bad 'ffmpeg/ffprobe missing and winget is unavailable.'
    Write-Host '    Download a build from https://www.gyan.dev/ffmpeg/builds/ ,'
    Write-Host '    unzip it, and add its bin\ directory to your PATH.'
    Write-Host '    Both ffmpeg.exe AND ffprobe.exe are required.'
}

# ------------------------------------------------------------- optional keys
Write-Step 'Optional API keys'
if ($env:YOUTUBE_API_KEY) {
    Write-Ok 'YOUTUBE_API_KEY is set'
} else {
    Write-Host '  [note] YOUTUBE_API_KEY is not set — this is FINE.' -ForegroundColor DarkGray
    Write-Host '         Discovery and the unattended source gate both work without it:' -ForegroundColor DarkGray
    Write-Host '         a video found in a verified channel own feed carries the same' -ForegroundColor DarkGray
    Write-Host '         channel binding the API would report. A key only adds richer' -ForegroundColor DarkGray
    Write-Host '         metadata.' -ForegroundColor DarkGray
}

# ------------------------------------------------------------------- verify
Write-Step 'Verifying with worker-doctor'
if ($WhatIfOnly) {
    Write-Host '  (skipped in -WhatIfOnly)'
    exit 0
}
& $python $Cli worker-doctor
$doctor = $LASTEXITCODE

Write-Host ''
if ($doctor -eq 0) {
    Write-Host 'This machine can run the pipeline.' -ForegroundColor Green
    Write-Host ''
    Write-Host 'Next:'
    Write-Host "  $python pipeline\automation\cli.py auto-run --what-if --unattended"
    Write-Host '  .\tools\install-scheduled-task.ps1 -OperatorName "<your name>"'
} else {
    Write-Host 'worker-doctor still reports problems (see above).' -ForegroundColor Yellow
    Write-Host 'If you just installed ffmpeg, open a NEW shell and re-run this script.'
}
exit $doctor
