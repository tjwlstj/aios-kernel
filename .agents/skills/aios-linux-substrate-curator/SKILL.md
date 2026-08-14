---
name: aios-linux-substrate-curator
description: Use when researching, adding, reviewing, or validating Linux substrate references, upstream resource metadata, hosted-backend plans, provenance, SPDX or license boundaries, or proposed Linux code reuse in AIOS. Keep Linux entries source-only, block code import until every license and provenance gate passes, and keep the hosted backend PLANNED until runtime evidence exists.
---

# AIOS Linux Substrate Curator

Keep Linux useful as a traceable upstream reference without silently turning a
resource catalog into imported code or an implemented hosted backend.

## Read the authority first

1. Read `docs/os/linux_hosted_substrate_and_resource_policy_ko.md` completely.
2. Inspect `tools/platform/resources/linux_substrate_resources.json` as the
   canonical machine-readable resource set.
3. Inspect the affected repository paths and current maturity evidence.
4. Run `py -3 tools/platform/linux_resource_guard.py` before accepting or
   reporting a resource change as valid.

If the document, manifest, or guard is missing, malformed, or contradictory,
stop the affected advancement and report the exact missing authority. Do not
invent a replacement schema or relax the guard.

If the canonical document still contains unresolved baseline tokens, treat H0
exact-baseline closure as incomplete. Do not use that document as release
evidence or guess the missing schema, action, marker, or verifier values.

## Classify the request

Label the work before changing anything:

- `SOURCE_CURATION`: add or review URLs, revisions, hashes, license metadata,
  SPDX identifiers, provenance, and intended reference use.
- `HOSTED_DESIGN`: describe the intended default Linux-hosted userspace-service
  boundary without claiming that the backend exists.
- `IMPORT_PROPOSAL`: evaluate copying, vendoring, translating, generating from,
  or patching upstream code. Treat this as blocked until every import gate is
  explicitly satisfied.
- `AIOS_IMPLEMENTATION`: change native AIOS code. Route the implementation to
  the appropriate kernel, ABI, verification, or documentation skill in
  addition to this provenance boundary.

Do not let a source entry automatically authorize a hosted design or code
import. Do not let a hosted design imply a native-kernel milestone.

For roadmap work, treat the bounded native K2-a semantic oracle as `CURRENT`
and the wider K2 live lifecycle/reconciliation path as `PARTIAL`. Build H1
replay semantics as the next independent slice. Start H2 only after the H1
lifecycle/generation/reject contract, fail-closed fixtures, and Windows/Ubuntu
replay verdicts pass. Broad native process/storage expansion or full
conformance completion is not a prerequisite for the first observe-only hosted
slice. Keep H4/H5 blocked behind K5 principal, ownership,
authorize, and separate approval. Implementation convenience is not a binding
rule: PID, cgroup, Memory Fabric domain, and process generation cannot stand in
for an `AI_SERVICE` Node without a semantic-kind and producer-owned-generation
gate.

## Preserve the source-only boundary

- Treat every Linux identity in the resource manifest as source metadata only.
- Put product runtime under `hosted/`; keep `tools/platform/` limited to the
  manifest and guard.
- Use exact upstream identity and immutable revision evidence where the
  canonical schema requires it.
- Keep downloaded archives, source trees, patches, generated derivatives, and
  vendored code outside AIOS implementation paths until import is separately
  authorized.
- Do not copy implementation snippets into AIOS as a shortcut around the
  manifest or provenance process.
- Do not infer compatibility or permission from popularity, public visibility,
  a repository name, or a license filename alone.
- Preserve the distinction between reading an upstream design and deriving or
  distributing code from it.

The resource guard validates the checked-in contract; it does not by itself
grant license compatibility, import approval, or runtime support.

## Gate every import proposal

Keep code import blocked until the canonical policy's complete gate is
evidenced, including at least:

- exact upstream project, URL, revision, and integrity identity
- license review under the repository's stated compatibility policy
- required SPDX and copyright preservation
- provenance from upstream material to every imported or derived file
- an explicit bounded destination, ownership, update, and rollback plan
- review and verification appropriate to the code path being changed

If any gate is unknown or only planned, return a source-only or design-only
result. Never downgrade an unknown field to an empty value that passes by
omission.

## Keep maturity honest

- Resource policy, manifest rows, and guard behavior may be `CURRENT` only when
  their checked-in evidence and regular validation agree.
- A Linux-hosted backend remains `PLANNED` until executable integration and its
  stated verification lane exist.
- Upstream references are not AIOS driver, syscall, scheduler, process, Cell,
  Node, or NodeBit support.
- Research comparisons may be `RESEARCH`; they do not advance implementation
  maturity.

Report the exact boundary as `CURRENT`, `PARTIAL`, `SCAFFOLD`, `PLANNED`, or
`RESEARCH`. Never describe source discovery as implementation progress.

## Verify and hand off

For a resource or policy change:

1. Run `py -3 tools/platform/linux_resource_guard.py`.
2. Run `py -3 -m unittest discover -s tools/platform/tests -p "test_*.py" -v`.
3. Treat a nonzero exit, missing input, unknown field, duplicate identity,
   mutable reference, or provenance/license failure as blocking.
4. Run the narrow documentation and testkit checks required by the changed
   surface; do not update unrelated baselines.
5. Report which resources are source-only, which import gates remain open, and
   that the hosted backend remains `PLANNED` unless separately proven.

Use `$aios-doc-impl-sync` when maturity or guide text changes,
`$aios-verification-tooling-guardian` when the guard or its CI verdict changes,
and `$aios-kernel-change-guardian` plus `$aios-enum-abi-integrity` for any later
native kernel or public numeric contract. This skill never supplies import
authorization by itself.
