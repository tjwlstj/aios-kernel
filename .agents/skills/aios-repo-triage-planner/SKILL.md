---
name: aios-repo-triage-planner
description: Use when an AIOS request is broad, exploratory, maturity-oriented, or phrased as “what next”. Inspect the current repository and convert the request into a bounded implementation slice tied to an honest verification path without editing when the user asked only for research.
---

# AIOS Repository Triage Planner

Turn broad direction into the nearest useful, verifiable step.

## Orient before proposing

1. Inspect branch, status, recent history, and repository layout.
2. Read `PROJECT.md`, `CLAUDE.md`, `docs/meta/codex_handoff_tips_ko.md`, and the
   current roadmap for the affected domain.
3. Search implementation, public interfaces, tests, CI, and documentation.
4. Separate:
   - already present and verified
   - present but partial or scaffolded
   - missing but implied by the current milestone
   - genuinely new future work
5. If the user requested investigation only, stop before mutations.

## Select the next slice

Prefer one vertical slice that closes a real gap:

- one enum/ABI integrity fix
- one fail-closed verifier rule and negative test
- one QEMU-observable driver checkpoint
- one bounded action with reject and rollback behavior
- one documentation/implementation synchronization pass
- one runtime observation surface with a shell exchange

Avoid selecting a whole milestone when one evidence-bearing seam can be
completed independently.

## Plan in evidence order

For each proposed slice provide:

- current state and evidence
- exact gap
- minimal files/symbols likely involved
- verification lane and expected artifact
- invariant and rollback boundary
- dependencies and risk
- follow-up slices kept separate

Distinguish `CURRENT`, `PARTIAL`, `SCAFFOLD`, `PLANNED`, and optional `RESEARCH`
tracks. Do not let the roadmap imply implementation.

## Route to specialist skills

Choose the smallest relevant companion:

- `$aios-kernel-change-guardian`
- `$aios-enum-abi-integrity`
- `$aios-driver-bringup-qemu`
- `$aios-slm-policy-designer`
- `$aios-verification-tooling-guardian`
- `$aios-doc-impl-sync`

Report assumptions that would materially change the selected slice.
