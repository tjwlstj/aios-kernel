---
name: aios-enum-abi-integrity
description: Use when adding or changing AIOS enums, syscall numbers, action or reason codes, target IDs, dtype tags, controller kinds, state values, telemetry fields, or any numeric constant that crosses files, logs, persistence, or ABI boundaries.
---

# AIOS Enum and ABI Integrity

Prevent silent numeric, layout, and interpretation drift.

## Treat escaped values as contracts

Assume a value is stable when it appears outside one private implementation
file, including in:

- public headers and syscalls
- `switch` statements and indexed arrays
- string/name tables
- serial logs, state topics, telemetry, and artifacts
- fixtures, baselines, parsers, and documentation

## Audit before editing

1. Record current explicit and implicit numbering.
2. Use `rg` to find every producer and consumer.
3. Inspect `_COUNT`, `_MAX`, validity helpers, tables, serialization, and
   default cases.
4. Decide whether the change is append-only, a reserved-value fill, or a real
   migration.
5. State compatibility and rollback impact before patching.

## Prefer stable patterns

- Give externally visible values explicit numbers.
- Append new stable values; do not insert into the middle casually.
- Keep `*_UNKNOWN` or `*_INVALID` for parsed external values.
- Use `*_COUNT` only for dense indexable ranges.
- Add `_Static_assert` for table length, layout, or boundary coupling.
- Reject unknown input explicitly instead of mapping it silently.
- Version schemas when meaning changes without a safe append-only encoding.

## Verify the full contract

Update and test all coupled sites in one patch: header, implementation,
validation, name table, parser, fixtures, state output, and docs. "Docs" means
every mirror of the value — top-level `CLAUDE.md`, `README.md`, and
`PROJECT.md` included, not only the nearest design document; the
`$aios-doc-impl-sync` mirror-surface table lists the coupled sites. Include a
negative test for unknown or out-of-range values when externally supplied.

Use `$aios-kernel-change-guardian` for kernel/public-header changes and
`$aios-verification-tooling-guardian` when a verifier observes the value.

## Handoff

Report the previous numbering, resulting numbering, compatibility decision,
assertions/tests added, and any consumer that remains intentionally versioned
or unsupported.
