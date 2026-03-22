param(
    [ValidateSet('all', 'test', 'run', 'run-headless', 'debug', 'clean', 'info', 'iso')]
    [string]$Target = 'all'
)

$ErrorActionPreference = 'Stop'

function Add-PathIfExists {
    param([string]$PathEntry)

    if (-not $PathEntry) {
        return
    }

    if (Test-Path $PathEntry) {
        $script:ResolvedPaths += $PathEntry
    }
}

function Find-Tool {
    param(
        [string]$Name,
        [string[]]$Candidates
    )

    try {
        $cmd = Get-Command $Name -ErrorAction Stop
        return $cmd.Source
    } catch {
    }

    foreach ($candidate in $Candidates) {
        if ($candidate -and (Test-Path $candidate)) {
            return $candidate
        }
    }

    throw "Required tool not found: $Name"
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
$ResolvedPaths = @()

$toolchainRootCandidates = @(
    $env:AIOS_X86_64_ELF_ROOT,
    (Join-Path (Split-Path -Parent $RepoRoot) 'tools\x86_64-elf'),
    (Join-Path $RepoRoot '.toolchain\x86_64-elf'),
    (Join-Path (Get-Location) 'x86_64-elf')
)

$ToolchainRoot = $null
foreach ($candidate in $toolchainRootCandidates) {
    if ($candidate -and (Test-Path $candidate)) {
        $ToolchainRoot = $candidate
        break
    }
}

if (-not $ToolchainRoot) {
    throw "x86_64-elf toolchain not found. Set AIOS_X86_64_ELF_ROOT or place it under ..\tools\x86_64-elf."
}

Add-PathIfExists (Join-Path $ToolchainRoot 'bin')
Add-PathIfExists "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\ezwinports.make_Microsoft.Winget.Source_8wekyb3d8bbwe\bin"
Add-PathIfExists "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\mingw64\bin"
Add-PathIfExists 'C:\Program Files\Git\usr\bin'
Add-PathIfExists 'C:\Program Files\Git\bin'
Add-PathIfExists 'C:\Program Files\qemu'
Add-PathIfExists 'C:\Program Files\QEMU'

if ($ResolvedPaths.Count -gt 0) {
    $env:Path = ($ResolvedPaths -join ';') + ';' + $env:Path
}

$Make = Find-Tool 'make' @(
    "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\ezwinports.make_Microsoft.Winget.Source_8wekyb3d8bbwe\bin\make.exe"
)
$Nasm = Find-Tool 'nasm' @(
    "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\BrechtSanders.WinLibs.POSIX.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\mingw64\bin\nasm.exe"
)
$Qemu = $null
try {
    $Qemu = Find-Tool 'qemu-system-x86_64' @(
        'C:\Program Files\qemu\qemu-system-x86_64.exe',
        'C:\Program Files\QEMU\qemu-system-x86_64.exe'
    )
} catch {
    if ($Target -in @('test', 'run', 'run-headless', 'debug')) {
        throw
    }
}

$CrossPrefix = Join-Path (Join-Path $ToolchainRoot 'bin') 'x86_64-elf-'
$CrossGccPath = "${CrossPrefix}gcc.exe"
$CrossLdPath = "${CrossPrefix}ld.exe"
$CrossObjcopyPath = "${CrossPrefix}objcopy.exe"

foreach ($tool in @($CrossGccPath, $CrossLdPath, $CrossObjcopyPath)) {
    if (-not (Test-Path $tool)) {
        throw "Missing cross tool: $tool"
    }
}

$CrossGcc = 'x86_64-elf-gcc'
$CrossLd = 'x86_64-elf-ld'
$CrossObjcopy = 'x86_64-elf-objcopy'
$NasmCommand = 'nasm'

Write-Host "[INFO] Repo root: $RepoRoot"
Write-Host "[INFO] Toolchain root: $ToolchainRoot"
Write-Host "[INFO] make: $Make"
Write-Host "[INFO] nasm: $Nasm"
if ($Qemu) {
    Write-Host "[INFO] qemu: $Qemu"
}

Push-Location $RepoRoot
try {
    & $Make `
        "CC=$CrossGcc" `
        "LD=$CrossLd" `
        "OBJCOPY=$CrossObjcopy" `
        "ASM=$NasmCommand" `
        $Target
} finally {
    Pop-Location
}
