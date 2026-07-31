<#
.SYNOPSIS
    One unattended pass: doctor -> scan -> advance every open job.

.DESCRIPTION
    A thin wrapper over `cli.py auto-run`, which is where the actual logic
    lives. That split is deliberate. The first version of this script held
    the orchestration itself, and it could not be tested — the lock, the
    power check and the retention sweep were all trusted rather than
    verified, and one unrecognised cmdlet (`Get-CimInstance`, absent on
    PowerShell for Linux) took the whole pass down before it ran a single
    step. In Python the same logic has real coverage, and this file only has
    to do the one thing Task Scheduler needs: name something to run.

    So keep this file boring. New behaviour belongs in
    `pipeline/automation/auto_run.py`, where it can be tested.

.PARAMETER WhatIfOnly
    Print the plan and exit without touching a job. Run this first.

.PARAMETER OperatorName
    Recorded on anything this pass accepts, alongside the machine verdict.

.PARAMETER Gates
    Which gates may open. Defaults to all five ('unattended'). Pass a subset
    like 'templates','layout' to bring them up one at a time — the
    recommended way to start.

.PARAMETER MaxJobs
    Safety cap on jobs advanced in one pass (default 10).

.PARAMETER Last
    Print the most recent pass summary and exit.

.EXAMPLE
    .\tools\owcs-auto-run.ps1 -WhatIfOnly

.EXAMPLE
    .\tools\owcs-auto-run.ps1 -Gates templates,layout

.EXAMPLE
    .\tools\owcs-auto-run.ps1 -Last
#>
[CmdletBinding()]
param(
    [switch] $WhatIfOnly,
    [string] $OperatorName = $env:USERNAME,
    # A STRING, split here, not a [string[]] with a ValidateSet. Task
    # Scheduler invokes this with `powershell -File`, and `-File` binds every
    # argument as a single literal token — so `-Gates templates,layout`
    # arrives as one string and a [string[]]+ValidateSet parameter rejects it
    # outright. Splitting by hand is what makes the scheduled task able to
    # pass more than one gate at all.
    [string] $Gates = 'unattended',
    [int] $MaxJobs = 10,
    [switch] $IgnoreBattery,
    [switch] $SkipDoctor,
    [switch] $Last
)

# NOT 'Stop': a non-zero exit from the pass is normal (a held gate, a
# skipped run on battery), and this wrapper must report it rather than
# turning it into a PowerShell exception.
$ErrorActionPreference = 'Continue'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$Cli      = Join-Path $RepoRoot 'pipeline' 'automation' 'cli.py'

$python = $null
foreach ($candidate in @('python', 'python3', 'py')) {
    if (Get-Command $candidate -ErrorAction SilentlyContinue) {
        $python = $candidate; break
    }
}
if (-not $python) {
    Write-Error "No Python on PATH. Run .\tools\setup-machine.ps1 first."
    exit 1
}
if (-not (Test-Path $Cli)) {
    Write-Error "cli.py not found at $Cli"
    exit 1
}

$known = @('unattended', 'source', 'layout', 'templates', 'detect', 'publish')
$wanted = $Gates -split '[,\s]+' | Where-Object { $_ }
$bad = $wanted | Where-Object { $known -notcontains $_ }
if ($bad) {
    Write-Error ("Unknown gate(s): {0}. Valid: {1}" -f ($bad -join ', '), ($known -join ', '))
    exit 1
}

$cliArgs = @('auto-run', '--operator', $OperatorName, '--max-jobs', $MaxJobs)
foreach ($g in $wanted) {
    $cliArgs += if ($g -eq 'unattended') { '--unattended' } else { "--auto-$g" }
}
if ($WhatIfOnly)    { $cliArgs += '--what-if' }
if ($IgnoreBattery) { $cliArgs += '--ignore-battery' }
if ($SkipDoctor)    { $cliArgs += '--skip-doctor' }
if ($Last)          { $cliArgs = @('auto-run', '--last') }

& $python $Cli @cliArgs
exit $LASTEXITCODE
