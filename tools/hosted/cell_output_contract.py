"""Independent replay of bounded hosted Cell 1 management transitions.

Cell lifecycle changes management validity. It neither owns nor terminates
the MAIN process, model backend or source lifecycle.
"""
from __future__ import annotations

import copy

from newagent_output_contract import same, validate_snapshot

MAX_GENERATION = (1 << 63) - 1
ACTIONS = ("cell-status", "cell-activate", "cell-deactivate")


def require(condition, reason):
    if not condition:
        raise ValueError("cell_contract:" + reason)


def _invalidated(previous):
    expected = copy.deepcopy(previous)
    expected.update(source_trusted=False, discovered_source=None, binding_confirmed=False,
                    binding_valid=False, binding_current=False, bound_nodes=0)
    expected["state"] = ("UNINITIALIZED" if not previous["initialized"] else
                         "STALE" if previous["binding"] is not None else "UNBOUND")
    if previous["initialized"]:
        for bit in expected["nodebits"]:
            bit["parent_node_generation"] = expected["canonical"]["generation"]
            bit["value"] = False
            bit["valid"] = bit["id"] == 1002
    return expected


def validate_parent_continuity(previous, snapshot):
    """Non-Cell events cannot silently change parent or canonical identity."""
    validate_snapshot(previous)
    validate_snapshot(snapshot)
    require(all(same(previous[key], snapshot[key]) for key in
                ("authority_namespace", "authority_instance", "initialized", "parent", "canonical")),
            "unexpected_parent_transition")


def validate_cell_transition(previous, snapshot, action, error):
    """Compare the complete result to the one permitted explicit transition."""
    validate_snapshot(previous)
    validate_snapshot(snapshot)
    require(action in ACTIONS, "action")
    if action == "cell-status":
        require(error is None and same(previous, snapshot), "status_changed")
        return
    active = action == "cell-activate"
    if not previous["initialized"]:
        require(error == "init-order" and same(snapshot, _invalidated(previous)), "init_order")
        return
    if previous["parent"]["active"] is active:
        require(error is None and same(previous, snapshot), "idempotent_changed")
        return
    if previous["parent"]["generation"] == MAX_GENERATION:
        require(error == "overflow" and same(snapshot, _invalidated(previous)), "overflow_changed")
        return
    require(error is None, "unexpected_error")
    expected = copy.deepcopy(previous)
    expected["parent"]["active"] = active
    expected["parent"]["generation"] += 1
    expected["canonical"]["generation"] += 1
    expected = _invalidated(expected)
    require(same(snapshot, expected), "transition_changed")
