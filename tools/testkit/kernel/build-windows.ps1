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
        '\[BOOT\] Multiboot2 handoff PASS',
        '\[SELFTEST\] Memory microbench PASS',
        '\[HEAP\] lock selftest PASS',
        '\[SCHED\] context switch selftest PASS',
        '\[SCHED\] preempt selftest PASS',
        '\[MM\] address space selftest PASS',
        '\[MM\] user leaf isolation selftest PASS',
        '\[MM\] bootstrap user tensor exclusion PASS base=0x4000000 size=2097152 excluded=2097152 managed=1004535808 configured=1006632960 overflow=1 region=1 align=1 boundary=1 coalesce=1',
        '\[PROC\] bootstrap ownership selftest PASS slots=2 owned=2 stack_bytes=16384 unique_cr3=1 unique_backing=1 unique_stack=1',
        '\[TIMER\] PIT IRQ ready',
        '\[DEV\] Peripheral probe ready',
        '\[USER\] Ring3 scaffold ready=1',
        '\[ROOM\] snapshot stability=',
        '\[HEALTH\] stability=',
        '\[NODEBIT\] Policy gate ready entries=0',
        '\[PIPE\] Node pipeline ready',
        '\[PIPE\] selftest PASS',
        '\[SLM\] plan apply selftest PASS',
        '\[SYSCALL\] observe dispatch selftest PASS',
        '\[USER\] ring3 exec PASS',
        '\[USER\] private address space exec PASS slot=0 cr3_restored=1 if_restored=1 leaf_sealed=1 nx_enforced=1 tensor_excluded=1',
        '\[USER\] bootstrap process stack PASS pid=1 slot=0 process_bound=1 kstack_bytes=16384 rsp0_changed=1 rsp0_published=1 int80_entries=3 all_int80_entries_in_stack=1 rsp0_restored=1 kstack_floor_canary=1',
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

function Test-NormalSmokeVerdict {
    param([string]$SerialLog)

    function Get-PatternOccurrences {
        param(
            [string]$Pattern,
            [switch]$ValuePrefix,
            [switch]$AllowInline
        )

        $suffix = if ($ValuePrefix) { '\S+' } else { '(?=$|\s)' }
        $boundedRegex = [regex]::new("(?<!\S)(?:$Pattern)$suffix")
        $anchorRegex = [regex]::new("^(?:$Pattern)$suffix")
        $allOccurrences = @()
        $anchorLines = @()
        for ($lineIndex = 0; $lineIndex -lt $lines.Count; $lineIndex++) {
            $line = [string]$lines[$lineIndex]
            if ($anchorRegex.IsMatch($line)) {
                $anchorLines += ($lineIndex + 1)
            }
            foreach ($match in $boundedRegex.Matches($line)) {
                $allOccurrences += [pscustomobject]@{
                    LineNumber = $lineIndex + 1
                    Column     = $match.Index + 1
                    Text       = $line
                }
            }
        }
        if ($AllowInline) {
            return $allOccurrences
        }
        if ($anchorLines.Count -eq 0) {
            return @()
        }
        $firstAnchorLine = $anchorLines[0]
        return @($allOccurrences | Where-Object { $_.LineNumber -ge $firstAnchorLine })
    }

    $lines = @(Get-Content -Path $SerialLog -ErrorAction SilentlyContinue)
    $reasons = @()
    $missingPatterns = @()
    $requiredOccurrences = @{}
    $duplicateFieldLines = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($pattern in (Get-SmokeRequiredPatterns)) {
        $occurrenceArgs = @{ Pattern = $pattern }
        if ($pattern.EndsWith('=')) {
            $occurrenceArgs.ValuePrefix = $true
        }
        if ($pattern -eq 'AIOS Kernel Ready' -or $pattern -eq 'label=storage-bootstrap') {
            $occurrenceArgs.AllowInline = $true
        }
        $matches = @(Get-PatternOccurrences @occurrenceArgs)
        if ($pattern -eq 'AIOS Kernel Ready') {
            $matches = @($matches | Where-Object { $_.Text.StartsWith('=== ') })
        } elseif ($pattern -eq 'label=storage-bootstrap') {
            $matches = @($matches | Where-Object { $_.Text.StartsWith('[SLM] Seeded plan ') })
        }
        $requiredOccurrences[$pattern] = $matches
        if ($matches.Count -eq 0) {
            $missingPatterns += $pattern
        }
        foreach ($match in $matches) {
            $null = $duplicateFieldLines.Add([int]$match.LineNumber)
        }
    }
    if ($missingPatterns.Count -gt 0) {
        $reasons += "missing-required-patterns:$($missingPatterns -join ',')"
    }

    $fatalPattern = '\*\*\* KERNEL PANIC \*\*\*|!!! EXCEPTION|\bFAIL\b|\bFATAL\b'
    $fatalEvents = @()
    for ($lineIndex = 0; $lineIndex -lt $lines.Count; $lineIndex++) {
        if ([regex]::IsMatch([string]$lines[$lineIndex], $fatalPattern)) {
            $fatalEvents += [pscustomobject]@{
                LineNumber = $lineIndex + 1
                Text       = [string]$lines[$lineIndex]
            }
        }
    }
    if ($fatalEvents.Count -gt 0) {
        $firstFatal = $fatalEvents[0]
        $reasons += "fatal-event:line=$($firstFatal.LineNumber):$($firstFatal.Text.Trim())"
    }

    $terminalCheckpoints = @(
        [pscustomobject]@{ Name = 'ring3-scaffold'; Pattern = '\[USER\] Ring3 scaffold ready=1'; ValuePrefix = $false },
        [pscustomobject]@{ Name = 'bootstrap-process'; Pattern = '\[PROC\] bootstrap ownership selftest PASS'; ValuePrefix = $false },
        [pscustomobject]@{ Name = 'ring3-exec'; Pattern = '\[USER\] ring3 exec PASS'; ValuePrefix = $false },
        [pscustomobject]@{ Name = 'private-address-space-exec'; Pattern = '\[USER\] private address space exec PASS'; ValuePrefix = $false },
        [pscustomobject]@{ Name = 'bootstrap-process-stack'; Pattern = '\[USER\] bootstrap process stack PASS'; ValuePrefix = $false },
        [pscustomobject]@{ Name = 'kernel-room'; Pattern = '\[ROOM\] snapshot stability=stable'; ValuePrefix = $false },
        [pscustomobject]@{ Name = 'health'; Pattern = '\[HEALTH\] stability='; ValuePrefix = $true },
        [pscustomobject]@{ Name = 'kernel-ready'; Pattern = '=== AIOS Kernel Ready ==='; ValuePrefix = $false },
        [pscustomobject]@{ Name = 'boot-complete'; Pattern = '\[KERNEL\] Boot complete\. Launching interactive shell\.\.\.'; ValuePrefix = $false },
        [pscustomobject]@{ Name = 'shell-started'; Pattern = '\[SHELL\] Interactive shell started'; ValuePrefix = $false }
    )
    $terminalOccurrences = @{}
    $missingCheckpoints = @()
    $duplicateCheckpoints = @()
    $orderViolations = @()
    $previousName = $null
    $previousLine = 0
    foreach ($checkpoint in $terminalCheckpoints) {
        $checkpointArgs = @{ Pattern = $checkpoint.Pattern }
        if ($checkpoint.ValuePrefix) {
            $checkpointArgs.ValuePrefix = $true
        }
        $matches = @(Get-PatternOccurrences @checkpointArgs)
        $terminalOccurrences[$checkpoint.Name] = $matches
        foreach ($match in $matches) {
            $null = $duplicateFieldLines.Add([int]$match.LineNumber)
        }
        if ($matches.Count -eq 0) {
            $missingCheckpoints += $checkpoint.Name
            $reasons += "terminal-missing:$($checkpoint.Name)"
            continue
        }
        if ($matches.Count -gt 1) {
            $duplicateLinesText = ($matches | ForEach-Object { $_.LineNumber }) -join ','
            $duplicateCheckpoints += [pscustomobject]@{
                Checkpoint = $checkpoint.Name
                Lines      = @($matches | ForEach-Object { $_.LineNumber })
            }
            $reasons += "terminal-duplicate:$($checkpoint.Name):lines=$duplicateLinesText"
        }

        $lineNumber = $matches[0].LineNumber
        if ($previousLine -gt 0 -and $lineNumber -le $previousLine) {
            $orderViolations += [pscustomobject]@{
                Checkpoint        = $checkpoint.Name
                Line              = $lineNumber
                ExpectedAfter     = $previousName
                ExpectedAfterLine = $previousLine
            }
            $reasons += "terminal-order:$($checkpoint.Name):line=${lineNumber}:expected-after=${previousName}:$previousLine"
        }
        $previousName = $checkpoint.Name
        $previousLine = $lineNumber
    }

    $healthMatches = @($terminalOccurrences['health'])
    $health = [ordered]@{
        parsed       = $false
        line         = $null
        text         = $null
        stability    = $null
        degraded     = $null
        failed       = $null
        field_counts = [ordered]@{ stability = 0; degraded = 0; failed = 0 }
        passed       = $false
    }
    if ($healthMatches.Count -eq 1) {
        $healthLine = [string]$healthMatches[0].Text
        $health.line = $healthMatches[0].LineNumber
        $health.text = $healthLine
        $fieldValues = @{}
        foreach ($fieldMatch in [regex]::Matches($healthLine, '(?<!\S)(?<key>[a-z_]+)=(?<value>\S+)')) {
            $key = $fieldMatch.Groups['key'].Value
            $value = $fieldMatch.Groups['value'].Value
            if (-not $fieldValues.ContainsKey($key)) {
                $fieldValues[$key] = @()
            }
            $fieldValues[$key] = @($fieldValues[$key]) + $value
        }
        foreach ($fieldName in @('stability', 'degraded', 'failed')) {
            $health.field_counts[$fieldName] = @($fieldValues[$fieldName]).Count
        }
        if ($health.field_counts.stability -eq 1) {
            $health.stability = @($fieldValues['stability'])[0]
        }
        $degradedText = if ($health.field_counts.degraded -eq 1) { @($fieldValues['degraded'])[0] } else { $null }
        $failedText = if ($health.field_counts.failed -eq 1) { @($fieldValues['failed'])[0] } else { $null }
        $degradedNumber = 0L
        $failedNumber = 0L
        $degradedParsed = ($null -ne $degradedText -and $degradedText -match '^[0-9]+$' -and [long]::TryParse($degradedText, [ref]$degradedNumber))
        $failedParsed = ($null -ne $failedText -and $failedText -match '^[0-9]+$' -and [long]::TryParse($failedText, [ref]$failedNumber))
        if ($degradedParsed) { $health.degraded = $degradedNumber }
        if ($failedParsed) { $health.failed = $failedNumber }
        $health.parsed = (
            $health.field_counts.stability -eq 1 -and
            $health.field_counts.degraded -eq 1 -and
            $health.field_counts.failed -eq 1 -and
            $degradedParsed -and
            $failedParsed
        )
        $health.passed = (
            $health.parsed -and
            $health.stability -ceq 'stable' -and
            $degradedText -ceq '0' -and
            $failedText -ceq '0'
        )
    }
    if (-not $health.passed) {
        $reasons += "health-invalid:matches=$($healthMatches.Count),stability=$($health.stability),degraded=$($health.degraded),failed=$($health.failed)"
    }

    $duplicateEvidenceFields = @()
    $invalidEvidenceRecords = @()
    foreach ($lineNumber in @($duplicateFieldLines | Sort-Object)) {
        $lineText = [string]$lines[$lineNumber - 1]
        $fieldValues = @{}
        foreach ($fieldMatch in [regex]::Matches($lineText, '(?<!\S)(?<key>[A-Za-z_][A-Za-z0-9_]*)=(?<value>\S+)')) {
            $key = $fieldMatch.Groups['key'].Value
            $value = $fieldMatch.Groups['value'].Value
            if (-not $fieldValues.ContainsKey($key)) {
                $fieldValues[$key] = @()
            }
            $fieldValues[$key] = @($fieldValues[$key]) + $value
        }
        if ($lineText.StartsWith('[STO] IDE channels')) {
            $ideRecordPattern = '^\[STO\] IDE channels ' +
                'primary=(?<primaryCommand>0x[0-9A-Fa-f]+)/(?<primaryControl>0x[0-9A-Fa-f]+) ' +
                'status=(?<primaryStatus>0x[0-9A-Fa-f]+) live=(?<primaryLive>[01]) ' +
                'secondary=(?<secondaryCommand>0x[0-9A-Fa-f]+)/(?<secondaryControl>0x[0-9A-Fa-f]+) ' +
                'status=(?<secondaryStatus>0x[0-9A-Fa-f]+) live=(?<secondaryLive>[01])$'
            # Match Python's serial sanitizer: trailing transport whitespace is
            # ignored, while leading whitespace remains contract-significant.
            $ideText = $lineText.TrimEnd()
            $ideMatch = [regex]::Match($ideText, $ideRecordPattern)
            $ideRecordValid = $ideMatch.Success
            if ($ideRecordValid) {
                $endpointNames = @('primaryCommand', 'primaryControl', 'secondaryCommand', 'secondaryControl')
                $canonicalEndpoints = @()
                foreach ($endpointName in $endpointNames) {
                    $digits = $ideMatch.Groups[$endpointName].Value.Substring(2).TrimStart('0').ToLowerInvariant()
                    if ($digits.Length -eq 0) { $digits = '0' }
                    $canonicalEndpoints += $digits
                }
                $statusNames = @('primaryStatus', 'secondaryStatus')
                $canonicalStatuses = @()
                foreach ($statusName in $statusNames) {
                    $digits = $ideMatch.Groups[$statusName].Value.Substring(2).TrimStart('0').ToLowerInvariant()
                    if ($digits.Length -eq 0) { $digits = '0' }
                    $canonicalStatuses += $digits
                }
                $ideRecordValid = (
                    @($canonicalEndpoints | Select-Object -Unique).Count -eq 4 -and
                    @($canonicalEndpoints | Where-Object { $_ -eq '0' -or $_.Length -gt 4 }).Count -eq 0 -and
                    @($canonicalStatuses | Where-Object { $_.Length -gt 2 }).Count -eq 0
                )
            }
            if (-not $ideRecordValid) {
                $invalidEvidenceRecords += [pscustomobject]@{
                    Line   = $lineNumber
                    Text   = $lineText
                    Record = 'ide_channels'
                }
            }
        }
        $duplicatedFields = [ordered]@{}
        foreach ($key in $fieldValues.Keys) {
            if (@($fieldValues[$key]).Count -gt 1) {
                if ($lineText.StartsWith('[STO] IDE channels') -and $key -in @('status', 'live')) {
                    continue
                }
                $duplicatedFields[$key] = @($fieldValues[$key])
            }
        }
        if ($duplicatedFields.Count -gt 0) {
            $duplicateEvidenceFields += [pscustomobject]@{
                Line   = $lineNumber
                Text   = $lineText
                Fields = $duplicatedFields
            }
        }
    }
    if ($duplicateEvidenceFields.Count -gt 0) {
        $reasons += "evidence-fields-duplicated:line=$($duplicateEvidenceFields[0].Line)"
    }
    if ($invalidEvidenceRecords.Count -gt 0) {
        $reasons += "evidence-record-invalid:line=$($invalidEvidenceRecords[0].Line):ide_channels"
    }

    $firstFailure = $null
    $lineFailures = @()
    foreach ($fatalEvent in $fatalEvents) {
        $lineFailures += [pscustomobject]@{ Kind = 'FATAL_EVENT'; Line = $fatalEvent.LineNumber; Text = $fatalEvent.Text }
    }
    if (-not $health.passed -and $null -ne $health.line) {
        $lineFailures += [pscustomobject]@{ Kind = 'HEALTH_INVALID'; Line = $health.line; Text = $health.text }
    }
    foreach ($event in $duplicateEvidenceFields) {
        $lineFailures += [pscustomobject]@{ Kind = 'EVIDENCE_FIELDS_DUPLICATED'; Line = $event.Line; Text = $event.Text }
    }
    foreach ($event in $invalidEvidenceRecords) {
        $lineFailures += [pscustomobject]@{ Kind = 'EVIDENCE_RECORD_INVALID'; Line = $event.Line; Text = $event.Text }
    }
    foreach ($duplicate in $duplicateCheckpoints) {
        if ($duplicate.Lines.Count -gt 1) {
            $lineFailures += [pscustomobject]@{ Kind = 'TERMINAL_CHECKPOINT_DUPLICATED'; Line = $duplicate.Lines[1]; Checkpoint = $duplicate.Checkpoint }
        }
    }
    foreach ($violation in $orderViolations) {
        $lineFailures += [pscustomobject]@{ Kind = 'TERMINAL_CHECKPOINT_ORDER_INVALID'; Line = $violation.Line; Checkpoint = $violation.Checkpoint }
    }
    if ($lineFailures.Count -gt 0) {
        $firstFailure = $lineFailures | Sort-Object Line | Select-Object -First 1
    } elseif ($missingPatterns.Count -gt 0) {
        $firstFailure = [pscustomobject]@{ Kind = 'MISSING_REQUIRED_PATTERN'; Line = $null; Pattern = $missingPatterns[0] }
    } elseif ($missingCheckpoints.Count -gt 0) {
        $firstFailure = [pscustomobject]@{ Kind = 'TERMINAL_CHECKPOINT_MISSING'; Line = $null; Checkpoint = $missingCheckpoints[0] }
    }

    $passed = ($reasons.Count -eq 0)
    return [pscustomobject]@{
        schema_version            = 1
        outcome                   = if ($passed) { 'PASS' } else { 'FAIL' }
        passed                    = $passed
        reasons                   = $reasons
        first_failure             = $firstFailure
        missing_patterns          = $missingPatterns
        fatal_events              = $fatalEvents
        duplicate_evidence_fields = $duplicateEvidenceFields
        invalid_evidence_records  = $invalidEvidenceRecords
        health                    = [pscustomobject]$health
        checkpoints               = [pscustomobject]@{
            expected_order   = @($terminalCheckpoints | ForEach-Object { $_.Name })
            occurrences      = $terminalOccurrences
            missing          = $missingCheckpoints
            duplicates       = $duplicateCheckpoints
            order_violations = $orderViolations
            passed           = ($missingCheckpoints.Count -eq 0 -and $duplicateCheckpoints.Count -eq 0 -and $orderViolations.Count -eq 0)
        }
        termination               = [pscustomobject]@{
            reason      = 'not-evaluated'
            exit_code   = $null
            timed_out   = $null
            duration_ms = $null
        }
    }
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
    $isoRoot = Join-Path $BuildDir 'winiso'
    $bootDir = Join-Path $isoRoot 'boot'
    $grubDir = Join-Path $bootDir 'grub'
    $grubCfg = Join-Path $grubDir 'grub.cfg'
    $coreImg = Join-Path $isoRoot 'core.img'
    $biosImg = Join-Path $grubDir 'bios.img'
    $kernelIsoPath = Join-Path $bootDir 'kernel.bin'
    $kernelBin = Join-Path $BuildDir 'aios-kernel.bin'
    $outputIso = Join-Path $BuildDir 'aios-kernel.iso'

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

# Script lives at <repo>/tools/testkit/kernel/, so the repo root is three levels up.
$RepoRoot = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $PSScriptRoot))
# Kernel build artifacts live under the kernel/ domain (kernel/build/).
$BuildDir = Join-Path $RepoRoot 'kernel\build'
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
            $serialLog = Join-Path $BuildDir 'serial_output.log'
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
            $verdict = Test-NormalSmokeVerdict -SerialLog $serialLog
            if ($verdict.Passed) {
                Write-Host '[OK] Smoke test PASSED - kernel booted successfully'
            } else {
                Write-Host "[ERR] Smoke verdict failed. Reasons=$($verdict.Reasons -join '; ')"
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
