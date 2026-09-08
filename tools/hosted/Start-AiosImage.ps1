[CmdletBinding()]
param(
    [ValidateSet('Build', 'Smoke', 'Run', 'Select', 'Selection', 'Rollback')][string]$Action = 'Run',
    [string]$ImageDirectory = '',
    [string]$QemuPath = 'C:\Program Files\qemu\qemu-system-x86_64.exe',
    [string]$InferenceCache = '',
    [switch]$Agent,
    [switch]$Offline,
    [switch]$GuestTests
)
$ErrorActionPreference = 'Stop'
$repoRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$pythonLauncher = (Get-Command py.exe -ErrorAction Stop).Source
$imageRunner = Join-Path $PSScriptRoot 'qemu_image.py'
if ($Action -in @('Run', 'Select', 'Selection', 'Rollback')) {
    $selectionAction = switch ($Action) {
        'Selection' { 'show' }
        default { $Action.ToLowerInvariant() }
    }
    $selectionArgs = @('-3', '-B', (Join-Path $PSScriptRoot 'image_selection.py'), $selectionAction)
    if (-not [string]::IsNullOrWhiteSpace($ImageDirectory)) { $selectionArgs += @('--image-dir', $ImageDirectory) }
    if ($Agent) { $selectionArgs += '--agent' }
    if ($Offline) { $selectionArgs += '--offline' }
    if ($Action -eq 'Run') { $selectionArgs += @('--qemu', $QemuPath) }
    & $pythonLauncher @selectionArgs
    exit $LASTEXITCODE
}
if ([string]::IsNullOrWhiteSpace($ImageDirectory)) {
    $profileDirectory = if ($Agent) { 'local-model' } else { 'local-basic' }
    $ImageDirectory = Join-Path $env:LOCALAPPDATA ('AIOS\operating-images\' + $profileDirectory)
}
$imageRoot = [IO.Path]::GetFullPath($ImageDirectory)
if (-not (Test-Path -LiteralPath $QemuPath -PathType Leaf)) { throw "QEMU executable missing: $QemuPath" }
$runnerArgs = @('-3', $imageRunner, $Action.ToLowerInvariant(),
    '--qemu', $QemuPath, '--image-dir', $imageRoot)
if ($Agent) { $runnerArgs += '--agent' }
if ($Action -eq 'Build') {
    if ($Agent) {
        if ([string]::IsNullOrWhiteSpace($InferenceCache)) {
            $InferenceCache = Join-Path $env:LOCALAPPDATA 'AIOS\hosted-inference'
        }
        $runnerArgs += @('--inference-cache', $InferenceCache)
    }
    $vmCache = Join-Path $env:LOCALAPPDATA 'AIOS\hosted-bootstrap\alpine-3.24.1'
    $isoPath = Join-Path $vmCache 'alpine.iso'
    $expectedHash = 'e73a6241bd5f3c5c2d4d38c02cc52c378c0415a7c888bd292066bf36e0f41a39'
    New-Item -ItemType Directory -Path $vmCache -Force | Out-Null
    if (-not (Test-Path -LiteralPath $isoPath -PathType Leaf)) {
        Invoke-WebRequest -UseBasicParsing -Uri 'https://dl-cdn.alpinelinux.org/alpine/v3.24/releases/x86_64/alpine-virt-3.24.1-x86_64.iso' -OutFile $isoPath
    }
    $isoStream = [IO.File]::OpenRead($isoPath)
    $isoHasher = [Security.Cryptography.SHA256]::Create()
    try { $actualHash = [BitConverter]::ToString($isoHasher.ComputeHash($isoStream)).Replace('-', '').ToLowerInvariant() }
    finally { $isoHasher.Dispose(); $isoStream.Dispose() }
    if ($actualHash -ne $expectedHash) {
        throw 'Linux installer ISO checksum mismatch.'
    }
    $archiveTool = (Get-Command tar.exe -ErrorAction Stop).Source
    & $archiveTool -xf $isoPath -C $vmCache boot/vmlinuz-virt boot/initramfs-virt
    if ($LASTEXITCODE -ne 0) { throw 'Installer kernel and initramfs extraction failed.' }
    $runnerArgs += @('--iso', $isoPath, '--kernel', (Join-Path $vmCache 'boot\vmlinuz-virt'),
        '--initramfs', (Join-Path $vmCache 'boot\initramfs-virt'))
}
if ($Offline) { $runnerArgs += '--offline' }
if ($GuestTests) { $runnerArgs += '--guest-tests' }
& $pythonLauncher @runnerArgs
exit $LASTEXITCODE
