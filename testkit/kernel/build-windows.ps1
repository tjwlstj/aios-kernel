param(
    [ValidateSet('all', 'test', 'run', 'run-headless', 'debug', 'clean', 'info', 'iso')]
    [string]$Target = 'all',
    [ValidateSet('full', 'minimal', 'storage-only')]
    [string]$SmokeProfile = 'full',
    [ValidateRange(1, 600)]
    [int]$TestTimeoutSec = 60,
    [switch]$SkipLock
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

function Get-QemuBootArguments {
    param(
        [string]$IsoPath,
        [string]$SerialMode,
        [string]$DisplayMode,
        [string]$Memory = '256M',
        [switch]$DebugWait
    )

    $args = @(
        '-cdrom', $IsoPath,
        '-boot', 'd',
        '-m', $Memory
    )

    if ($script:SmokeProfile -in @('minimal', 'storage-only')) {
        $args += @('-nic', 'none')
    } else {
        $args += @('-nic', 'user,model=e1000', '-device', 'qemu-xhci')
    }

    $args += @(
        '-serial', $SerialMode,
        '-display', $DisplayMode,
        '-no-reboot',
        '-no-shutdown'
    )

    if ($DebugWait) {
        $args += @('-s', '-S')
    }

    return $args
}

function Get-SmokeRequiredPatterns {
    $patterns = @(
        'AIOS Kernel Ready',
        '\[SELFTEST\] Memory microbench PASS',
        '\[DEV\] Peripheral probe ready',
        '\[USER\] Ring3 scaffold ready=1',
        '\[ROOM\] snapshot stability=',
        '\[HEALTH\] stability=',
        '\[NODEBIT\] Policy gate ready entries=0',
        '\[SHELL\] Interactive shell started'
    )

    if ($script:SmokeProfile -eq 'storage-only') {
        $patterns += @(
            '\[NET\] No Intel E1000-compatible controller found',
            '\[USB\] No USB host controller found',
            '\[STO\] IDE ready=1',
            '\[STO\] IDE channels',
            'label=storage-bootstrap'
        )
    } elseif ($script:SmokeProfile -eq 'minimal') {
        $patterns += @(
            '\[NET\] No Intel E1000-compatible controller found',
            '\[USB\] No USB host controller found'
        )
    } else {
        $patterns += @(
            '\[NET\] E1000 ready',
            '\[USB\] XHCI ready=1'
        )
    }

    return $patterns
}

function Enter-TestkitLock {
    Ensure-Directory $script:BuildDir
    try {
        New-Item -ItemType Directory -Path $script:LockDir -ErrorAction Stop | Out-Null
    } catch {
        $details = ''
        $ownerFile = Join-Path $script:LockDir 'owner.json'
        if (Test-Path $ownerFile) {
            try {
                $owner = Get-Content $ownerFile -Raw | ConvertFrom-Json
                $details = " (label=$($owner.label), pid=$($owner.pid), host=$($owner.host))"
            } catch {
            }
        }
        throw "Another AIOS testkit run is already active. Wait for it to finish or remove $($script:LockDir) if the previous run crashed.$details"
    }

    $ownerPayload = @{
        label        = "windows-kernel:${Target}:${SmokeProfile}"
        pid          = $PID
        host         = $env:COMPUTERNAME
        cwd          = $script:RepoRoot
        created_unix = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    } | ConvertTo-Json
    [System.IO.File]::WriteAllText((Join-Path $script:LockDir 'owner.json'), $ownerPayload)
    $script:LockHeld = $true
}

function Exit-TestkitLock {
    if (-not $script:LockHeld) {
        return
    }

    $ownerFile = Join-Path $script:LockDir 'owner.json'
    Remove-Item $ownerFile -Force -ErrorAction SilentlyContinue
    Remove-Item $script:LockDir -Force -ErrorAction SilentlyContinue
    $script:LockHeld = $false
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

$RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$BuildDir = Join-Path $RepoRoot 'build'
$LockDir = Join-Path $BuildDir '.testkit-lock'
$LockHeld = $false
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
        throw "Windows GRUB tools not found. Expected grub-2.12-for-windows under ..\tools or .toolchain."
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
Write-Host "[INFO] smoke profile: $SmokeProfile"
Write-Host "[INFO] test timeout: ${TestTimeoutSec}s"
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
if (-not $SkipLock) {
    Enter-TestkitLock
}
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
            $proc = Start-Process -FilePath $Qemu -ArgumentList (Get-QemuBootArguments -IsoPath $iso -SerialMode "file:$serialLog" -DisplayMode 'none' -Memory '256M') -PassThru
            if (-not $proc.WaitForExit($TestTimeoutSec * 1000)) {
                Stop-Process -Id $proc.Id -Force
            }
            if (-not (Test-Path $serialLog)) {
                throw 'Smoke test did not produce a serial log'
            }
            if ((Get-Item $serialLog).Length -eq 0) {
                throw 'Smoke test produced an empty serial log'
            }
            $missingPatterns = @()
            foreach ($pattern in (Get-SmokeRequiredPatterns)) {
                $matched = Select-String -Path $serialLog -Pattern $pattern -Quiet -ErrorAction SilentlyContinue
                if (-not $matched) {
                    $missingPatterns += $pattern
                }
            }
            if ($missingPatterns.Count -eq 0) {
                Write-Host '[OK] Smoke test PASSED - kernel booted successfully'
            } else {
                Write-Host "[ERR] Smoke test did not reach expected state. Missing=$($missingPatterns -join ', ')"
                Get-Content $serialLog -Tail 40 -ErrorAction SilentlyContinue
                throw 'Smoke test failed'
            }
        }
        'run' {
            Invoke-MakeTarget 'all'
            $iso = New-WindowsBiosIso
            & $Qemu @(Get-QemuBootArguments -IsoPath $iso -SerialMode 'stdio' -DisplayMode 'curses' -Memory '2G')
        }
        'run-headless' {
            Invoke-MakeTarget 'all'
            $iso = New-WindowsBiosIso
            & $Qemu @(Get-QemuBootArguments -IsoPath $iso -SerialMode 'stdio' -DisplayMode 'none' -Memory '2G')
        }
        'debug' {
            Invoke-MakeTarget 'all'
            $iso = New-WindowsBiosIso
            & $Qemu @(Get-QemuBootArguments -IsoPath $iso -SerialMode 'stdio' -DisplayMode 'curses' -Memory '2G' -DebugWait)
        }
        default {
            throw "Unsupported target: $Target"
        }
    }
} finally {
    if (-not $SkipLock) {
        Exit-TestkitLock
    }
    Pop-Location
}
