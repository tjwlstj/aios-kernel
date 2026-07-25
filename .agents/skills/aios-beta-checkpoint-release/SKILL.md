---
name: aios-beta-checkpoint-release
description: Use when the user asks to checkpoint, commit, push, publish, synchronize beta and main, or release current AIOS work. Stage intentionally, validate by change risk, publish to beta first, inspect the exact beta commit and CI, then move main only by fast-forward to that verified same SHA with explicit authorization.
---

# AIOS Beta-first Checkpoint and Release

Preserve a single auditable commit identity from local validation through
`beta` and, when authorized, `main`.

## Confirm authority and scope

- A request to review or validate does not authorize commits or pushes.
- A request to checkpoint or publish authorizes only the named repository and
  branch flow.
- Moving `main` requires explicit authorization. A same-turn instruction such
  as “check beta, then put the same result on main” is sufficient and should
  not be asked again.
- Never include unrelated user changes merely because they are present.

## Inspect before staging

1. Run `git status --short --branch`, `git branch -vv`, and recent log.
2. Fetch remote refs without merging.
3. Inspect every tracked and untracked file in scope.
4. Run `git diff --check`.
5. Separate logical changes into intentional commits when it improves rollback.
6. Confirm the current branch and upstream before writing history.

Never use force push, destructive reset, or a merge commit for this flow.

## Select verification by risk

### Documentation or project skills only

- validate each changed skill with the repository-supported skill validator
- check links, maturity wording, code fences, and generated metadata
- run `git diff --check`

Do not claim kernel/QEMU coverage from a prose-only change.

### Testkit, evidence, marker, state, baseline, or CI changes

- use `$aios-verification-tooling-guardian`
- run host verdict tests and PowerShell selftests
- run affected QEMU profiles, shell, and inventory lanes
- preserve artifacts and exact termination results

### Kernel, runtime, public header, ABI, or driver changes

- use `$aios-kernel-change-guardian`
- add `$aios-enum-abi-integrity` or `$aios-driver-bringup-qemu` when triggered
- run compile/static checks, the narrow selftest, and affected QEMU matrix

Record every unrun lane.

## Create the local checkpoint

1. Stage exact paths or reviewed hunks.
2. Review `git diff --cached --stat` and `git diff --cached`.
3. Commit with an imperative message describing one logical outcome.
4. Verify `git status`, commit contents, and parentage.
5. Keep the verified commit SHA as the release identity.

If work began on another branch, integrate into `beta` only with a safe,
reviewed fast-forward or explicitly approved method. Do not switch branches
through an unknown dirty worktree.

## Publish and verify beta first

1. Push the checkpoint chain to `origin/beta`.
2. Verify local `beta`, `origin/beta`, and the intended release SHA agree.
3. Inspect the remote commit and required GitHub Actions run for that SHA.
4. Wait for a terminal result; queued or in-progress is not verified.
5. If CI fails, keep `main` unchanged, preserve failure evidence, fix on
   `beta`, and establish a new release SHA.

## Fast-forward main to the same SHA

Proceed only after beta evidence passes and main authority is present:

1. Fetch remote refs again.
2. Prove `origin/main` is an ancestor of the verified `origin/beta`.
3. Update local `main` with `git merge --ff-only beta`.
4. Push `main` without force.
5. Verify:

   ```text
   origin/main == origin/beta == verified release SHA
   ```

Do not cherry-pick, squash, rebase, or create a merge commit between verified
beta and main; those operations create a different release identity.

## Finish on beta

Switch back to `beta`, fetch, and report:

- commit list and verified SHA
- local validation
- beta push and CI result
- main fast-forward result or reason it remained unchanged
- final branch/upstream/cleanliness

Leave `beta` as the default working branch for the next slice.
