param(
    [ValidateSet('all', 'test', 'run', 'run-headless', 'debug', 'clean', 'info', 'iso')]
    [string]$Target = 'all'
)

$ErrorActionPreference = 'Stop'

$RepoRoot = Split-Path -Parent $PSScriptRoot
$TargetScript = Join-Path $RepoRoot 'testkit\kernel\build-windows.ps1'

if (-not (Test-Path $TargetScript)) {
    throw "Delegated testkit script not found: $TargetScript"
}

& $TargetScript -Target $Target
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
