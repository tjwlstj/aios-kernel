"""Bounded, copied AI_SERVICE discovery and explicit binding reconciliation.

This module owns management relationships, never processes, models or resources.
The caller supplies a live authenticated producer read and serializes access and
private persistence. A JSON claim alone does not prove the producer's warmup.
Native K1 IDs are semantic counterparts scoped by our separate authority UUID;
no native registry, ABI or frozen H1 trace is imported or changed.
"""
from __future__ import annotations

import copy
import json
import re
import uuid

SCHEMA_VERSION = 1
AUTHORITY_NAMESPACE = "aios-hosted-management"
SOURCE_NAMESPACE = "linux-userspace-service"
CELL_ID = 1
NODE_ID = 101
PRESENT_ID = 1001
SOURCE_BOUND_ID = 1002
MAX_GENERATION = (1 << 63) - 1
MAX_RETIRED = 64
MAX_JSON = 65536
SOURCE_KEYS = frozenset({
    "schema_version", "source_namespace", "source_id", "source_instance",
    "source_generation", "service_start_generation", "source_kind", "source_role",
    "lifecycle_state", "producer_owned", "copied_read", "model_ready",
    "model_sha256", "warmup_request_sha256", "warmup_response_sha256",
    "completed_requests", "host_boot_id", "process_id", "source_only",
})
REASONS = frozenset({
    "none", "init-order", "missing", "schema", "overflow", "duplicate", "orphan",
    "namespace", "kind", "role", "instance", "zero-generation",
    "generation-rollback", "stale", "model-not-ready", "source-exited",
    "not-discovered", "already-bound", "retired-instance", "counter-regression",
    "unbound",
})
_HASH = re.compile(r"[0-9a-f]{64}\Z")


class BindingError(ValueError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _require(condition: bool, reason: str = "schema") -> None:
    if not condition:
        raise BindingError(reason)


def _keys(value: object, keys: set | frozenset) -> None:
    _require(type(value) is dict and value.keys() == keys)


def _integer(value: object, *, positive: bool = True) -> None:
    _require(type(value) is int)
    _require(0 <= value <= MAX_GENERATION, "overflow")
    if positive:
        _require(value > 0, "zero-generation")


def _uuid(value: object) -> None:
    try:
        _require(type(value) is str)
        parsed = uuid.UUID(value)
        _require(str(parsed) == value and parsed.int != 0, "instance")
    except (ValueError, AttributeError) as exc:
        if isinstance(exc, BindingError):
            raise
        raise BindingError("instance") from exc


def _hash(value: object) -> None:
    _require(type(value) is str and _HASH.fullmatch(value) is not None)


def validate_source(value: object) -> dict:
    """Check the exact producer contract; return an independent copied record."""
    if value is None:
        raise BindingError("missing")
    _keys(value, SOURCE_KEYS)
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1)
    for name, expected, reason in (("source_namespace", SOURCE_NAMESPACE, "namespace"),
                                   ("source_kind", "ai-service", "kind"),
                                   ("source_role", "main", "role")):
        _require(value[name] == expected, reason)
    for name in ("source_id", "source_instance", "host_boot_id"):
        _uuid(value[name])
    for name in ("source_generation", "service_start_generation", "process_id"):
        _integer(value[name])
    _integer(value["completed_requests"], positive=False)
    _require(value["lifecycle_state"] in ("active", "exited"))
    for name in ("producer_owned", "copied_read", "source_only"):
        _require(value[name] is True)
    _require(type(value["model_ready"]) is bool)
    for name in ("model_sha256", "warmup_request_sha256", "warmup_response_sha256"):
        if value[name] is not None:
            _hash(value[name])
    warmup = value["warmup_request_sha256"] is not None
    _require(warmup == (value["warmup_response_sha256"] is not None))
    _require(warmup == (value["completed_requests"] > 0))
    if warmup:
        _require(value["model_sha256"] is not None)
    if value["model_ready"]:
        _require(warmup, "model-not-ready")
        _require(value["lifecycle_state"] == "active", "source-exited")
    return copy.deepcopy(value)


def strict_json(raw: bytes) -> dict:
    """Bounded duplicate-rejecting parser for exported authority state."""
    _require(type(raw) is bytes and len(raw) <= MAX_JSON, "overflow")

    def pairs(items: list) -> dict:
        row = {}
        for key, value in items:
            _require(key not in row, "duplicate")
            row[key] = value
        return row

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(BindingError("schema")))
        _require(type(value) is dict)
        pending = [(value, 1)]
        count = 0
        while pending:
            item, depth = pending.pop()
            count += 1
            _require(count <= 2048 and depth <= 8, "overflow")
            children = item.values() if type(item) is dict else item if type(item) is list else ()
            pending.extend((child, depth + 1) for child in children)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, BindingError):
            raise
        raise BindingError("schema") from exc


def _same_semantics(left: dict, right: dict) -> bool:
    # Request accounting is not a lifecycle or binding generation.
    return all(left[key] == right[key] for key in SOURCE_KEYS - {"completed_requests"})


class Authority:
    """One explicit Cell/MAIN Node relationship under a management-owned UUID.

    Calls are not concurrent. The hosting daemon owns its lock and atomic private
    state writer. No method performs I/O or authenticates an external producer.
    """

    def __init__(self, authority_id: str | None = None):
        self.authority_instance = authority_id if authority_id is not None else str(uuid.uuid4())
        _uuid(self.authority_instance)
        self.parent = None
        self.canonical = None
        self.current_source = None
        self.discovered_source = None
        self.binding = None
        self.binding_confirmed = False
        self.source_trusted = False
        self.retired_instances = []

    @property
    def initialized(self) -> bool:
        return self.parent is not None

    def _invalidate(self) -> None:
        self.source_trusted = False
        self.discovered_source = None
        self.binding_confirmed = False

    def _result(self, reason: str = "none", *, invalidate: bool = False) -> dict:
        if invalidate:
            self._invalidate()
        return {"schema_version": 1, "outcome": "accepted" if reason == "none" else "rejected",
                "reason": reason, "snapshot": self.snapshot()}

    def initialize(self, *, cell_id: int = CELL_ID, node_id: int = NODE_ID,
                   parent_cell_id: int = CELL_ID) -> dict:
        """Declare our local semantic counterpart; never import a Linux ID."""
        if self.initialized:
            return self._result("duplicate", invalidate=True)
        if any(type(value) is not int for value in (cell_id, node_id, parent_cell_id)):
            return self._result("schema")
        if parent_cell_id != cell_id:
            return self._result("orphan")
        if cell_id != CELL_ID or node_id != NODE_ID:
            return self._result("orphan")
        self.parent = {"namespace": "cell", "id": CELL_ID, "generation": 1, "active": True}
        self.canonical = {"namespace": "node", "id": NODE_ID, "kind": "ai-service",
                          "generation": 1, "parent_cell_id": CELL_ID}
        return self._result()

    def set_parent(self, active: bool) -> dict:
        """Explicit management lifecycle only; this changes no resource policy."""
        if not self.initialized:
            return self._result("init-order", invalidate=True)
        if type(active) is not bool:
            return self._result("schema", invalidate=True)
        if self.parent["active"] == active:
            return self._result()
        if self.parent["generation"] == MAX_GENERATION:
            return self._result("overflow", invalidate=True)
        self.parent["active"] = active
        self.parent["generation"] += 1
        self.canonical["generation"] += 1
        self._invalidate()
        return self._result()

    def _read(self, record: object, *, discovery: bool = False, allow_inactive: bool = False) -> dict:
        _require(self.initialized, "init-order")
        _require(self.parent["active"] or allow_inactive, "orphan")
        source = validate_source(record)
        current = self.current_source
        if not self.parent["active"]:
            # Observing an already known producer's exit is independent of
            # whether its Cell may bind it. This never introduces a source.
            _require(current is not None and source["source_instance"] == current["source_instance"], "orphan")
        if current is None:
            _require(discovery, "not-discovered")
            _require(source["source_generation"] == 1, "stale")
            return source
        _require(source["source_id"] == current["source_id"], "instance")
        if source["source_instance"] != current["source_instance"]:
            _require(source["source_instance"] not in self.retired_instances, "retired-instance")
            _require(discovery, "stale")
            _require(source["source_generation"] == 1, "stale")
            _require(source["service_start_generation"] > current["service_start_generation"],
                     "generation-rollback")
            _require(len(self.retired_instances) < MAX_RETIRED, "overflow")
            return source
        for key in ("host_boot_id", "process_id", "service_start_generation"):
            _require(source[key] == current[key], "instance")
        _require(source["source_generation"] >= current["source_generation"], "generation-rollback")
        _require(source["completed_requests"] >= current["completed_requests"], "counter-regression")
        if source["source_generation"] == current["source_generation"]:
            _require(_same_semantics(source, current), "stale")
        else:
            # Once a producer reports exit it cannot resurrect the same instance.
            _require(current["lifecycle_state"] != "exited", "retired-instance")
        return source

    def _copy_current(self, source: dict) -> None:
        if self.current_source is not None and not _same_semantics(source, self.current_source):
            self.binding_confirmed = False
        if self.current_source is not None and source["source_instance"] != self.current_source["source_instance"]:
            self.retired_instances.append(self.current_source["source_instance"])
        self.current_source = copy.deepcopy(source)
        self.source_trusted = True

    def discover(self, records: object) -> dict:
        try:
            _require(self.initialized, "init-order")
            _require(type(records) is list)
            _require(len(records) <= 1, "duplicate" if len(records) == 2 else "overflow")
            _require(len(records) == 1, "missing")
            source = self._read(records[0], discovery=True)
            self._copy_current(source)
            self.discovered_source = None
            _require(source["lifecycle_state"] == "active", "source-exited")
            self.discovered_source = copy.deepcopy(source)
            return self._result()
        except BindingError as exc:
            return self._result(exc.reason, invalidate=True)

    def bind(self, record: object) -> dict:
        return self._bind(record, reconcile=False)

    def reconcile(self, record: object) -> dict:
        """Explicitly capture a rediscovered tuple after an existing binding."""
        return self._bind(record, reconcile=True)

    def _bind(self, record: object, *, reconcile: bool) -> dict:
        try:
            source = self._read(record)
            _require(self.discovered_source is not None, "not-discovered")
            _require(_same_semantics(source, self.discovered_source), "stale")
            _require(source["lifecycle_state"] == "active", "source-exited")
            _require(source["model_ready"], "model-not-ready")
            if reconcile:
                _require(self.binding is not None, "unbound")
                _require(self.binding["generation"] < MAX_GENERATION, "overflow")
                _require(not self._binding_current(source), "already-bound")
                generation = self.binding["generation"] + 1
            else:
                _require(self.binding is None, "already-bound")
                generation = 1
            self._copy_current(source)
            self.binding = {"generation": generation,
                            "canonical_generation": self.canonical["generation"],
                            "parent_generation": self.parent["generation"],
                            "source": copy.deepcopy(source)}
            self.binding_confirmed = True
            return self._result()
        except BindingError as exc:
            return self._result(exc.reason, invalidate=exc.reason != "already-bound")

    def observe(self, record: object) -> dict:
        try:
            source = self._read(record, allow_inactive=True)
            changed = self.current_source is not None and not _same_semantics(source, self.current_source)
            self._copy_current(source)
            if changed:
                self.discovered_source = None
            _require(self.parent["active"], "orphan")
            _require(source["lifecycle_state"] == "active", "source-exited")
            _require(source["model_ready"], "model-not-ready")
            _require(self.binding is not None, "unbound")
            _require(self._binding_current(source), "stale")
            return self._result()
        except BindingError as exc:
            return self._result(exc.reason, invalidate=True)

    def _binding_current(self, source: dict | None = None) -> bool:
        current = source if source is not None else self.current_source
        return bool(self.initialized and self.parent["active"] and self.source_trusted
                    and self.binding_confirmed and self.binding and current
                    and current["lifecycle_state"] == "active"
                    and current["model_ready"]
                    and self.binding["canonical_generation"] == self.canonical["generation"]
                    and self.binding["parent_generation"] == self.parent["generation"]
                    and _same_semantics(self.binding["source"], current))

    def export_state(self) -> dict:
        """Copy state for the caller's private, locked, atomic writer."""
        return copy.deepcopy({
            "schema_version": 1, "authority_namespace": AUTHORITY_NAMESPACE,
            "authority_instance": self.authority_instance, "initialized": self.initialized,
            "parent": self.parent, "canonical": self.canonical,
            "current_source": self.current_source, "discovered_source": self.discovered_source,
            "binding": self.binding, "source_trusted": self.source_trusted,
            "binding_confirmed": self.binding_confirmed,
            "retired_instances": self.retired_instances,
        })

    def snapshot(self) -> dict:
        current = self._binding_current()
        present_valid = bool(self.initialized and self.parent["active"] and self.source_trusted
                             and self.current_source is not None)
        present = bool(present_valid and self.current_source["lifecycle_state"] == "active")
        state = ("UNINITIALIZED" if not self.initialized else "BOUND" if current
                 else "STALE" if self.binding is not None else "DISCOVERED"
                 if self.discovered_source is not None else "UNBOUND")
        bits = []
        if self.initialized:
            for bit_id, name, bit_class, value, valid in (
                    (PRESENT_ID, "present", "state", present, present_valid),
                    (SOURCE_BOUND_ID, "source-bound", "validity", current, True)):
                bits.append({"namespace": "nodebit", "id": bit_id, "class": bit_class,
                             "name": name, "parent_node_id": NODE_ID,
                             "parent_node_generation": self.canonical["generation"],
                             "value": value, "valid": valid})
        return {**self.export_state(), "state": state, "binding_valid": current,
                "binding_current": current, "bound_nodes": int(current), "nodebits": bits,
                "observation_only": True, "management_only": True,
                "resource_actions": "UNSUPPORTED", "consistency": "copied-single-producer"}

    @classmethod
    def from_state(cls, value: object) -> "Authority":
        """Reject malformed or internally contradictory persisted state.

        Persistence is trusted only within the caller's private state directory;
        no MAC, live producer authentication or cross-process locking is implied.
        """
        _keys(value, {"schema_version", "authority_namespace", "authority_instance", "initialized",
                      "parent", "canonical", "current_source", "discovered_source", "binding",
                      "source_trusted", "binding_confirmed", "retired_instances"})
        _require(type(value["schema_version"]) is int and value["schema_version"] == 1)
        _require(value["authority_namespace"] == AUTHORITY_NAMESPACE, "namespace")
        _require(all(type(value[name]) is bool for name in
                     ("initialized", "source_trusted", "binding_confirmed")))
        authority = cls(value["authority_instance"])
        retired = value["retired_instances"]
        _require(type(retired) is list and len(retired) <= MAX_RETIRED, "overflow")
        for item in retired:
            _uuid(item)
        _require(len(set(retired)) == len(retired), "duplicate")
        if not value["initialized"]:
            _require(all(value[name] is None for name in
                         ("parent", "canonical", "current_source", "discovered_source", "binding")))
            _require(not retired and not value["source_trusted"] and not value["binding_confirmed"])
            return authority
        parent, canonical = value["parent"], value["canonical"]
        _keys(parent, {"namespace", "id", "generation", "active"})
        _keys(canonical, {"namespace", "id", "kind", "generation", "parent_cell_id"})
        _require(parent["namespace"] == "cell" and canonical["namespace"] == "node", "namespace")
        _require(type(parent["id"]) is int and parent["id"] == CELL_ID, "orphan")
        _require(type(canonical["id"]) is int and canonical["id"] == NODE_ID, "orphan")
        _require(type(canonical["parent_cell_id"]) is int and canonical["parent_cell_id"] == CELL_ID, "orphan")
        _require(canonical["kind"] == "ai-service", "kind")
        _require(type(parent["active"]) is bool)
        _integer(parent["generation"])
        _integer(canonical["generation"])
        _require(parent["generation"] == canonical["generation"], "stale")
        current = validate_source(value["current_source"]) if value["current_source"] is not None else None
        discovered = (validate_source(value["discovered_source"])
                      if value["discovered_source"] is not None else None)
        if current is None:
            _require(not value["source_trusted"] and not retired and discovered is None and value["binding"] is None)
        else:
            _require(current["source_instance"] not in retired, "retired-instance")
        if discovered is not None:
            _require(value["source_trusted"] and parent["active"] and current is not None)
            _require(discovered["lifecycle_state"] == "active", "source-exited")
            _require(_same_semantics(discovered, current), "stale")
            _require(discovered["completed_requests"] <= current["completed_requests"], "counter-regression")
        binding = value["binding"]
        if binding is not None:
            _keys(binding, {"generation", "canonical_generation", "parent_generation", "source"})
            for name in ("generation", "canonical_generation", "parent_generation"):
                _integer(binding[name])
            _require(binding["canonical_generation"] == binding["parent_generation"]
                     <= parent["generation"], "stale")
            bound = validate_source(binding["source"])
            _require(bound["lifecycle_state"] == "active" and bound["model_ready"], "model-not-ready")
            _require(current is not None and bound["source_id"] == current["source_id"], "instance")
            if bound["source_instance"] == current["source_instance"]:
                _require(bound["source_generation"] <= current["source_generation"], "generation-rollback")
                _require(bound["completed_requests"] <= current["completed_requests"], "counter-regression")
                for name in ("host_boot_id", "process_id", "service_start_generation"):
                    _require(bound[name] == current[name], "instance")
                if bound["source_generation"] == current["source_generation"]:
                    _require(_same_semantics(bound, current), "stale")
            else:
                _require(bound["source_instance"] in retired, "retired-instance")
                _require(bound["service_start_generation"] < current["service_start_generation"], "generation-rollback")
        _require(parent["active"] or not value["source_trusted"], "orphan")
        authority.parent = copy.deepcopy(parent)
        authority.canonical = copy.deepcopy(canonical)
        authority.current_source = current
        authority.discovered_source = discovered
        authority.binding = copy.deepcopy(binding)
        authority.binding_confirmed = value["binding_confirmed"]
        authority.source_trusted = value["source_trusted"]
        authority.retired_instances = list(retired)
        if authority.binding_confirmed:
            _require(discovered is not None and authority._binding_current(), "stale")
        return authority
