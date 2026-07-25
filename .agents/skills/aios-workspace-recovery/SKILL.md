---
name: aios-workspace-recovery
description: Use when the AIOS mapped drive, repository folder, shell working directory, or patch tool becomes inaccessible; when Windows reports invalid working directory, access denied, CreateProcessWithLogonW error 267, or an unrelated PowerShell profile failure; or when local and remote repository state must be reconciled after reconnecting.
---

# AIOS Workspace Recovery

Recover access without guessing about local state or using the remote repository
as a destructive fallback.

## Separate failure layers

Classify the symptom before acting:

- **path layer:** mapped drive or repository root is absent
- **process layer:** the command cannot start because its current directory is
  invalid
- **profile layer:** PowerShell startup/profile modules fail before the command
- **permission layer:** the path exists but the current process cannot access it
- **repository layer:** Git opens, but branch, index, worktree, or remote refs
  differ

Do not interpret one layer as proof about another. An inaccessible folder does
not mean the worktree is clean.

## Recover read-only first

1. Start a shell from a known local directory such as `C:\` when the current
   working directory is invalid.
2. Check the mapped drive, project parent, and repository root with
   `Test-Path` and `Resolve-Path`.
3. If the PowerShell profile is the failure, run a no-profile/non-login command
   and keep the profile issue separate.
4. Once Git is reachable, inspect:

   ```powershell
   git -C <repo> status --short --branch
   git -C <repo> log -5 --oneline --decorate
   git -C <repo> remote -v
   ```

5. Do not edit, switch branches, pull, or reset until status is known.

Never request, print, store, or reconstruct SMB credentials. Do not create a new
drive mapping with guessed credentials.

## Use remote state carefully

If local access is still unavailable, remote GitHub inspection may answer
read-only questions about branches, commits, CI, and tracked files. Clearly
label local dirty state, untracked files, and unpublished commits as
**unverified**.

Do not push remote edits as a substitute for an inaccessible local worktree.
Remote state cannot prove that local user work is safe.

## Reconcile after access returns

1. Re-run status and inspect all tracked and untracked changes.
2. Fetch without merging.
3. Compare `HEAD`, upstream, `beta`, and `main` ancestry.
4. Attribute changes before staging; preserve unrelated user work.
5. Run `git diff --check` and the narrow relevant validation before a
   checkpoint.
6. Confirm the final branch and upstream relation explicitly.

Use `$aios-beta-checkpoint-release` only after the local worktree is understood.

## Stop conditions

Stop and report the boundary when:

- local state remains unknowable
- reconnecting requires credentials or OS-level authority not provided
- the worktree contains overlapping changes with uncertain ownership
- branch history diverged and fast-forward safety cannot be proven

Report the recovered path layer, verified Git state, remaining uncertainty, and
the safest next action.
