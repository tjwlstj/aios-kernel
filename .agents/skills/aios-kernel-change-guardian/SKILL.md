---
name: aios-kernel-change-guardian
description: Use when editing AIOS kernel, runtime, scheduler, memory management, HAL, drivers, assembly, linker layout, syscalls, or public headers. Protect low-level invariants with minimal reversible patches and verification matched to risk. Do not use for prose-only work.
---

# AIOS Kernel Change Guardian

Make low-level changes correct, reviewable, and easy to roll back.

## Declare the change contract

Before editing, identify:

- touched files and symbols
- the invariant being changed or preserved
- boot, memory, privilege, interrupt, ownership, and cleanup effects
- enum, struct layout, syscall, ABI, log, or persistent-format impact
- the nearest negative test and rollback path

Read `CLAUDE.md`, `docs/meta/codex_handoff_tips_ko.md`, and the nearest design.
Inspect `git status` and preserve unrelated work.

## Keep the patch bounded

- Implement the smallest vertical slice that proves the requested behavior.
- Avoid broad refactors unless explicitly requested.
- Preserve public numbering and layout unless a migration is part of the task.
- Keep invalid or unsupported states rejectable.
- Preserve bootstrap behavior unless the slice intentionally advances it.
- Prefer explicit states and reason codes over hidden fallback.
- Do not claim runtime support that the code cannot exercise.

## Guard high-risk invariants

For privilege, MM, or process work, fail-stop when restoration of CR3, TSS
`rsp0`, IF, stack ownership, current process, or sealed mappings cannot be
proven. For state machines, inspect every transition and default path. For
autonomy, require a bounded action surface, verifier, and rollback path.

Use `$aios-enum-abi-integrity` for stable numeric contracts and
`$aios-verification-tooling-guardian` whenever evidence or verdict behavior
changes. When the slice adds or changes an externally documented surface — a
syscall, a shell `state` topic, a boot marker, or a selftest line — run the
`$aios-doc-impl-sync` mirror sweep in the same patch: `CLAUDE.md`, `README.md`,
`PROJECT.md`, and the nearest design doc must all move together, not just the
doc closest to the code.

## Verify from narrow to broad

1. formatter, compile, and `git diff --check`
2. relevant host unit tests and static analysis
3. the narrow kernel selftest or QEMU reproduction
4. affected smoke profiles
5. shell/inventory/security/fault lanes when their contracts change

Never describe a timeout-killed VM as a clean exit. Preserve the first failure
and its raw evidence.

## Handoff

Report exact files, the preserved invariant, risk level, commands and results,
rollback behavior, and what remains intentionally incomplete.
