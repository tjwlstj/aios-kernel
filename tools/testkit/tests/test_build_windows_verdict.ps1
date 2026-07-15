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
    '[SLM] plan apply selftest PASS'
    '[SLM] Seeded plan 4 label=storage-bootstrap action=8'
    '[SYSCALL] observe dispatch selftest PASS'
    '[USER] Ring3 scaffold ready=1 tr=0x28'
    '[PROC] bootstrap ownership selftest PASS slots=2 owned=2 stack_bytes=16384 unique_cr3=1 unique_backing=1 unique_stack=1'
    '[USER] ring3 exec PASS exit_code=42'
    '[USER] private address space exec PASS slot=0 cr3_restored=1 if_restored=1 leaf_sealed=1 nx_enforced=1 tensor_excluded=1'
    '[USER] bootstrap process stack PASS pid=1 slot=0 process_bound=1 kstack_bytes=16384 rsp0_changed=1 rsp0_published=1 int80_entries=3 all_int80_entries_in_stack=1 rsp0_restored=1 kstack_floor_canary=1'
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
} finally {
    Remove-Item -LiteralPath $tempPath -Force -ErrorAction SilentlyContinue
}

Write-Output "PowerShell verdict selftest passed cases=$($cases.Count)"
