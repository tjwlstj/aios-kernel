#!/usr/bin/env python3
"""Evaluate a userspace policy decision from an SLM NodeBit catalog."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


NODEBIT_F_PRESENT = 1 << 0
NODEBIT_F_USER_VISIBLE = 1 << 1
NODEBIT_F_OBSERVE_ONLY = 1 << 2
NODEBIT_F_APPLY_ALLOWED = 1 << 3
NODEBIT_F_RISKY = 1 << 4
NODEBIT_F_REQUIRES_MEDIATION = 1 << 5
NODEBIT_F_RUNTIME_READY = 1 << 6

ACTION_IDS = {
    "SLM_ACTION_NONE": 0,
    "SLM_ACTION_REPROBE_PCI": 1,
    "SLM_ACTION_BOOTSTRAP_E1000": 2,
    "SLM_ACTION_E1000_TX_SMOKE": 3,
    "SLM_ACTION_E1000_DUMP": 4,
    "SLM_ACTION_BOOTSTRAP_USB": 5,
    "SLM_ACTION_USB_DUMP": 6,
    "SLM_ACTION_BOOTSTRAP_STORAGE": 7,
    "SLM_ACTION_STORAGE_DUMP": 8,
    "SLM_ACTION_IO_AUDIT": 9,
    "SLM_ACTION_CORE_AUDIT": 10,
    "SLM_ACTION_E1000_RX_POLL": 11,
}
SLM_ACTION_NONE = ACTION_IDS["SLM_ACTION_NONE"]
SLM_ACTION_COUNT = 12
SUPPORTED_SCHEMA = "aios.nodebit.catalog.v1"

BOOTSTRAP_ACTIONS = {
    ACTION_IDS["SLM_ACTION_BOOTSTRAP_E1000"],
    ACTION_IDS["SLM_ACTION_BOOTSTRAP_USB"],
    ACTION_IDS["SLM_ACTION_BOOTSTRAP_STORAGE"],
}


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def action_id(value: str) -> int:
    try:
        return int(value, 0)
    except ValueError:
        pass

    key = value.upper()
    if key not in ACTION_IDS:
        names = ", ".join(sorted(ACTION_IDS))
        raise ValueError(f"unknown action {value!r}; expected one of: {names}")
    return ACTION_IDS[key]


def action_name(value: int) -> str:
    for name, action in ACTION_IDS.items():
        if action == value:
            return name
    return f"SLM_ACTION_{value}"


def node_kind(node: dict) -> str:
    kind = node.get("kind", "unknown")
    if isinstance(kind, int):
        return {
            1: "api",
            2: "tool",
            3: "device",
            4: "memory",
            5: "clock",
            6: "policy",
        }.get(kind, "unknown")
    return str(kind)


def support_state(flags: int) -> str:
    if (flags & NODEBIT_F_PRESENT) == 0:
        return "absent"
    if (flags & NODEBIT_F_RUNTIME_READY) != 0:
        return "ready"
    return "degraded"


def find_node(catalog: dict, node_id: int) -> dict | None:
    for node in catalog.get("nodes", []):
        if int(node.get("node_id", -1)) == node_id:
            return node
    return None


def catalog_risky_io_allowed(catalog: dict, allow_risky: bool) -> bool:
    return bool(allow_risky or catalog.get("risky_io_allowed", False))


def deny(reason: str, *, node: dict | None, requested_node_id: int,
         action: int, mode: str, catalog: dict, allow_risky: bool) -> dict:
    flags = int(node.get("flags", 0)) if node else 0
    return {
        "decision": "deny",
        "reason": reason,
        "mode": mode,
        "requested_node_id": requested_node_id,
        "node_id": int(node.get("node_id", requested_node_id)) if node else requested_node_id,
        "parent_id": int(node.get("parent_id", 0)) if node else 0,
        "node_name": node.get("name", "unknown") if node else "unknown",
        "kind": node_kind(node or {}),
        "support_state": support_state(flags),
        "action": action_name(action),
        "action_id": action,
        "runtime_state": str(catalog.get("runtime_state", "unknown")),
        "risky_io_allowed": catalog_risky_io_allowed(catalog, allow_risky),
        "latency_class": int(node.get("latency_class", 0)) if node else 0,
        "generation": int(catalog.get("nodebit_generation", 0)),
    }


def evaluate(catalog: dict, node_id: int, action: int, mode: str, allow_risky: bool) -> dict:
    schema = catalog.get("schema")
    if schema is not None and schema != SUPPORTED_SCHEMA:
        return deny("unsupported_schema", node=None, requested_node_id=node_id,
                    action=action, mode=mode, catalog=catalog, allow_risky=allow_risky)

    if action <= SLM_ACTION_NONE or action >= SLM_ACTION_COUNT:
        return deny("invalid_action", node=None, requested_node_id=node_id,
                    action=action, mode=mode, catalog=catalog, allow_risky=allow_risky)

    runtime_state = str(catalog.get("runtime_state", "unknown")).lower()
    if runtime_state not in {"ready", "bootstrap", "degraded"}:
        return deny("runtime_unavailable", node=None, requested_node_id=node_id,
                    action=action, mode=mode, catalog=catalog, allow_risky=allow_risky)

    node = find_node(catalog, node_id)
    if node is None:
        return deny("node_missing", node=None, requested_node_id=node_id,
                    action=action, mode=mode, catalog=catalog, allow_risky=allow_risky)

    flags = int(node.get("flags", 0))
    action_bit = 1 << action
    action_bits = int(node.get("action_bits", 0))
    allow_bits = int(node.get("allow_bits", 0))
    observe_bits = int(node.get("observe_only_bits", 0))
    risky_bits = int(node.get("risky_bits", 0))
    required_capability_bits = int(node.get("required_capability_bits", 0))
    capability_flags = int(catalog.get("capability_flags", 0))
    kind = node_kind(node)
    risk_level = int(node.get("risk_level", 0))
    risky_io_allowed = catalog_risky_io_allowed(catalog, allow_risky)

    if (flags & NODEBIT_F_PRESENT) == 0:
        return deny("node_absent", node=node, requested_node_id=node_id,
                    action=action, mode=mode, catalog=catalog, allow_risky=allow_risky)
    if (flags & NODEBIT_F_USER_VISIBLE) == 0:
        return deny("node_hidden", node=node, requested_node_id=node_id,
                    action=action, mode=mode, catalog=catalog, allow_risky=allow_risky)
    if required_capability_bits and (capability_flags & required_capability_bits) != required_capability_bits:
        return deny("missing_capability", node=node, requested_node_id=node_id,
                    action=action, mode=mode, catalog=catalog, allow_risky=allow_risky)
    if (action_bits & action_bit) == 0:
        return deny("unsupported_action", node=node, requested_node_id=node_id,
                    action=action, mode=mode, catalog=catalog, allow_risky=allow_risky)

    risky = ((flags & NODEBIT_F_RISKY) != 0) or ((risky_bits & action_bit) != 0)
    if mode == "apply":
        if (risky or risk_level > 0) and not risky_io_allowed:
            return deny("risky_apply_blocked", node=node, requested_node_id=node_id,
                        action=action, mode=mode, catalog=catalog, allow_risky=allow_risky)
        if (flags & NODEBIT_F_APPLY_ALLOWED) == 0:
            return deny("apply_flag_missing", node=node, requested_node_id=node_id,
                        action=action, mode=mode, catalog=catalog, allow_risky=allow_risky)
        if (allow_bits & action_bit) == 0:
            return deny("apply_not_allowed", node=node, requested_node_id=node_id,
                        action=action, mode=mode, catalog=catalog, allow_risky=allow_risky)
        if kind == "device" and (flags & NODEBIT_F_RUNTIME_READY) == 0 and action not in BOOTSTRAP_ACTIONS:
            return deny("node_not_ready", node=node, requested_node_id=node_id,
                        action=action, mode=mode, catalog=catalog, allow_risky=allow_risky)
    else:
        if ((observe_bits | allow_bits) & action_bit) == 0:
            return deny("observe_not_allowed", node=node, requested_node_id=node_id,
                        action=action, mode=mode, catalog=catalog, allow_risky=allow_risky)

    return {
        "decision": "allow",
        "reason": "matched_nodebit",
        "mode": mode,
        "requested_node_id": node_id,
        "node_id": int(node["node_id"]),
        "parent_id": int(node.get("parent_id", 0)),
        "node_name": node.get("name", "unknown"),
        "kind": kind,
        "support_state": support_state(flags),
        "action": action_name(action),
        "action_id": action,
        "risky": risky,
        "risk_level": risk_level,
        "required_capability_bits": required_capability_bits,
        "runtime_state": str(catalog.get("runtime_state", "unknown")),
        "risky_io_allowed": risky_io_allowed,
        "latency_class": int(node.get("latency_class", 0)),
        "generation": int(catalog.get("nodebit_generation", 0)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", required=True, type=Path, help="NodeBit catalog JSON")
    parser.add_argument("--node-id", required=True, type=int, help="NodeBit ID to evaluate")
    parser.add_argument("--action", required=True, help="SLM action name or numeric ID")
    parser.add_argument("--mode", choices=["observe", "apply"], default="observe")
    parser.add_argument("--allow-risky", action="store_true")
    parser.add_argument("--output", type=Path, help="Optional output JSON path")
    args = parser.parse_args()

    catalog = load_json(args.catalog)
    try:
        action = action_id(args.action)
    except ValueError as exc:
        parser.error(str(exc))
    result = evaluate(catalog, args.node_id, action, args.mode, args.allow_risky)

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
