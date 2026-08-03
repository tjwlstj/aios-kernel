$ErrorActionPreference = 'Stop'

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$buildScript = Join-Path $repoRoot 'tools/testkit/kernel/build-windows.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile(
    $buildScript,
    [ref]$tokens,
    [ref]$parseErrors
)
if ($parseErrors.Count -ne 0) {
    throw "build-windows.ps1 parse failed: $($parseErrors[0].Message)"
}

foreach ($functionName in @('Get-SmokeRequiredPatterns', 'Test-NormalSmokeVerdict')) {
    $functionAst = $ast.Find({
        param($node)
        $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and
            $node.Name -ceq $functionName
    }, $true)
    if ($null -eq $functionAst) {
        throw "Missing function: $functionName"
    }
    Invoke-Expression $functionAst.Extent.Text
}

$script:SmokeProfile = 'storage-only'
$validIde = '[STO] IDE channels primary=0x1f0/0x3f6 status=0x0 live=1 secondary=0x170/0x376 status=0x50 live=1'
$normalLines = @(
    '[BOOT] Multiboot2 handoff PASS'
    '[SELFTEST] Memory microbench PASS'
    '[HEAP] lock selftest PASS acquires=4'
    '[SCHED] context switch selftest PASS switches=8 ping=3 pong=3'
    '[SCHED] preempt selftest PASS ticks=2 switches=3'
    '[TRAP] frame contract selftest PASS size=176 canaries=15 int_no=3 err=0 cpl0=1 cs_match=1 ss_match=1 rip_exact=1 rsp_exact=1 frame_addr_exact=1 rflags_bit1=1 df_clear=1'
    '[MM] address space selftest PASS switches=2'
    '[MM] user leaf isolation selftest PASS slots=2'
    '[MM] bootstrap user tensor exclusion PASS base=0x4000000 size=2097152 excluded=2097152 managed=1004535808 configured=1006632960 overflow=1 region=1 align=1 boundary=1 coalesce=1'
    '[TIMER] PIT IRQ ready'
    '[DEV] Peripheral probe ready'
    '[NET] No Intel E1000-compatible controller found'
    '[USB] No USB host controller found'
    '[STO] IDE ready=1'
    $validIde
    '[NODEBIT] Policy gate ready entries=0'
    '[PIPE] Node pipeline ready'
    '[PIPE] selftest PASS'
    '[RESOURCE] ledger selftest PASS schema=1 kinds=5 units=2 entries=5 capacity=8 source_flags=31 limit_kinds=5 used_kinds=5 high_water_kinds=1 denied_kinds=0 owners_unattributed=1 observation_only=1'
    '[PRESSURE] tracker selftest PASS schema=1 planes=3 max_levels=4 active_levels=2 balanced=1 hotspot=1 overlap=1 gate_mask=1 observation_only=1'
    '[SLM] plan apply selftest PASS'
    '[SLM] Seeded plan 4 label=storage-bootstrap action=8'
    '[SYSCALL] observe dispatch selftest PASS'
    '[USER] Ring3 scaffold ready=1 tr=0x28'
    '[PROC] bootstrap ownership selftest PASS slots=2 owned=2 stack_bytes=16384 unique_cr3=1 unique_backing=1 unique_stack=1'
    '[USER] ring3 exec PASS exit_code=42'
    '[USER] private address space exec PASS slot=0 cr3_restored=1 if_restored=1 leaf_sealed=1 nx_enforced=1 tensor_excluded=1'
    '[USER] bootstrap process stack PASS pid=1 slot=0 process_bound=1 kstack_bytes=16384 rsp0_changed=1 rsp0_published=1 int80_entries=3 all_int80_entries_in_stack=1 rsp0_restored=1 kstack_floor_canary=1'
    '[USER] bootstrap process pair PASS runs=2 order=1,2 pid_a=1 slot_a=0 pid_b=2 slot_b=1 distinct_pid=1 distinct_slot=1 distinct_cr3=1 distinct_backing=1 distinct_stack=1 int80_a=3 int80_b=3 between_clean=1 current_pid=0 last_pid=2 rsp0_publishes=2 rsp0_restores=2 tss_rsp0_baseline=1 both_restored=1'
    '[TRAP] user frame capture PASS pid_a=1 pid_b=2 captures_a=1 captures_b=1 from_user=1 cs=0x23 ss=0x1b rsp_user=1 rip_user=1 canary_ok=1 frame_in_kstack=1 frame_addr_exact=1 contract=1'
    '[ROOM] snapshot stability=stable ok=18 degraded=0 failed=0'
    '[HEALTH] stability=stable ok=18 degraded=0 failed=0 unknown=2'
    '=== AIOS Kernel Ready ==='
    '[KERNEL] Boot complete. Launching interactive shell...'
    '[SHELL] Interactive shell started'
)

$cases = @(
    [pscustomobject]@{ Name = 'valid'; Expected = $true; Ide = $validIde }
    [pscustomobject]@{ Name = 'trailing-whitespace'; Expected = $true; Ide = "$validIde`t " }
    [pscustomobject]@{ Name = 'marker-only'; Expected = $false; Ide = '[STO] IDE channels' }
    [pscustomobject]@{ Name = 'truncated'; Expected = $false; Ide = '[STO] IDE channels primary=0x1f0/0x3f6 status=0x0 live=1' }
    [pscustomobject]@{ Name = 'numeric-alias'; Expected = $false; Ide = '[STO] IDE channels primary=0x1f0/0x3f6 status=0x0 live=1 secondary=0x01f0/0x03f6 status=0x50 live=1' }
    [pscustomobject]@{ Name = 'reordered'; Expected = $false; Ide = '[STO] IDE channels primary=0x1f0/0x3f6 secondary=0x170/0x376 status=0x0 status=0x50 live=1 live=1' }
    [pscustomobject]@{ Name = 'status-range'; Expected = $false; Ide = '[STO] IDE channels primary=0x1f0/0x3f6 status=0x100 live=1 secondary=0x170/0x376 status=0x50 live=1' }
    [pscustomobject]@{ Name = 'shared-endpoint'; Expected = $false; Ide = '[STO] IDE channels primary=0x1f0/0x3f6 status=0x0 live=1 secondary=0x1f0/0x376 status=0x50 live=1' }
    [pscustomobject]@{ Name = 'uppercase-keys'; Expected = $false; Ide = '[STO] IDE channels Primary=0x1f0/0x3f6 Status=0x0 Live=1 Secondary=0x170/0x376 Status=0x50 Live=1' }
)

$tempPath = [IO.Path]::GetTempFileName()
try {
    foreach ($case in $cases) {
        $candidateLines = @(
            $normalLines | ForEach-Object {
                if ($_ -ceq $validIde) { [string]$case.Ide } else { $_ }
            }
        )
        [IO.File]::WriteAllLines(
            $tempPath,
            $candidateLines,
            [Text.UTF8Encoding]::new($false)
        )
        $verdict = Test-NormalSmokeVerdict -SerialLog $tempPath
        if ([bool]$verdict.Passed -ne [bool]$case.Expected) {
            throw "Unexpected verdict for $($case.Name): passed=$($verdict.Passed) reasons=$($verdict.Reasons -join ';')"
        }
        Write-Output "PASS $($case.Name) expected=$($case.Expected)"
    }

    $missingPairLines = @(
        $normalLines | Where-Object {
            $_ -notmatch '^\[USER\] bootstrap process pair '
        }
    )
    [IO.File]::WriteAllLines(
        $tempPath,
        $missingPairLines,
        [Text.UTF8Encoding]::new($false)
    )
    $missingPairVerdict = Test-NormalSmokeVerdict -SerialLog $tempPath
    if ([bool]$missingPairVerdict.Passed) {
        throw 'Missing bootstrap process pair evidence unexpectedly passed'
    }
    Write-Output 'PASS missing-process-pair expected=False'

    $missingPressureLines = @(
        $normalLines | Where-Object {
            $_ -notmatch '^\[PRESSURE\] tracker selftest '
        }
    )
    [IO.File]::WriteAllLines(
        $tempPath,
        $missingPressureLines,
        [Text.UTF8Encoding]::new($false)
    )
    $missingPressureVerdict = Test-NormalSmokeVerdict -SerialLog $tempPath
    if ([bool]$missingPressureVerdict.Passed) {
        throw 'Missing pressure tracker evidence unexpectedly passed'
    }
    Write-Output 'PASS missing-pressure-tracker expected=False'

    $mutatingPressureLines = @(
        $normalLines | ForEach-Object {
            if ($_ -match '^\[PRESSURE\] tracker selftest ') {
                "$_ apply_enabled=1"
            } else {
                $_
            }
        }
    )
    [IO.File]::WriteAllLines(
        $tempPath,
        $mutatingPressureLines,
        [Text.UTF8Encoding]::new($false)
    )
    $mutatingPressureVerdict = Test-NormalSmokeVerdict -SerialLog $tempPath
    if ([bool]$mutatingPressureVerdict.Passed) {
        throw 'Apply-capable pressure tracker evidence unexpectedly passed'
    }
    Write-Output 'PASS mutating-pressure-tracker expected=False'

    $missingResourceLines = @(
        $normalLines | Where-Object {
            $_ -notmatch '^\[RESOURCE\] ledger selftest '
        }
    )
    [IO.File]::WriteAllLines(
        $tempPath,
        $missingResourceLines,
        [Text.UTF8Encoding]::new($false)
    )
    $missingResourceVerdict = Test-NormalSmokeVerdict -SerialLog $tempPath
    if ([bool]$missingResourceVerdict.Passed) {
        throw 'Missing resource ledger evidence unexpectedly passed'
    }
    Write-Output 'PASS missing-resource-ledger expected=False'

    $incompleteResourceLines = @(
        $normalLines | ForEach-Object {
            if ($_ -match '^\[RESOURCE\] ledger selftest ') {
                '[RESOURCE] ledger selftest PASS schema=1 kinds=5'
            } else {
                $_
            }
        }
    )
    [IO.File]::WriteAllLines(
        $tempPath,
        $incompleteResourceLines,
        [Text.UTF8Encoding]::new($false)
    )
    $incompleteResourceVerdict = Test-NormalSmokeVerdict -SerialLog $tempPath
    if ([bool]$incompleteResourceVerdict.Passed) {
        throw 'Incomplete resource ledger evidence unexpectedly passed'
    }
    Write-Output 'PASS incomplete-resource-ledger expected=False'

    $mutatingResourceLines = @(
        $normalLines | ForEach-Object {
            if ($_ -match '^\[RESOURCE\] ledger selftest ') {
                "$_ apply_enabled=1"
            } else {
                $_
            }
        }
    )
    [IO.File]::WriteAllLines(
        $tempPath,
        $mutatingResourceLines,
        [Text.UTF8Encoding]::new($false)
    )
    $mutatingResourceVerdict = Test-NormalSmokeVerdict -SerialLog $tempPath
    if ([bool]$mutatingResourceVerdict.Passed) {
        throw 'Apply-capable resource ledger evidence unexpectedly passed'
    }
    Write-Output 'PASS mutating-resource-ledger expected=False'

    $missingTrapContractLines = @(
        $normalLines | Where-Object {
            $_ -notmatch '^\[TRAP\] frame contract selftest '
        }
    )
    [IO.File]::WriteAllLines(
        $tempPath,
        $missingTrapContractLines,
        [Text.UTF8Encoding]::new($false)
    )
    $missingTrapContractVerdict = Test-NormalSmokeVerdict -SerialLog $tempPath
    if ([bool]$missingTrapContractVerdict.Passed) {
        throw 'Missing trapframe contract evidence unexpectedly passed'
    }
    Write-Output 'PASS missing-trapframe-contract expected=False'

    $missingTrapCaptureLines = @(
        $normalLines | Where-Object {
            $_ -notmatch '^\[TRAP\] user frame capture '
        }
    )
    [IO.File]::WriteAllLines(
        $tempPath,
        $missingTrapCaptureLines,
        [Text.UTF8Encoding]::new($false)
    )
    $missingTrapCaptureVerdict = Test-NormalSmokeVerdict -SerialLog $tempPath
    if ([bool]$missingTrapCaptureVerdict.Passed) {
        throw 'Missing user trap capture evidence unexpectedly passed'
    }
    Write-Output 'PASS missing-user-trap-capture expected=False'

    $mutatingTrapCaptureLines = @(
        $normalLines | ForEach-Object {
            if ($_ -match '^\[TRAP\] user frame capture ') {
                "$_ extra=1"
            } else {
                $_
            }
        }
    )
    [IO.File]::WriteAllLines(
        $tempPath,
        $mutatingTrapCaptureLines,
        [Text.UTF8Encoding]::new($false)
    )
    $mutatingTrapCaptureVerdict = Test-NormalSmokeVerdict -SerialLog $tempPath
    if ([bool]$mutatingTrapCaptureVerdict.Passed) {
        throw 'Extended user trap capture evidence unexpectedly passed'
    }
    Write-Output 'PASS mutating-user-trap-capture expected=False'

    $duplicateObservationCases = @(
        [pscustomobject]@{
            Name = 'duplicate-resource-ledger'
            Line = $normalLines | Where-Object {
                $_ -match '^\[RESOURCE\] ledger selftest '
            } | Select-Object -First 1
        }
        [pscustomobject]@{
            Name = 'duplicate-pressure-tracker'
            Line = $normalLines | Where-Object {
                $_ -match '^\[PRESSURE\] tracker selftest '
            } | Select-Object -First 1
        }
        [pscustomobject]@{
            Name = 'duplicate-trapframe-contract'
            Line = $normalLines | Where-Object {
                $_ -match '^\[TRAP\] frame contract selftest '
            } | Select-Object -First 1
        }
        [pscustomobject]@{
            Name = 'duplicate-user-trap-capture'
            Line = $normalLines | Where-Object {
                $_ -match '^\[TRAP\] user frame capture '
            } | Select-Object -First 1
        }
    )
    foreach ($duplicateCase in $duplicateObservationCases) {
        $duplicateObservationLines = @($normalLines) + @(
            [string]$duplicateCase.Line
        )
        [IO.File]::WriteAllLines(
            $tempPath,
            $duplicateObservationLines,
            [Text.UTF8Encoding]::new($false)
        )
        $duplicateObservationVerdict =
            Test-NormalSmokeVerdict -SerialLog $tempPath
        if ([bool]$duplicateObservationVerdict.Passed) {
            throw "$($duplicateCase.Name) unexpectedly passed"
        }
        Write-Output "PASS $($duplicateCase.Name) expected=False"
    }
} finally {
    Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
}

Write-Output "PowerShell verdict selftest passed cases=$($cases.Count + 9 + $duplicateObservationCases.Count)"
