[CmdletBinding()]
param(
    [string]$QemuPath = 'C:\Program Files\qemu\qemu-system-x86_64.exe',
    [string]$ArtifactDirectory = '',
    [string]$InferenceCache = '',
    [switch]$Smoke,
    [switch]$ServiceSmoke,
    [switch]$Agent,
    [switch]$AgentSmoke,
    [switch]$ResourceSmoke,
    [switch]$CellSmoke,
    [switch]$BackendSmoke,
    [switch]$SpaceSmoke,
    [switch]$TaskSmoke,
    [switch]$GuestTests
)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$vmCache = Join-Path $env:LOCALAPPDATA 'AIOS\hosted-bootstrap\alpine-3.24.1'
$isoPath = Join-Path $vmCache 'alpine.iso'
$isoUrl = 'https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/x86_64/alpine-virt-3.24.1-x86_64.iso'
$expectedHash = 'e73a6241bd5f3c5c2d4d38c02cc52c378c0415a7c888bd292066bf36e0f41a39'
if (-not (Test-Path -LiteralPath $QemuPath -PathType Leaf)) { throw "QEMU executable missing: $QemuPath" }
$pythonLauncher = (Get-Command py.exe -ErrorAction Stop).Source
$archiveTool = (Get-Command tar.exe -ErrorAction Stop).Source
if ($Agent -or $AgentSmoke -or $ResourceSmoke -or $CellSmoke -or $BackendSmoke -or $SpaceSmoke -or $TaskSmoke) {
    if ($Smoke -or $ServiceSmoke) { throw 'Choose one console smoke workflow.' }
    if ([string]::IsNullOrWhiteSpace($InferenceCache)) {
        $InferenceCache = Join-Path $env:LOCALAPPDATA 'AIOS\hosted-inference'
    }
    Write-Host '[AIOS] Checking the pinned model and backend cache.'
    & $pythonLauncher -3 (Join-Path $PSScriptRoot 'prepare_inference.py') --cache $InferenceCache
    if ($LASTEXITCODE -ne 0) { throw 'MAIN dependencies could not be prepared. No VM was started.' }
}
New-Item -ItemType Directory -Path $vmCache -Force | Out-Null
if (-not (Test-Path -LiteralPath $isoPath -PathType Leaf)) {
    Write-Host '[AIOS] Downloading the Linux development hardware layer.'
    Invoke-WebRequest -UseBasicParsing -Uri $isoUrl -OutFile $isoPath
}
if ((Get-FileHash -LiteralPath $isoPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
    throw 'Development image checksum mismatch. No VM was started.'
}
& $archiveTool -xf $isoPath -C $vmCache boot/vmlinuz-virt boot/initramfs-virt
if ($LASTEXITCODE -ne 0) { throw 'Could not extract the development kernel and initramfs.' }
if ([string]::IsNullOrWhiteSpace($ArtifactDirectory)) {
    $runName = 'console-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 6)
    $ArtifactDirectory = Join-Path $repoRoot ('build\hosted-console\' + $runName)
}
$consoleArgs = @('-3', (Join-Path $PSScriptRoot 'qemu_console.py'), '--qemu', $QemuPath,
    '--iso', $isoPath, '--sha256', $expectedHash, '--kernel', (Join-Path $vmCache 'boot\vmlinuz-virt'),
    '--initramfs', (Join-Path $vmCache 'boot\initramfs-virt'), '--artifact-dir', $ArtifactDirectory)
if ($Smoke) { $consoleArgs += '--smoke' }
if ($ServiceSmoke) { $consoleArgs += '--service-smoke' }
if ($Agent) { $consoleArgs += '--agent' }
if ($AgentSmoke) { $consoleArgs += '--agent-smoke' }
if ($ResourceSmoke) { $consoleArgs += '--resource-smoke' }
if ($CellSmoke) { $consoleArgs += '--cell-smoke' }
if ($BackendSmoke) { $consoleArgs += '--backend-smoke' }
if ($SpaceSmoke) { $consoleArgs += '--space-smoke' }
if ($TaskSmoke) { $consoleArgs += '--task-smoke' }
if ($Agent -or $AgentSmoke -or $ResourceSmoke -or $CellSmoke -or $BackendSmoke -or $SpaceSmoke -or $TaskSmoke) { $consoleArgs += @('--inference-cache', $InferenceCache) }
if ($GuestTests) { $consoleArgs += '--guest-tests' }
& $pythonLauncher @consoleArgs
exit $LASTEXITCODE
