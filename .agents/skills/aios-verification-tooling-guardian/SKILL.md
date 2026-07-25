---
name: aios-verification-tooling-guardian
description: Use when auditing, designing, or changing AIOS testkit, QEMU smoke or shell lanes, boot-log parsers, CI gates, artifacts, baselines, kernel selftests, health or panic markers, state topics, or any verifier-observed evidence. Enforce fail-closed verdicts, evidence/verdict separation, reproducible artifacts, and honest maturity claims.
---

# AIOS Verification Tooling Guardian

Keep the verifier at least as trustworthy as the kernel behavior it claims to
prove. Treat kernel evidence, host verdicts, execution outcomes, and CI status
as separate contracts.

## Establish the baseline

1. Inspect `git status --short --branch` and preserve unrelated work.
2. Read:
   - `CLAUDE.md`
   - `docs/meta/codex_handoff_tips_ko.md`
   - `docs/tools/verification_tooling_evolution_design_ko.md`
   - `docs/tools/testkit_guide_ko.md`
   - the affected subsystem design
3. Use `rg` to find every producer and consumer of a changed marker, field,
   numeric ID, baseline value, or outcome.
4. State the exact current implementation boundary before proposing a stronger
   claim.

Use `$aios-kernel-change-guardian` for kernel/runtime/MM/scheduler/driver edits
and `$aios-enum-abi-integrity` for stable numeric contracts.

## Classify the layer

| Layer | Examples | Contract |
|---|---|---|
| Internal evidence | selftest, health, panic, guard, assert | kernel proves an invariant and fails safely |
| Observation | serial marker, state topic, event record | evidence is unambiguous and machine-readable |
| External verdict | parser, smoke, shell, matrix, baseline | malformed or fatal runs cannot pass |
| Execution | QEMU launch, timeout, exit handling | termination reason is explicit |
| Artifact and CI | manifest, raw log, workflow gate | a result can be reproduced and triaged |

Prefer the smallest vertical slice that closes one false-positive or
missing-proof path end to end.

## Apply fail-closed verdict rules

For a normal boot or shell verdict, require every applicable rule:

- all required profile markers are present
- fatal events are scanned across the entire log, including after PASS
- panic, exception, unexpected `FAIL`, and `FATAL` fail the contract
- health values are parsed and semantically validated
- terminal checkpoints are unique and ordered
- proof markers use anchored records and exact token boundaries
- quoted diagnostics, prefixes, and values such as `PASSFAIL` or `ready=10`
  are not evidence
- fields forming one claim come from the same record
- duplicate keys on contract-bearing records fail
- canonical values are case-sensitive and leading indentation cannot turn
  quoted output into proof
- timeout, skip, unsupported, infrastructure error, and kernel failure remain
  distinct outcomes
- guest exit and host termination reasons are recorded
- shell PASS requires acknowledged reboot/exit, drained output, and a clean
  QEMU exit
- verdict output includes machine-readable reasons and first-failure context

Expected-fault lanes must define their allowed fatal or degraded outcome
separately. Never weaken the normal contract to accommodate them.

## Test pure verdict logic first

Add host tests before QEMU integration when practical. Cover:

- complete normal log
- missing required marker
- PASS followed by panic or exception
- explicit FAIL or FATAL
- harmless substrings such as `failed=0`
- degraded or malformed health
- reordered, duplicated, or conflicting checkpoints
- token-prefix impostors and quoted markers
- duplicate or conflicting fields
- blank lines before failure so raw-log line numbers remain correct
- truncated output
- stale PASS artifact from a previous run

Keep verdict evaluation pure and dependency-light where possible.

## Change kernel evidence safely

1. State the invariant and failure policy.
2. Distinguish ordinary test failure from uncertain machine state.
3. Fail-stop when CR3, TSS `rsp0`, IF, stack ownership, current process, or
   sealed mappings cannot be proven restored.
4. Emit one bounded human-readable marker with stable key/value facts.
5. Expose durable runtime state through `state <topic>` when useful.
6. Update Python, PowerShell, shell exchanges, CI, docs, and baselines that
   consume the evidence in the same slice.
7. Add negative or rollback proof for cleanup paths.

## Protect baselines

Treat checked-in baseline writes as approval operations:

- require strict mode and a complete non-skipped source matrix
- match requested and produced profiles exactly
- run all guards before touching a fixture
- reject unknown states, impossible counters, non-finite metrics, and
  incomparable test shapes
- generate a candidate for explicit approval when practical
- never refresh a baseline merely to make a failing comparison pass

## Preserve artifacts and provenance

For each executed profile retain, when available:

- raw serial log
- parsed events and boot summary
- verdict and reasons
- exact QEMU arguments and termination reason
- git SHA and dirty state
- kernel/ISO hashes
- QEMU/compiler/linker/assembler versions
- duration and timeout state

Create or clear current-run artifacts before launching external processes. An
early build, launch, or read failure must replace stale PASS state with an
honest failure or skip. Drain output before final marker and exit-code
decisions. Do not overwrite the only raw log while building a matrix.

## Keep platform contracts aligned

- Until a shared marker manifest exists, update Python and PowerShell smoke
  contracts together.
- Keep `make test`, direct PowerShell, Python testkit, and CI semantics
  explicit; do not imply unimplemented parity.
- Use an explicit CPU profile such as QEMU `-cpu max` before claiming
  SMAP/SMEP coverage.
- Keep performance results advisory unless the host and variance policy support
  a hard gate.

## Validate in layers

1. parser/formatter checks and `git diff --check`
2. host unit tests for verdicts and guards
3. Python/PowerShell syntax and fixture checks
4. relevant static analysis
5. one minimal QEMU smoke reproducing the contract
6. `full`, `minimal`, and `storage-only` when shared markers, health, device
   expectations, or baselines change
7. shell lane for state topics or clean-exit behavior
8. inventory strict comparison for stable structured output
9. security CPU or expected-fault profile when required

Report exact commands, outcomes, and unrun lanes. A timeout-killed VM is not a
clean exit.

## Synchronize maturity claims

Use `CURRENT`, `PARTIAL`, `SCAFFOLD`, and `PLANNED` for implementation maturity.
Reserve `RESEARCH` for optional roadmap experiments. Promote to `CURRENT` only
when code, regular test, and reproducible artifact agree.

Before finishing, answer:

- What kernel evidence is produced?
- Which host rule turns it into PASS or FAIL?
- Can PASS followed by a fatal event still succeed?
- What do timeout and termination mean?
- Can a failed or skipped run update a baseline?
- Are Python, PowerShell, Make, and CI aligned or explicitly partial?
- Which negative case proves the rule?
- Which claims remain partial or planned?
