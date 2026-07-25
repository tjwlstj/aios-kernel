---
name: aios-doc-impl-sync
description: Use when updating AIOS README files, design documents, gap reports, architecture notes, handoff notes, or roadmaps. Ground every maturity claim in current code and verification evidence, and keep implemented, partial, scaffolded, planned, and research states distinct.
---

# AIOS Documentation and Implementation Sync

Prevent optimism drift between AIOS documentation and the repository.

## Establish the source of truth

1. Inspect `git status --short --branch` and preserve unrelated changes.
2. Read `CLAUDE.md`, `PROJECT.md`, `docs/meta/codex_handoff_tips_ko.md`, and the
   nearest subsystem design.
3. Locate the implementation, public headers, host verifier, CI lane, and
   generated artifact that support the claim.
4. Search every document that repeats the changed status, command, marker, ID,
   or milestone.
5. Record mismatches before editing prose.

## Use bounded maturity labels

- `CURRENT`: implemented and exercised by the stated regular verification path.
- `PARTIAL`: implemented with a material platform, verdict, or coverage limit.
- `SCAFFOLD`: an interface or manual path exists without a regular contract.
- `PLANNED`: no implementation exists yet.
- `RESEARCH`: an optional hypothesis or experiment, not an implementation
  maturity level.

Do not promote a feature to `CURRENT` from declarations, stubs, probe logs, or a
single happy-path observation. Name the missing evidence explicitly.

## Edit with implementation precision

- Name concrete files, symbols, commands, profiles, and artifacts.
- Separate current behavior from future architecture in different sections.
- State supported and unsupported targets rather than describing the union as
  complete.
- Keep bootstrap, probe, init, data path, and production support distinct.
- Preserve historical documents when useful; mark them OLD/REVIEW and point to
  the current source of truth.
- Update navigation indexes when adding a canonical document.

## Verify the synchronization

1. Re-run the narrow evidence path behind the edited claim when practical.
2. Check internal links and repository-relative paths.
3. Search for stale wording and contradictory maturity labels.
4. Run `git diff --check`.
5. Report the evidence used, claims intentionally left partial, and any
   verification lane not run.
