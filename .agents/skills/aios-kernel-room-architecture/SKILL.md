---
name: aios-kernel-room-architecture
description: Use when planning, documenting, reviewing, or implementing AIOS Kernel Room, Cell, Node, NodeBit, Axis Gate, Orbit, ownership, binding, resource attribution, or related roadmap work. Preserve the Room to Cell to Node to NodeBit management hierarchy, keep execution substrate separate from product direction, and require identity and observation before policy enforcement.
---

# AIOS Kernel Room Architecture

Keep Kernel Room work centered on the management model rather than allowing
kernel mechanisms or syscall enforcement to become the architecture by default.

## Read the canon first

1. Read `docs/kernel-room/kernel_room_management_model_ko.md` completely.
2. Read `docs/kernel-room/development_guide_ko.md` completely.
3. Inspect the affected implementation and its current maturity evidence.
4. Treat older Kernel Room feasibility/topology documents as historical context
   when they carry a `REVIEW` banner; do not let them override the canon.

## Preserve the hierarchy

Use these meanings consistently:

- `Kernel Room`: authoritative management view of identities, relationships,
  lifecycle, generation, and aggregate state.
- `Cell`: primary bounded management and isolation domain.
- `Node`: logical execution, service, or resource subject bound to a Cell.
- `NodeBit`: fine-grained state, capability, eligibility, or validity fact that
  belongs to one Node.
- `Axis Gate`: a later transition and security boundary that consumes canonical
  state; it is not the identity of Kernel Room.
- `Orbit`: a derived placement or distance view; keep it `RESEARCH` until a
  separately justified runtime exists.

Never infer identity from equal numeric IDs across Memory Fabric, SLM agent
profiles, SLM policy nodes, runtime NodeBit, pipelines, scheduler tasks,
processes, or rings. Require an explicit namespace and binding.

## Apply the direction gate

Before selecting work, label its relationship to the management model:

- `DIRECT`: creates or validates a Room, Cell, Node, NodeBit, or binding.
- `SUPPORTING`: improves execution substrate required by an already named
  management milestone.
- `ORTHOGONAL`: useful maintenance that does not advance the hierarchy.
- `RESEARCH`: optional exploration with no maturity implication.

Do not present `SUPPORTING` kernel work as the next product milestone by itself.
If no `DIRECT` management milestone exists, return to the canonical work plan
before expanding process, scheduler, driver, or enforcement breadth.

## Follow the implementation order

1. Define stable identity, namespace, lifecycle, generation, validity, and
   ownership contracts in documentation.
2. Add one bounded read-only hierarchy registry with at least one Cell, one
   Cell-bound Node, and one or more parent-bound NodeBits. Keep the whole v0
   slice `management_only=1`; a Cell-only table is not hierarchy completion.
3. Expand explicit Node-to-Cell binding and reject orphan, duplicate, or stale
   identities.
4. Project selected legacy NodeBit sources with namespace, source generation,
   and parent validity; do not merge their source tables.
5. Attribute pipelines, rings, tasks, resources, and pressure without changing
   scheduling, quota, or policy.
6. Add proposal and validation paths with stale-generation rejection.
7. Add authorize or enforcement only after principal, ownership, binding,
   operation class, revalidation, and rollback contracts are verified.

Keep resource and pressure paths `observation_only=1` until their own apply UAPI
and rollback verifier are separately authorized and proven.

## Keep current claims honest

- Treat the existing Room aggregate snapshot and gate descriptors as `CURRENT`
  observation/classification metadata only.
- Treat the current SLM agent tree, runtime NodeBit registry, SLM policy-node
  catalog, and Node-owned pipeline as independent partial or scaffolded sources,
  not one canonical Node graph.
- Keep Cell registry/lifecycle, canonical Node binding, integrated NodeBit view,
  per-Cell or per-Node attribution, and Room-wide enforcement `PLANNED` until
  their exact evidence exists.
- Keep Orbit runtime `RESEARCH`.

## Verify the smallest vertical slice

Prefer one bounded hierarchy proof before broad integration. Require:

- unique Cell, Node, and NodeBit identities within explicit namespaces
- every Node bound to an existing Cell
- every NodeBit bound to an existing parent Node
- source binding and generation validity
- fail-closed rejection of orphan, duplicate, unknown, and stale records
- no scheduler, resource, capability, or hardware mutation in a management-only
  slice

When code changes add evidence, use `$aios-verification-tooling-guardian`. When
IDs or enums change, use `$aios-enum-abi-integrity`. For kernel implementation,
use `$aios-kernel-change-guardian`; for mirrored documentation, use
`$aios-doc-impl-sync`; for authorize, policy, proposal, or apply design, use
`$aios-slm-policy-designer`.

## Reject direction drift

Do not:

- equate Cell with a convenience tuple of existing subsystem objects before its
  lifecycle and ownership semantics are defined
- merge Axis Gate and NodeBit merely because both affect policy
- merge the two existing NodeBit namespaces by numeric coincidence
- add per-syscall enforcement before caller identity and target ownership exist
- add a Room-level sequence counter and claim cross-subsystem atomicity unless
  every source participates in a defined consistency protocol
- promote bootstrap fixtures, aggregate counts, or declared profiles into live
  managed Nodes
