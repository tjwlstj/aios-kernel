[CmdletBinding()]
param(
    [string]$QemuPath = 'C:\Program Files\qemu\qemu-system-x86_64.exe',
    [string]$ArtifactDirectory = ''
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
New-Item -ItemType Directory -Path $vmCache -Force | Out-Null
if (-not (Test-Path -LiteralPath $isoPath -PathType Leaf)) {
    Write-Host '[AIOS demo] Downloading the isolated Linux development host (not an AIOS distribution).'
    Invoke-WebRequest -UseBasicParsing -Uri $isoUrl -OutFile $isoPath
}
$actualHash = (Get-FileHash -LiteralPath $isoPath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actualHash -ne $expectedHash) { throw 'Linux development image checksum mismatch. No VM was started.' }
& $archiveTool -xf $isoPath -C $vmCache boot/vmlinuz-virt boot/initramfs-virt
if ($LASTEXITCODE -ne 0) { throw 'Could not extract the development kernel and initramfs.' }
if ([string]::IsNullOrWhiteSpace($ArtifactDirectory)) {
    $runName = 'qemu-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0, 6)
    $ArtifactDirectory = Join-Path $repoRoot ('build\hosted-boot\' + $runName)
}
$demoArgs = @(
    '-3', (Join-Path $PSScriptRoot 'qemu_boot_demo.py'),
    '--qemu', $QemuPath, '--iso', $isoPath, '--sha256', $expectedHash,
    '--kernel', (Join-Path $vmCache 'boot\vmlinuz-virt'),
    '--initramfs', (Join-Path $vmCache 'boot\initramfs-virt'),
    '--artifact-dir', $ArtifactDirectory
)
& $pythonLauncher @demoArgs
$demoExitCode = $LASTEXITCODE
Write-Host ('[AIOS demo] Evidence: ' + $ArtifactDirectory)
exit $demoExitCode
