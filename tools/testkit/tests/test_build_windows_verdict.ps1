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

foreach ($functionName in @('Get-QemuBootArguments', 'Get-SmokeRequiredPatterns', 'Test-NormalSmokeVerdict')) {
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
$script:CpuProfile = 'default'
$defaultQemuArgs = @(Get-QemuBootArguments -IsoPath 'kernel.iso' -SerialMode 'stdio' -DisplayMode 'none')
if ($defaultQemuArgs -contains '-cpu') {
    throw 'Default CPU profile unexpectedly added a QEMU -cpu argument'
}
$script:CpuProfile = 'max-smap'
$maxQemuArgs = @(Get-QemuBootArguments -IsoPath 'kernel.iso' -SerialMode 'stdio' -DisplayMode 'none')
$cpuIndex = [Array]::IndexOf($maxQemuArgs, '-cpu')
if ($cpuIndex -lt 0 -or $cpuIndex + 1 -ge $maxQemuArgs.Count -or
        $maxQemuArgs[$cpuIndex + 1] -cne 'max' -or
        @($maxQemuArgs | Where-Object { $_ -ceq '-cpu' }).Count -ne 1) {
    throw 'max-smap CPU profile did not add exactly one `-cpu max` pair'
}
$script:CpuProfile = 'default'
$validIde = '[STO] IDE channels primary=0x1f0/0x3f6 status=0x0 live=1 secondary=0x170/0x376 status=0x50 live=1'
$normalLines = @(
    '[BOOT] Multiboot2 handoff PASS'
    '[SEC] nx=1 smep=0 umip=0 smap_supported=0 smap=0'
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
    '[PROC] trap evidence snapshot PASS schema=1 captures=2 pid_a=1 slot_a=0 seq_a=1 valid_a=1 owner_a=1 frame_a=1 cr3_a=1 rsp0_a=1 pid_b=2 slot_b=1 seq_b=2 valid_b=1 owner_b=1 frame_b=1 cr3_b=1 rsp0_b=1 distinct_storage=1 current_pid=0 stale_owner=0 resume_ready=0'
    '[PROC] process event journal PASS schema=1 events=6 lifecycle=4 captures=2 seqs=1,2,3,4,5,6 kinds=1,2,3,1,2,3 reasons=1,2,3,1,2,3 from_pids=0,1,1,0,2,2 to_pids=1,1,0,2,2,0 slots=0,0,0,1,1,1 generations=1,1,1,1,1,1 capture_seqs=0,1,1,0,2,2 owner_ok=1,1,1,1,1,1 cr3_ok=1,1,1,1,1,1 rsp0_ok=1,1,1,1,1,1 if0=1,1,1,1,1,1 snapshot_refs=0,1,1,0,1,1 outcomes=1,1,1,1,1,1 capture_seq_separate=1 current_pid=0 stale_owner=0 dropped=0 overflow=0 evidence_only=1 switch_events=0 resume_ready=0'
    '[SEC] ring3 entry AC hardening PASS schema=1 smap_supported=0 smap=0 gate_active=0 common_entries=2 common_saved_ac=2 common_clac=0 common_fallback=2 common_post_ac0=2 int80_entries=6 int80_saved_ac=4 int80_clac=0 int80_fallback=6 int80_post_ac0=6 gate_skips=8 gate_mismatch=0'
    '[ROOM] management hierarchy selftest PASS schema=1 struct_size=1024 generation=1 cells=1 nodes=1 bound_nodes=1 nodebits=2 bound_nodebits=2 source_valid=1 generation_valid=1 duplicate_rejected=1 orphan_rejected=1 unknown_rejected=1 stale_rejected=1 overflow_rejected=1 tail_rejected=1 observation_only=1 management_only=1'
    '[ROOM] snapshot stability=stable ok=18 degraded=0 failed=0 unknown=2 topology=segmented domains=4 windows=0 drivers=1/1 plans=5 nodes=10 rings=0 active=0 user=1 nodebit_active=1 nodebit_risky=0'
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
$journalExtraCases = 0
$securityExtraCases = 0
$managementExtraCases = 0
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

    $defaultSecurity = '[SEC] nx=1 smep=0 umip=0 smap_supported=0 smap=0'
    $maxSecurity = '[SEC] nx=1 smep=1 umip=1 smap_supported=1 smap=1'
    $defaultEntry = [string]($normalLines | Where-Object {
        $_ -match '^\[SEC\] ring3 entry AC hardening '
    } | Select-Object -First 1)
    $roomLine = [string]($normalLines | Where-Object {
        $_ -match '^\[ROOM\] snapshot '
    } | Select-Object -First 1)
    $managementLine = [string]($normalLines | Where-Object {
        $_ -match '^\[ROOM\] management hierarchy '
    } | Select-Object -First 1)
    $maxEntry = '[SEC] ring3 entry AC hardening PASS schema=1 smap_supported=1 smap=1 gate_active=1 common_entries=2 common_saved_ac=2 common_clac=2 common_fallback=0 common_post_ac0=2 int80_entries=6 int80_saved_ac=4 int80_clac=6 int80_fallback=0 int80_post_ac0=6 gate_skips=0 gate_mismatch=0'

    $maxProfileLines = @(
        $normalLines | ForEach-Object {
            if ($_ -ceq $defaultSecurity) { $maxSecurity }
            elseif ($_ -ceq $defaultEntry) { $maxEntry }
            else { $_ }
        }
    )
    $script:CpuProfile = 'max-smap'
    [IO.File]::WriteAllLines(
        $tempPath,
        $maxProfileLines,
        [Text.UTF8Encoding]::new($false)
    )
    $maxProfileVerdict = Test-NormalSmokeVerdict -SerialLog $tempPath
    if (-not [bool]$maxProfileVerdict.Passed) {
        throw "Valid max-smap evidence failed: $($maxProfileVerdict.Reasons -join ';')"
    }
    $securityExtraCases++
    Write-Output 'PASS valid-max-smap expected=True'

    $script:CpuProfile = 'default'
    $securityMutations = @(
        [pscustomobject]@{ Name = 'missing-entry-ac'; Line = $null }
        [pscustomobject]@{ Name = 'saved-ac'; Line = $defaultEntry.Replace('common_saved_ac=2', 'common_saved_ac=1') }
        [pscustomobject]@{ Name = 'int80-saved-ac'; Line = $defaultEntry.Replace('int80_saved_ac=4', 'int80_saved_ac=3') }
        [pscustomobject]@{ Name = 'post-ac'; Line = $defaultEntry.Replace('common_post_ac0=2', 'common_post_ac0=1') }
        [pscustomobject]@{ Name = 'gate-skips'; Line = $defaultEntry.Replace('gate_skips=8', 'gate_skips=7') }
        [pscustomobject]@{ Name = 'gate-mismatch'; Line = $defaultEntry.Replace('gate_mismatch=0', 'gate_mismatch=1') }
        [pscustomobject]@{ Name = 'extended-entry-ac'; Line = "$defaultEntry extra=1" }
        [pscustomobject]@{ Name = 'max-entry-on-default'; Line = $maxEntry }
    )
    foreach ($mutation in $securityMutations) {
        $mutatedLines = @(
            $normalLines | ForEach-Object {
                if ($_ -ceq $defaultEntry) {
                    if ($null -ne $mutation.Line) { [string]$mutation.Line }
                } else { $_ }
            }
        )
        [IO.File]::WriteAllLines(
            $tempPath,
            $mutatedLines,
            [Text.UTF8Encoding]::new($false)
        )
        $verdict = Test-NormalSmokeVerdict -SerialLog $tempPath
        if ([bool]$verdict.Passed) {
            throw "$($mutation.Name) unexpectedly passed"
        }
        $securityExtraCases++
        Write-Output "PASS $($mutation.Name) expected=False"
    }

    $roomMutations = @(
        [pscustomobject]@{ Name = 'room-truncated'; Line = '[ROOM] snapshot stability=stable' }
        [pscustomobject]@{ Name = 'room-ok-zero'; Line = $roomLine.Replace('ok=18', 'ok=0') }
        [pscustomobject]@{ Name = 'room-uint32-overflow'; Line = $roomLine.Replace('ok=18', "ok=$(('9' * 100) -join '')") }
        [pscustomobject]@{ Name = 'room-arabic-digit'; Line = $roomLine.Replace('ok=18', "ok=9$([char]0x0662)") }
        [pscustomobject]@{ Name = 'room-fullwidth-digit'; Line = $roomLine.Replace('ok=18', "ok=9$([char]0xFF12)") }
        [pscustomobject]@{ Name = 'room-degraded'; Line = $roomLine.Replace('degraded=0', 'degraded=1') }
        [pscustomobject]@{ Name = 'room-failed'; Line = $roomLine.Replace('failed=0', 'failed=1') }
        [pscustomobject]@{ Name = 'room-missing-field'; Line = $roomLine.Replace(' unknown=2', '') }
        [pscustomobject]@{ Name = 'room-unknown-topology'; Line = $roomLine.Replace('topology=segmented', 'topology=unknown') }
        [pscustomobject]@{ Name = 'room-zero-domains'; Line = $roomLine.Replace('domains=4', 'domains=0') }
        [pscustomobject]@{ Name = 'room-driver-range'; Line = $roomLine.Replace('drivers=1/1', 'drivers=2/1') }
        [pscustomobject]@{ Name = 'room-active-range'; Line = $roomLine.Replace('rings=0 active=0', 'rings=0 active=1') }
        [pscustomobject]@{ Name = 'room-user-not-ready'; Line = $roomLine.Replace('user=1', 'user=0') }
        [pscustomobject]@{ Name = 'room-nodebit-range'; Line = $roomLine.Replace('nodebit_risky=0', 'nodebit_risky=2') }
        [pscustomobject]@{ Name = 'room-reordered'; Line = $roomLine.Replace('failed=0 unknown=2', 'unknown=2 failed=0') }
        [pscustomobject]@{ Name = 'room-passfail-suffix'; Line = "$roomLine PASSFAIL" }
        [pscustomobject]@{ Name = 'room-partial-suffix'; Line = "$roomLine PARTIAL" }
        [pscustomobject]@{ Name = 'room-extra-field'; Line = "$roomLine extra=1" }
    )
    foreach ($mutation in $roomMutations) {
        $mutatedLines = @(
            $normalLines | ForEach-Object {
                if ($_ -ceq $roomLine) { [string]$mutation.Line } else { $_ }
            }
        )
        [IO.File]::WriteAllLines(
            $tempPath,
            $mutatedLines,
            [Text.UTF8Encoding]::new($false)
        )
        $verdict = Test-NormalSmokeVerdict -SerialLog $tempPath
        if ([bool]$verdict.Passed -or
                @($verdict.Reasons | Where-Object {
                    $_ -like 'evidence-record-invalid:*:kernel_room'
                }).Count -eq 0) {
            throw "$($mutation.Name) did not fail the ROOM record contract"
        }
        $securityExtraCases++
        Write-Output "PASS $($mutation.Name) expected=False"
    }

    $managementMutations = @(
        [pscustomobject]@{ Name = 'management-missing'; Line = $null }
        [pscustomobject]@{ Name = 'management-truncated'; Line = '[ROOM] management hierarchy selftest PASS schema=1' }
        [pscustomobject]@{ Name = 'management-struct-size'; Line = $managementLine.Replace('struct_size=1024', 'struct_size=1023') }
        [pscustomobject]@{ Name = 'management-generation'; Line = $managementLine.Replace('generation=1 cells=1', 'generation=2 cells=1') }
        [pscustomobject]@{ Name = 'management-bound-nodes'; Line = $managementLine.Replace('bound_nodes=1', 'bound_nodes=0') }
        [pscustomobject]@{ Name = 'management-bound-nodebits'; Line = $managementLine.Replace('bound_nodebits=2', 'bound_nodebits=1') }
        [pscustomobject]@{ Name = 'management-source-valid'; Line = $managementLine.Replace('source_valid=1', 'source_valid=0') }
        [pscustomobject]@{ Name = 'management-generation-valid'; Line = $managementLine.Replace('generation_valid=1', 'generation_valid=0') }
        [pscustomobject]@{ Name = 'management-duplicate-rejected'; Line = $managementLine.Replace('duplicate_rejected=1', 'duplicate_rejected=0') }
        [pscustomobject]@{ Name = 'management-orphan-rejected'; Line = $managementLine.Replace('orphan_rejected=1', 'orphan_rejected=0') }
        [pscustomobject]@{ Name = 'management-unknown-rejected'; Line = $managementLine.Replace('unknown_rejected=1', 'unknown_rejected=0') }
        [pscustomobject]@{ Name = 'management-stale-rejected'; Line = $managementLine.Replace('stale_rejected=1', 'stale_rejected=0') }
        [pscustomobject]@{ Name = 'management-overflow-rejected'; Line = $managementLine.Replace('overflow_rejected=1', 'overflow_rejected=0') }
        [pscustomobject]@{ Name = 'management-tail-rejected'; Line = $managementLine.Replace('tail_rejected=1', 'tail_rejected=0') }
        [pscustomobject]@{ Name = 'management-observation-only'; Line = $managementLine.Replace('observation_only=1', 'observation_only=0') }
        [pscustomobject]@{ Name = 'management-management-only'; Line = $managementLine.Replace('management_only=1', 'management_only=0') }
        [pscustomobject]@{ Name = 'management-duplicate-field'; Line = $managementLine.Replace('generation=1 cells=1', 'generation=1 generation=1 cells=1') }
        [pscustomobject]@{ Name = 'management-passfail'; Line = $managementLine.Replace('selftest PASS', 'selftest PASSFAIL') }
        [pscustomobject]@{ Name = 'management-extra-field'; Line = "$managementLine apply_enabled=1" }
        [pscustomobject]@{ Name = 'management-indented'; Line = "  $managementLine" }
        [pscustomobject]@{ Name = 'management-quoted'; Line = '"' + $managementLine + '"' }
    )
    foreach ($mutation in $managementMutations) {
        $mutatedLines = @(
            $normalLines | ForEach-Object {
                if ($_ -ceq $managementLine) {
                    if ($null -ne $mutation.Line) { [string]$mutation.Line }
                } else { $_ }
            }
        )
        [IO.File]::WriteAllLines(
            $tempPath,
            $mutatedLines,
            [Text.UTF8Encoding]::new($false)
        )
        $verdict = Test-NormalSmokeVerdict -SerialLog $tempPath
        if ([bool]$verdict.Passed) {
            throw "$($mutation.Name) unexpectedly passed"
        }
        $managementExtraCases++
        Write-Output "PASS $($mutation.Name) expected=False"
    }

    foreach ($extra in @(
            $managementLine,
            '[ROOM] management hierarchy selftest PARTIAL schema=1',
            '[ROOM] management hierarchy'
        )) {
        $extraLines = @($normalLines) + @([string]$extra)
        [IO.File]::WriteAllLines(
            $tempPath,
            $extraLines,
            [Text.UTF8Encoding]::new($false)
        )
        $verdict = Test-NormalSmokeVerdict -SerialLog $tempPath
        if ([bool]$verdict.Passed -or
                @($verdict.Reasons | Where-Object {
                    $_ -like 'evidence-family-count:kernel_room_management:*'
                }).Count -eq 0) {
            throw 'Duplicate management hierarchy family unexpectedly passed'
        }
        $managementExtraCases++
        Write-Output 'PASS duplicate-management-hierarchy-family expected=False'
    }

    $managementBeforeEntryLines = @()
    foreach ($line in $normalLines) {
        if ($line -ceq $managementLine) { continue }
        if ($line -ceq $defaultEntry) {
            $managementBeforeEntryLines += $managementLine
        }
        $managementBeforeEntryLines += $line
    }
    $managementAfterRoomLines = @()
    foreach ($line in $normalLines) {
        if ($line -ceq $managementLine) { continue }
        $managementAfterRoomLines += $line
        if ($line -ceq $roomLine) {
            $managementAfterRoomLines += $managementLine
        }
    }
    foreach ($orderCase in @(
            [pscustomobject]@{
                Name = 'management-before-entry-ac'
                Lines = $managementBeforeEntryLines
            }
            [pscustomobject]@{
                Name = 'management-after-aggregate-room'
                Lines = $managementAfterRoomLines
            }
        )) {
        [IO.File]::WriteAllLines(
            $tempPath,
            [string[]]$orderCase.Lines,
            [Text.UTF8Encoding]::new($false)
        )
        $verdict = Test-NormalSmokeVerdict -SerialLog $tempPath
        if ([bool]$verdict.Passed -or
                @($verdict.Reasons | Where-Object {
                    $_ -like 'terminal-order:*'
                }).Count -eq 0) {
            throw "$($orderCase.Name) did not fail the terminal order chain"
        }
        $managementExtraCases++
        Write-Output "PASS $($orderCase.Name) expected=False"
    }

    $securityFamilyCases = @(
        [pscustomobject]@{
            Name = 'duplicate-entry-ac-family'
            Extra = '[SEC] ring3 entry AC hardening PARTIAL schema=1'
        }
        [pscustomobject]@{
            Name = 'duplicate-cpu-security-family'
            Extra = $defaultSecurity
        }
        [pscustomobject]@{
            Name = 'malformed-cpu-security-family'
            Extra = "$defaultSecurity extra=1"
        }
    )
    foreach ($familyCase in $securityFamilyCases) {
        $extraLines = @($normalLines) + @([string]$familyCase.Extra)
        [IO.File]::WriteAllLines(
            $tempPath,
            $extraLines,
            [Text.UTF8Encoding]::new($false)
        )
        $verdict = Test-NormalSmokeVerdict -SerialLog $tempPath
        if ([bool]$verdict.Passed) {
            throw 'Duplicate security family evidence unexpectedly passed'
        }
        $securityExtraCases++
        Write-Output "PASS $($familyCase.Name) expected=False"
    }

    $featureAfterEntryLines = @()
    foreach ($line in $normalLines) {
        if ($line -ceq $defaultSecurity) {
            continue
        }
        $featureAfterEntryLines += $line
        if ($line -ceq $defaultEntry) {
            $featureAfterEntryLines += $defaultSecurity
        }
    }
    $featureAfterShellLines = @(
        $normalLines | Where-Object { $_ -cne $defaultSecurity }
    ) + @($defaultSecurity)
    foreach ($orderCase in @(
            [pscustomobject]@{
                Name = 'cpu-security-after-entry-ac'
                Lines = $featureAfterEntryLines
            }
            [pscustomobject]@{
                Name = 'cpu-security-after-shell'
                Lines = $featureAfterShellLines
            }
        )) {
        [IO.File]::WriteAllLines(
            $tempPath,
            [string[]]$orderCase.Lines,
            [Text.UTF8Encoding]::new($false)
        )
        $verdict = Test-NormalSmokeVerdict -SerialLog $tempPath
        if ([bool]$verdict.Passed -or
                @($verdict.Reasons | Where-Object {
                    $_ -like 'security-order:*'
                }).Count -eq 0) {
            throw "$($orderCase.Name) did not fail the security order chain"
        }
        $securityExtraCases++
        Write-Output "PASS $($orderCase.Name) expected=False"
    }

    $duplicateRoomFamilyLines = @()
    foreach ($line in $normalLines) {
        if ($line -match '^\[ROOM\] snapshot stability=stable') {
            $duplicateRoomFamilyLines +=
                $roomLine.Replace(
                    'stability=stable ok=18 degraded=0',
                    'stability=degraded ok=17 degraded=1'
                )
        }
        $duplicateRoomFamilyLines += $line
    }
    [IO.File]::WriteAllLines(
        $tempPath,
        $duplicateRoomFamilyLines,
        [Text.UTF8Encoding]::new($false)
    )
    $duplicateRoomVerdict = Test-NormalSmokeVerdict -SerialLog $tempPath
    if ([bool]$duplicateRoomVerdict.Passed -or
            @($duplicateRoomVerdict.Reasons | Where-Object {
                $_ -like 'evidence-family-count:kernel_room:*'
            }).Count -eq 0) {
        throw 'Duplicate ROOM snapshot family unexpectedly passed'
    }
    $securityExtraCases++
    Write-Output 'PASS duplicate-room-snapshot-family expected=False'

    $featureMismatchLines = @(
        $normalLines | ForEach-Object {
            if ($_ -ceq $defaultSecurity) { $maxSecurity } else { $_ }
        }
    )
    [IO.File]::WriteAllLines(
        $tempPath,
        $featureMismatchLines,
        [Text.UTF8Encoding]::new($false)
    )
    if ([bool](Test-NormalSmokeVerdict -SerialLog $tempPath).Passed) {
        throw 'max-smap feature row under default profile unexpectedly passed'
    }
    $securityExtraCases++
    Write-Output 'PASS mismatched-cpu-security-row expected=False'

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

    $missingTrapSnapshotLines = @(
        $normalLines | Where-Object {
            $_ -notmatch '^\[PROC\] trap evidence snapshot '
        }
    )
    [IO.File]::WriteAllLines(
        $tempPath,
        $missingTrapSnapshotLines,
        [Text.UTF8Encoding]::new($false)
    )
    $missingTrapSnapshotVerdict =
        Test-NormalSmokeVerdict -SerialLog $tempPath
    if ([bool]$missingTrapSnapshotVerdict.Passed) {
        throw 'Missing process trap snapshot evidence unexpectedly passed'
    }
    Write-Output 'PASS missing-process-trap-snapshot expected=False'

    foreach ($snapshotMutation in @(
        '[PROC] trap evidence snapshot PASS schema=1 captures=2'
        '[PROC] trap evidence snapshot PASS schema=1 captures=2 pid_a=1 slot_a=0 seq_a=1 valid_a=1 owner_a=1 frame_a=1 cr3_a=1 rsp0_a=1 pid_b=2 slot_b=1 seq_b=2 valid_b=1 owner_b=1 frame_b=1 cr3_b=1 rsp0_b=1 distinct_storage=1 current_pid=0 stale_owner=1 resume_ready=0'
        '[PROC] trap evidence snapshot PASS schema=1 captures=2 pid_a=1 slot_a=0 seq_a=1 valid_a=1 owner_a=1 frame_a=1 cr3_a=1 rsp0_a=1 pid_b=2 slot_b=1 seq_b=2 valid_b=1 owner_b=1 frame_b=1 cr3_b=1 rsp0_b=1 distinct_storage=1 current_pid=0 stale_owner=0 resume_ready=1'
    )) {
        $mutatedTrapSnapshotLines = @(
            $normalLines | ForEach-Object {
                if ($_ -match '^\[PROC\] trap evidence snapshot ') {
                    $snapshotMutation
                } else {
                    $_
                }
            }
        )
        [IO.File]::WriteAllLines(
            $tempPath,
            $mutatedTrapSnapshotLines,
            [Text.UTF8Encoding]::new($false)
        )
        $mutatedTrapSnapshotVerdict =
            Test-NormalSmokeVerdict -SerialLog $tempPath
        if ([bool]$mutatedTrapSnapshotVerdict.Passed) {
            throw 'Invalid process trap snapshot evidence unexpectedly passed'
        }
        Write-Output 'PASS invalid-process-trap-snapshot expected=False'
    }

    $journalLine = [string]($normalLines | Where-Object {
        $_ -match '^\[PROC\] process event journal '
    } | Select-Object -First 1)
    $missingJournalLines = @(
        $normalLines | Where-Object {
            $_ -notmatch '^\[PROC\] process event journal '
        }
    )
    [IO.File]::WriteAllLines(
        $tempPath,
        $missingJournalLines,
        [Text.UTF8Encoding]::new($false)
    )
    $missingJournalVerdict = Test-NormalSmokeVerdict -SerialLog $tempPath
    if ([bool]$missingJournalVerdict.Passed) {
        throw 'Missing process event journal evidence unexpectedly passed'
    }
    $journalExtraCases++
    Write-Output 'PASS missing-process-event-journal expected=False'

    $journalMutations = @(
        [pscustomobject]@{
            Name = 'truncated-process-event-journal'
            Line = '[PROC] process event journal PASS schema=1 events=6'
        }
        [pscustomobject]@{
            Name = 'aggregate-only-process-event-journal'
            Line = '[PROC] process event journal PASS schema=1 events=6 lifecycle=4 captures=2 current_pid=0 stale_owner=0 dropped=0 overflow=0 evidence_only=1 switch_events=0 resume_ready=0'
        }
        [pscustomobject]@{
            Name = 'reordered-process-event-vector'
            Line = $journalLine.Replace(
                'seqs=1,2,3,4,5,6',
                'seqs=1,3,2,4,5,6'
            )
        }
        [pscustomobject]@{
            Name = 'short-process-event-vector'
            Line = $journalLine.Replace(
                'capture_seqs=0,1,1,0,2,2',
                'capture_seqs=0,1,0,2,2'
            )
        }
        [pscustomobject]@{
            Name = 'unknown-process-event-kind'
            Line = $journalLine.Replace(
                'kinds=1,2,3,1,2,3',
                'kinds=1,2,99,1,2,3'
            )
        }
        [pscustomobject]@{
            Name = 'unknown-process-event-reason'
            Line = $journalLine.Replace(
                'reasons=1,2,3,1,2,3',
                'reasons=1,2,99,1,2,3'
            )
        }
        [pscustomobject]@{
            Name = 'unknown-process-event-outcome'
            Line = $journalLine.Replace(
                'outcomes=1,1,1,1,1,1',
                'outcomes=1,1,1,1,1,99'
            )
        }
        [pscustomobject]@{
            Name = 'stale-process-event-owner'
            Line = $journalLine.Replace('stale_owner=0', 'stale_owner=1')
        }
        [pscustomobject]@{
            Name = 'duplicate-process-event-key'
            Line = $journalLine.Replace(
                'kinds=1,2,3,1,2,3',
                'kinds=1,2,3,1,2,3 kinds=1,2,3,1,2,3'
            )
        }
    )
    foreach ($journalMutation in $journalMutations) {
        $mutatedJournalLines = @(
            $normalLines | ForEach-Object {
                if ($_ -match '^\[PROC\] process event journal ') {
                    [string]$journalMutation.Line
                } else {
                    $_
                }
            }
        )
        [IO.File]::WriteAllLines(
            $tempPath,
            $mutatedJournalLines,
            [Text.UTF8Encoding]::new($false)
        )
        $mutatedJournalVerdict =
            Test-NormalSmokeVerdict -SerialLog $tempPath
        if ([bool]$mutatedJournalVerdict.Passed) {
            throw "$($journalMutation.Name) unexpectedly passed"
        }
        $journalExtraCases++
        Write-Output "PASS $($journalMutation.Name) expected=False"
    }

    $duplicateJournalFamilyLines = @($normalLines) + @(
        '[PROC] process event journal PARTIAL schema=1 events=6'
    )
    [IO.File]::WriteAllLines(
        $tempPath,
        $duplicateJournalFamilyLines,
        [Text.UTF8Encoding]::new($false)
    )
    $duplicateJournalFamilyVerdict =
        Test-NormalSmokeVerdict -SerialLog $tempPath
    if ([bool]$duplicateJournalFamilyVerdict.Passed) {
        throw 'Truncated duplicate process event journal family unexpectedly passed'
    }
    $journalExtraCases++
    Write-Output 'PASS duplicate-process-event-journal-family expected=False'

    $reorderedJournalLines = @()
    foreach ($line in $normalLines) {
        if ($line -match '^\[PROC\] process event journal ') {
            continue
        }
        if ($line -match '^\[PROC\] trap evidence snapshot ') {
            $reorderedJournalLines += $journalLine
        }
        $reorderedJournalLines += $line
    }
    [IO.File]::WriteAllLines(
        $tempPath,
        $reorderedJournalLines,
        [Text.UTF8Encoding]::new($false)
    )
    $reorderedJournalVerdict =
        Test-NormalSmokeVerdict -SerialLog $tempPath
    if ([bool]$reorderedJournalVerdict.Passed) {
        throw 'Reordered process event journal checkpoint unexpectedly passed'
    }
    $journalExtraCases++
    Write-Output 'PASS reordered-process-event-journal-checkpoint expected=False'

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
        [pscustomobject]@{
            Name = 'duplicate-process-trap-snapshot'
            Line = $normalLines | Where-Object {
                $_ -match '^\[PROC\] trap evidence snapshot '
            } | Select-Object -First 1
        }
        [pscustomobject]@{
            Name = 'duplicate-process-event-journal'
            Line = $normalLines | Where-Object {
                $_ -match '^\[PROC\] process event journal '
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

Write-Output "PowerShell verdict selftest passed cases=$($cases.Count + 13 + $duplicateObservationCases.Count + $journalExtraCases + $securityExtraCases + $managementExtraCases + 1)"
