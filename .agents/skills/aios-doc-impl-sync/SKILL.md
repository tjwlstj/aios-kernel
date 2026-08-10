---
name: aios-doc-impl-sync
description: Use when updating AIOS README files, design documents, gap reports, architecture notes, handoff notes, or roadmaps — and, in the reverse direction, whenever a code change adds, renames, or promotes a documented surface (syscall, shell state topic, boot marker, selftest line, maturity label). Ground every maturity claim in current code and verification evidence, keep implemented, partial, scaffolded, planned, and research states distinct, and move every mirror document in the same patch.
---

# AIOS Documentation and Implementation Sync

Prevent optimism drift between AIOS documentation and the repository — in both
directions. Docs must not claim more than the code proves, and the code must not
ship surfaces the top-level docs still deny. The canonical failure this skill
exists to stop: commit a03b3c2 (2026-08-02) shipped `SYS_INFO_RESOURCE` and
`state resource`, updated `PROJECT.md` and `docs/`, but left `CLAUDE.md` and
`README.md` claiming neither existed.

## Establish the source of truth

1. Inspect `git status --short --branch` and preserve unrelated changes.
2. Read `CLAUDE.md`, `PROJECT.md`, `docs/meta/codex_handoff_tips_ko.md`, and the
   nearest subsystem design.
   For Kernel Room, Cell, Node, NodeBit, Axis Gate, Orbit, or attribution
   claims, also read `docs/kernel-room/kernel_room_management_model_ko.md` and
   use `$aios-kernel-room-architecture`.
3. Locate the implementation, public headers, host verifier, CI lane, and
   generated artifact that support the claim.
4. Search every document that repeats the changed status, command, marker, ID,
   or milestone.
5. Record mismatches before editing prose.

## Sweep the mirror surfaces (same patch, no exceptions)

Some facts are intentionally duplicated across files. When any one copy changes,
update every mirror in the same patch:

| Surface | Coupled sites |
|---|---|
| Shell `state` topic list | `kernel/core/shell.c` (`state_list`) · `CLAUDE.md` "Interactive Shell Lane" · `tools/testkit/lib/shell_lane.py` `DEFAULT_EXCHANGES` · `docs/tools/testkit_guide_ko.md` |
| Syscall existence and maturity | `kernel/include/runtime/ai_syscall.h` · the subsystem bullet in `CLAUDE.md` · `PROJECT.md` §5 invariants · `README.md` "Current Status" · the nearest design doc |
| Boot markers / selftest PASS lines | kernel source emitting the line · `CLAUDE.md` "Smoke Test Checkpoints" · testkit anchors (`boot_log.py`, `shell_lane.py`, `EXACT_REQUIRED_RECORDS`) |
| Kernel Room gate ranges | `kernel/core/kernel_room.c` · the gate-coverage bullets in `CLAUDE.md` and `PROJECT.md` |
| Kernel Room hierarchy and direction | `docs/kernel-room/kernel_room_management_model_ko.md` · `docs/kernel-room/development_guide_ko.md` · `CLAUDE.md` · `README.md` · `PROJECT.md` · handoff/current roadmap |
| Maturity labels (`CURRENT`/`PARTIAL`/`SCAFFOLD`/`PLANNED`) | `README.md` · `PROJECT.md` · `CLAUDE.md` · design docs must agree for the same feature |

Before finishing, `rg` the *previous* wording of the changed claim (for example
the old topic list string, "exists yet", or a stale `PLANNED`) across
`CLAUDE.md`, `README.md`, `PROJECT.md`, and `docs/` and resolve every hit.

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
- Keep execution substrate maturity separate from direct progress on the
  `Room -> Cell -> Node -> NodeBit` management model.
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
