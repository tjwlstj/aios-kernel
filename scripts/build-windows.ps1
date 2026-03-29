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

function Invoke-NativeCommand {
    param(
        [string]$Command,
        [string[]]$ArgumentList = @()
    )

    & $Command @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        $joined = ($ArgumentList -join ' ')
        throw "Command failed with exit code ${LASTEXITCODE}: $Command $joined"
    }
}

function Test-CommandAvailable {
    param([string]$Name)

    try {
        Get-Command $Name -ErrorAction Stop | Out-Null
        return $true
    } catch {
        return $false
    }
}

function Invoke-MakeTarget {
    param([string]$MakeTarget)

    Invoke-NativeCommand $script:Make @(
        "CC=$script:CrossGcc",
        "LD=$script:CrossLd",
        "OBJCOPY=$script:CrossObjcopy",
        "ASM=$script:NasmCommand",
        $MakeTarget
    )
}

function Ensure-Directory {
    param([string]$Path)

    if (-not $Path) {
        throw 'Directory path must not be empty'
    }

    New-Item -ItemType Directory -Force -Path $Path | Out-Null
}

function Convert-ToMsysPath {
    param([string]$Path)

    $full = [System.IO.Path]::GetFullPath($Path)
    if ($full -match '^[A-Za-z]:\\') {
        $drive = $full.Substring(0, 1).ToLowerInvariant()
        $rest = $full.Substring(2).Replace('\', '/')
        return "/$drive$rest"
    }

    return $full.Replace('\', '/')
}

function New-WindowsBiosIso {
    $isoRoot = Join-Path $RepoRoot 'build\winiso'
    $bootDir = Join-Path $isoRoot 'boot'
    $grubDir = Join-Path $bootDir 'grub'
    $grubCfg = Join-Path $grubDir 'grub.cfg'
    $coreImg = Join-Path $isoRoot 'core.img'
    $biosImg = Join-Path $grubDir 'bios.img'
    $kernelIsoPath = Join-Path $bootDir 'kernel.bin'
    $kernelBin = Join-Path $RepoRoot 'build\aios-kernel.bin'
    $outputIso = Join-Path $RepoRoot 'build\aios-kernel.iso'

    if (-not (Test-Path $kernelBin)) {
        throw "Kernel binary not found: $kernelBin"
    }

    Remove-Item $isoRoot -Recurse -Force -ErrorAction SilentlyContinue
    Ensure-Directory $grubDir
    [System.IO.File]::Copy($kernelBin, $kernelIsoPath, $true)

    $grubCfgText = @(
        'serial --unit=0 --speed=115200'
        'terminal_input serial console'
        'terminal_output serial console'
        'set timeout=0'
        'set default=0'
        ''
        'menuentry "AIOS - AI-Native Operating System" {'
        '    multiboot2 /boot/kernel.bin'
        '    boot'
        '}'
    )
    [System.IO.File]::WriteAllLines($grubCfg, $grubCfgText)

    Invoke-NativeCommand $script:GrubMkImage @(
        '-O', 'i386-pc-eltorito',
        '-o', $coreImg,
        '-p', '/boot/grub',
        'biosdisk', 'iso9660', 'multiboot2', 'normal', 'configfile', 'search', 'echo', 'serial', 'terminal'
    )

    [System.IO.File]::Copy($coreImg, $biosImg, $true)

    Invoke-NativeCommand $script:Xorriso @(
        '-as', 'mkisofs',
        '-R',
        '-b', 'boot/grub/bios.img',
        '-no-emul-boot',
        '-boot-load-size', '4',
        '-boot-info-table',
        '-o', (Convert-ToMsysPath $outputIso),
        (Convert-ToMsysPath $isoRoot)
    )

    return $outputIso
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
Add-PathIfExists (Join-Path (Split-Path -Parent $RepoRoot) 'msys64\usr\bin')
Add-PathIfExists (Join-Path (Split-Path -Parent $RepoRoot) 'msys64\ucrt64\bin')
Add-PathIfExists (Join-Path (Split-Path -Parent $RepoRoot) 'tools\grub-2.12-for-windows')

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

$NeedsIsoBoot = $Target -in @('iso', 'test', 'run', 'run-headless', 'debug')

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
$Xorriso = $null
$GrubMkImage = $null

if ($NeedsIsoBoot) {
    $Xorriso = Find-Tool 'xorriso' @(
        (Join-Path (Split-Path -Parent $RepoRoot) 'msys64\usr\bin\xorriso.exe')
    )

    $grubRootCandidates = @(
        (Join-Path (Split-Path -Parent $RepoRoot) 'tools\grub-2.12-for-windows'),
        (Join-Path $RepoRoot '.toolchain\grub-2.12-for-windows')
    )
    $grubRoot = $null
    foreach ($candidate in $grubRootCandidates) {
        if (Test-Path $candidate) {
            $grubRoot = $candidate
            break
        }
    }

    if (-not $grubRoot) {
        throw "Windows GRUB tools not found. Expected grub-2.12-for-windows under ..\\tools or .toolchain."
    }

    $GrubMkImage = Join-Path $grubRoot 'grub-mkimage.exe'
    foreach ($tool in @($GrubMkImage)) {
        if (-not (Test-Path $tool)) {
            throw "Missing Windows GRUB asset: $tool"
        }
    }
}

Write-Host "[INFO] Repo root: $RepoRoot"
Write-Host "[INFO] Toolchain root: $ToolchainRoot"
Write-Host "[INFO] make: $Make"
Write-Host "[INFO] nasm: $Nasm"
if ($Qemu) {
    Write-Host "[INFO] qemu: $Qemu"
}
if ($Xorriso) {
    Write-Host "[INFO] xorriso: $Xorriso"
}
if ($GrubMkImage) {
    Write-Host "[INFO] grub-mkimage: $GrubMkImage"
}

Push-Location $RepoRoot
try {
    switch ($Target) {
        'all' {
            Invoke-MakeTarget 'all'
        }
        'clean' {
            Invoke-MakeTarget 'clean'
        }
        'info' {
            Invoke-MakeTarget 'info'
        }
        'iso' {
            Invoke-MakeTarget 'all'
            $iso = New-WindowsBiosIso
            Write-Host "[OK] Bootable ISO: $iso"
        }
        'test' {
            Invoke-MakeTarget 'all'
            $iso = New-WindowsBiosIso
            $serialLog = Join-Path $RepoRoot 'build\serial_output.log'
            Remove-Item $serialLog -Force -ErrorAction SilentlyContinue
            $proc = Start-Process -FilePath $Qemu -ArgumentList @(
                '-cdrom', $iso,
                '-boot', 'd',
                '-m', '256M',
                '-nic', 'user,model=e1000',
                '-device', 'qemu-xhci',
                '-serial', "file:$serialLog",
                '-display', 'none',
                '-no-reboot',
                '-no-shutdown'
            ) -PassThru
            if (-not $proc.WaitForExit(20000)) {
                Stop-Process -Id $proc.Id -Force
            }
            if (-not (Test-Path $serialLog)) {
                throw 'Smoke test did not produce a serial log'
            }
            if ((Get-Item $serialLog).Length -eq 0) {
                throw 'Smoke test produced an empty serial log'
            }
            $hasReady = Select-String -Path $serialLog -Pattern 'AIOS Kernel Ready' -Quiet -ErrorAction SilentlyContinue
            $hasSelftest = Select-String -Path $serialLog -Pattern '\[SELFTEST\] Memory microbench PASS' -Quiet -ErrorAction SilentlyContinue
            $hasProbe = Select-String -Path $serialLog -Pattern '\[DEV\] Peripheral probe ready' -Quiet -ErrorAction SilentlyContinue
            $hasHealth = Select-String -Path $serialLog -Pattern '\[HEALTH\] stability=' -Quiet -ErrorAction SilentlyContinue
            if ($hasReady -and $hasSelftest -and $hasProbe -and $hasHealth) {
                Write-Host '[OK] Smoke test PASSED - kernel booted successfully'
            } else {
                Write-Host '[ERR] Smoke test did not reach expected ready/selftest/probe state'
                Get-Content $serialLog -Tail 40 -ErrorAction SilentlyContinue
                throw 'Smoke test failed'
            }
        }
        'run' {
            Invoke-MakeTarget 'all'
            $iso = New-WindowsBiosIso
            & $Qemu -cdrom $iso -boot d -m 2G -nic user,model=e1000 -device qemu-xhci -serial stdio -display curses -no-reboot -no-shutdown
        }
        'run-headless' {
            Invoke-MakeTarget 'all'
            $iso = New-WindowsBiosIso
            & $Qemu -cdrom $iso -boot d -m 2G -nic user,model=e1000 -device qemu-xhci -serial stdio -display none -no-reboot -no-shutdown
        }
        'debug' {
            Invoke-MakeTarget 'all'
            $iso = New-WindowsBiosIso
            & $Qemu -cdrom $iso -boot d -m 2G -nic user,model=e1000 -device qemu-xhci -serial stdio -display curses -no-reboot -no-shutdown -s -S
        }
        default {
            throw "Unsupported target: $Target"
        }
    }
} finally {
    Pop-Location
}
