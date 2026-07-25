---
name: aios-slm-policy-designer
description: Use when designing or editing AIOS autonomy, SLM policy actions, support matrices, verifier flows, telemetry frames, model control surfaces, or low-level AI action paths. Favor bounded structured proposals, observe-before-apply behavior, and rollback-first design.
---

# AIOS SLM Policy Designer

Keep model influence narrower than the kernel risk boundary.

## Place intelligence at the right layer

- Keep free-form LLM reasoning, context, memory, and orchestration in
  userspace or a host runtime.
- Keep the kernel surface deterministic, typed, bounded, and auditable.
- Treat an in-kernel model as an optional bounded policy component, not a
  general code writer or direct hardware operator.

## Define the action contract

Specify fixed fields such as:

- `schema`
- `target`
- `action`
- `delta` or bounded parameters
- `risk_level`
- `reason`
- `support_state`
- `verifier_requirement`
- `rollback_token`

Use enum-backed actions, clampable ranges, explicit unsupported-target
rejection, and versioned schemas. Never generate raw pointers, register writes,
or unbounded command text.

## Separate phases

1. Observe current state through a stable snapshot or `state <topic>`.
2. Propose without mutating.
3. Validate schema, support matrix, policy, and bounds.
4. Apply one reversible action.
5. Measure before/after evidence.
6. Commit or roll back explicitly.
7. Record the decision and outcome for audit.

Do not silently turn observe-only or unsupported behavior into an apply path.

## Design failure first

Define rejection, timeout, partial-apply, stale-state, verifier-failure, and
rollback-failure behavior before the happy path. Make unsupported targets
visible and safe.

## Verify and document

Use `$aios-enum-abi-integrity` for action/reason IDs,
`$aios-kernel-change-guardian` for low-level implementation, and
`$aios-verification-tooling-guardian` for evidence and verdict changes. Keep
declared targets separate from actionable targets in docs.
