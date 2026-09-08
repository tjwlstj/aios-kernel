"""Pure MAIN-to-backend relationships and copied resource windows.

The caller authenticates live processes and serializes persistence. These
functions perform no I/O, acquire no resource ownership and issue no controls.
Linux process identifiers remain source coordinates under an explicit relation.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from urllib.parse import urlsplit

from .binding import Authority, BindingError, SOURCE_KEYS, validate_source
from aios_resources import ResourceError
from aios_resources.proc import accounting, parse_pressure, parse_stat, parse_status

MAX_VALUE = (1 << 64) - 1
MAX_RELATIONS = 64
IDENTITY_KEYS = frozenset({"host_boot_id", "process_id", "process_start_ticks", "uid"})
CONFIG_KEYS = frozenset({"schema_version", "endpoint", "model_id", "model_path", "model_sha256",
                         "backend_path", "backend_sha256", "provenance_sha256"})
STATE_KEYS = frozenset({"schema_version", "authority_namespace", "authority_instance", "initialized",
    "parent", "canonical", "current_source", "discovered_source", "binding", "source_trusted",
    "binding_confirmed", "retired_instances"})
SNAPSHOT_KEYS = STATE_KEYS | {"state", "binding_valid", "binding_current", "bound_nodes", "nodebits",
    "observation_only", "management_only", "resource_actions", "consistency"}
DESCRIPTOR_KEYS = frozenset({"schema_version", "source_namespace", "source_instance", "source_generation",
    "lifecycle_state", "source_only", "producer_owned", "host_boot_id", "process_id", "process_start_ticks",
    "launcher_process_id", "launcher_start_ticks", "model_id", "model_sha256", "backend_sha256", "endpoint",
    "listener_inode"})
PROOF_KEYS = frozenset({"schema_version", "nonce", "descriptor", "listener_proof", "peer_pid", "peer_uid",
                         "observed_monotonic_ns", "capture_kind"})
RELATION_KEYS = frozenset({"schema_version", "relation_id", "relation_generation", "kind",
    "authority_namespace", "authority_instance", "canonical", "parent", "binding_generation", "source_record",
    "main_identity", "backend_proof", "config_sha256", "observation_only", "ownership_valid", "resource_actions"})
SAMPLE_KEYS = IDENTITY_KEYS | {"schema_version", "read_start_ns", "read_end_ns", "clock_ticks_per_second",
    "page_bytes", "raw_stat", "raw_status", "user_ticks", "system_ticks", "cpu_total_ns", "rss_pages",
    "rss_bytes_estimate", "virtual_bytes", "scope", "consistency", "memory_accuracy", "source_only"}
OBSERVATION_KEYS = frozenset({"schema_version", "observation_id", "kind", "request_id", "relation_before",
    "relation_after", "before", "after", "source_before", "source_after", "management_before", "management_after",
    "cpu", "observation_only", "ownership_valid", "resource_actions", "consistency"})


def _check(condition, code="resource-schema"):
    if not condition:
        raise ResourceError(code)


def _keys(value, keys):
    _check(type(value) is dict and value.keys() == keys)


def _same(left, right):
    if type(left) is not type(right):
        return False
    if type(left) is dict:
        return left.keys() == right.keys() and all(_same(left[k], right[k]) for k in left)
    if type(left) is list:
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right))
    return left == right


def _integer(value, minimum=0, maximum=MAX_VALUE):
    _check(type(value) is int and minimum <= value <= maximum, "resource-integer")


def _uuid(value):
    try:
        _check(type(value) is str, "resource-identity")
        parsed = uuid.UUID(value)
        _check(str(parsed) == value and parsed.int != 0, "resource-identity")
    except (ValueError, AttributeError) as exc:
        if isinstance(exc, ResourceError):
            raise
        raise ResourceError("resource-identity") from exc


def _hash(value):
    _check(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "resource-hash")


def _schema(value):
    _check(type(value) is int and value == 1)


def _boundary(value):
    _check(value["observation_only"] is True and value["ownership_valid"] is False
           and value["resource_actions"] == "UNSUPPORTED", "resource-boundary")


def _source(value):
    try:
        validate_source(value)
    except BindingError as exc:
        raise ResourceError("resource-source") from exc


def _snapshot(value):
    _keys(value, SNAPSHOT_KEYS)
    try:
        restored = Authority.from_state({key: value[key] for key in STATE_KEYS}).snapshot()
    except BindingError as exc:
        raise ResourceError("resource-management") from exc
    _check(_same(restored, value), "resource-management")


def _identity(value):
    _keys(value, IDENTITY_KEYS)
    _uuid(value["host_boot_id"])
    for key in ("process_id", "process_start_ticks"):
        _integer(value[key], 1)
    _integer(value["uid"])


def _endpoint(value):
    _check(type(value) is str and len(value) <= 1024, "resource-endpoint")
    try:
        address = urlsplit(value)
        _check(address.scheme == "http" and address.hostname == "127.0.0.1"
               and address.username is None and address.password is None and address.path in ("", "/")
               and not address.query and not address.fragment and address.port is not None
               and 1024 <= address.port <= 65535, "resource-endpoint")
        return address.port
    except ValueError as exc:
        if isinstance(exc, ResourceError):
            raise
        raise ResourceError("resource-endpoint") from exc


def config_digest(config):
    """Hash exact validated inference settings, without a trailing newline."""
    _keys(config, CONFIG_KEYS)
    _schema(config["schema_version"])
    for key in ("model_sha256", "backend_sha256", "provenance_sha256"):
        _hash(config[key])
    for key in ("model_id", "model_path", "backend_path"):
        _check(type(config[key]) is str and 0 < len(config[key]) <= 1024
               and all(c.isprintable() for c in config[key]), "resource-config")
    _endpoint(config["endpoint"])
    return hashlib.sha256(json.dumps(config, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _proof(value):
    _keys(value, PROOF_KEYS)
    _schema(value["schema_version"])
    _uuid(value["nonce"])
    for key in ("peer_pid", "observed_monotonic_ns"):
        _integer(value[key], 1)
    _integer(value["peer_uid"])
    _check(value["capture_kind"] in ("live", "fixture"), "resource-capture")
    desc = value["descriptor"]
    _keys(desc, DESCRIPTOR_KEYS)
    _schema(desc["schema_version"])
    _check(desc["source_namespace"] == "linux-model-backend" and desc["lifecycle_state"] == "active"
           and desc["source_only"] is True and desc["producer_owned"] is True, "backend-descriptor")
    _check(type(desc["source_generation"]) is int and desc["source_generation"] == 1, "backend-descriptor")
    for key in ("source_instance", "host_boot_id"):
        _uuid(desc[key])
    for key in ("process_id", "process_start_ticks", "launcher_process_id", "launcher_start_ticks", "listener_inode"):
        _integer(desc[key], 1)
    _check(desc["process_id"] != desc["launcher_process_id"] and value["peer_pid"] == desc["launcher_process_id"],
           "backend-peer")
    _check(type(desc["model_id"]) is str and 0 < len(desc["model_id"]) <= 1024
           and all(c.isprintable() for c in desc["model_id"]), "backend-descriptor")
    for key in ("model_sha256", "backend_sha256"):
        _hash(desc[key])
    port = _endpoint(desc["endpoint"])
    listener = value["listener_proof"]
    _keys(listener, {"raw_tcp_line", "fd_target", "fd_number"})
    _integer(listener["fd_number"])
    raw = listener["raw_tcp_line"]
    _check(type(raw) is str and 0 < len(raw.encode("utf-8")) <= 4096, "backend-listener")
    fields = raw.split()
    _check(len(fields) >= 10 and fields[0].endswith(":") and fields[0][:-1].isdigit()
           and fields[1] == "0100007F:" + format(port, "04X") and fields[2] == "00000000:0000"
           and fields[3] == "0A" and fields[7].isdigit() and fields[9].isdigit(), "backend-listener")
    _check(int(fields[7]) == value["peer_uid"] and int(fields[9]) == desc["listener_inode"]
           and listener["fd_target"] == "socket:[" + str(desc["listener_inode"]) + "]", "backend-listener")


def _proof_config(proof, config):
    config_digest(config)
    desc = proof["descriptor"]
    _check(all(_same(desc[key], config[key]) for key in
               ("endpoint", "model_id", "model_sha256", "backend_sha256")), "backend-config")


def validate_relation(relation):
    """Validate a stored relation without claiming its participants are alive."""
    _keys(relation, RELATION_KEYS)
    _schema(relation["schema_version"])
    _uuid(relation["relation_id"])
    _integer(relation["relation_generation"], 1, MAX_RELATIONS)
    _check(relation["kind"] == "uses-model-backend"
           and relation["authority_namespace"] == "aios-hosted-management", "resource-relation")
    _uuid(relation["authority_instance"])
    _integer(relation["binding_generation"], 1, (1 << 63) - 1)
    parent, node = relation["parent"], relation["canonical"]
    _keys(parent, {"namespace", "id", "generation", "active"})
    _keys(node, {"namespace", "id", "kind", "generation", "parent_cell_id"})
    _check(parent["namespace"] == "cell" and _same(parent["id"], 1) and parent["active"] is True
           and node["namespace"] == "node" and _same(node["id"], 101) and _same(node["parent_cell_id"], 1)
           and node["kind"] == "ai-service", "resource-canonical")
    _integer(parent["generation"], 1, (1 << 63) - 1)
    _integer(node["generation"], 1, (1 << 63) - 1)
    _check(parent["generation"] == node["generation"], "resource-canonical")
    source, main, proof = relation["source_record"], relation["main_identity"], relation["backend_proof"]
    _source(source)
    _check(source["model_ready"] and source["lifecycle_state"] == "active", "resource-source")
    _identity(main)
    _proof(proof)
    desc = proof["descriptor"]
    _check(main["host_boot_id"] == source["host_boot_id"] == desc["host_boot_id"]
           and main["process_id"] == source["process_id"] and main["process_id"] != desc["process_id"]
           and main["uid"] == proof["peer_uid"] and source["model_sha256"] == desc["model_sha256"],
           "resource-participant")
    _hash(relation["config_sha256"])
    _boundary(relation)


def _context(snapshot, source, main_identity, backend_proof, config):
    _snapshot(snapshot)
    _source(source)
    _identity(main_identity)
    _proof(backend_proof)
    _proof_config(backend_proof, config)
    _check(snapshot["binding_current"] is True and _same(snapshot["current_source"], source), "resource-unbound")


def _progression(previous, current):
    # A separately restored management file must not roll back the bounds kept
    # in resource-state.json. Counter progress is independent of generations.
    _check(all(_same(previous[key], current[key]) for key in
               ("authority_namespace", "authority_instance", "relation_id")), "resource-authority")
    _check(current["relation_generation"] == previous["relation_generation"] + 1, "resource-generation")
    _check(current["canonical"]["generation"] >= previous["canonical"]["generation"]
           and current["parent"]["generation"] >= previous["parent"]["generation"]
           and current["binding_generation"] >= previous["binding_generation"], "resource-generation-rollback")
    old, new = previous["source_record"], current["source_record"]
    _check(old["source_id"] == new["source_id"], "resource-source")
    if old["source_instance"] == new["source_instance"]:
        _check(_same(previous["main_identity"], current["main_identity"])
               and old["service_start_generation"] == new["service_start_generation"], "resource-participant")
        _check(new["source_generation"] >= old["source_generation"], "resource-generation-rollback")
        _check(new["completed_requests"] >= old["completed_requests"], "resource-counter-regression")
        if new["source_generation"] == old["source_generation"]:
            _check(all(_same(old[key], new[key]) for key in SOURCE_KEYS - {"completed_requests"}), "resource-source")
    else:
        _check(new["service_start_generation"] > old["service_start_generation"], "resource-generation-rollback")
    if current["binding_generation"] == previous["binding_generation"]:
        _check(_same(previous["canonical"], current["canonical"]) and _same(previous["parent"], current["parent"])
               and all(_same(old[key], new[key]) for key in SOURCE_KEYS - {"completed_requests"}),
               "resource-binding-generation")


def link(snapshot, source, main_identity, backend_proof, config, previous=None):
    """Explicitly copy the current bound MAIN and authenticated backend tuple."""
    _context(snapshot, source, main_identity, backend_proof, config)
    generation, relation_id = 1, str(uuid.uuid4())
    if previous is not None:
        validate_relation(previous)
        _check(previous["authority_instance"] == snapshot["authority_instance"], "resource-authority")
        _check(previous["relation_generation"] < MAX_RELATIONS, "relation-limit")
        generation, relation_id = previous["relation_generation"] + 1, previous["relation_id"]
    relation = {"schema_version": 1, "relation_id": relation_id, "relation_generation": generation,
        "kind": "uses-model-backend", "authority_namespace": snapshot["authority_namespace"],
        "authority_instance": snapshot["authority_instance"], "canonical": snapshot["canonical"],
        "parent": snapshot["parent"], "binding_generation": snapshot["binding"]["generation"],
        "source_record": source, "main_identity": main_identity, "backend_proof": backend_proof,
        "config_sha256": config_digest(config), "observation_only": True, "ownership_valid": False,
        "resource_actions": "UNSUPPORTED"}
    validate_relation(relation)
    if previous is not None:
        _progression(previous, relation)
    return copy.deepcopy(relation)


def is_current(relation, snapshot, source, main_identity, backend_proof, config):
    """Fail closed on changed identity; request progress alone keeps the link."""
    try:
        validate_relation(relation)
        _context(snapshot, source, main_identity, backend_proof, config)
        return (all(_same(relation[key], snapshot[key]) for key in
                    ("authority_namespace", "authority_instance", "canonical", "parent"))
            and relation["binding_generation"] == snapshot["binding"]["generation"]
            and all(_same(relation["source_record"][key], source[key]) for key in SOURCE_KEYS - {"completed_requests"})
            and source["completed_requests"] >= relation["source_record"]["completed_requests"]
            and _same(relation["main_identity"], main_identity)
            and _same(relation["backend_proof"]["descriptor"], backend_proof["descriptor"])
            and relation["backend_proof"]["peer_uid"] == backend_proof["peer_uid"]
            and relation["backend_proof"]["capture_kind"] == backend_proof["capture_kind"]
            and relation["config_sha256"] == config_digest(config))
    except (ResourceError, BindingError):
        return False


def _sample(value):
    _keys(value, SAMPLE_KEYS)
    _schema(value["schema_version"])
    _identity({key: value[key] for key in IDENTITY_KEYS})
    for key in ("read_start_ns", "read_end_ns", "clock_ticks_per_second", "page_bytes"):
        _integer(value[key], 1)
    _check(value["read_start_ns"] <= value["read_end_ns"], "resource-time")
    parsed = parse_stat(value["raw_stat"], value["process_id"])
    status = parse_status(value["raw_status"], value["process_id"])
    _check(parsed["state"] not in ("Z", "X", "x") and parsed["process_start_ticks"] == value["process_start_ticks"]
           and status["uid"] == value["uid"], "resource-process")
    calculated = accounting(parsed, value["clock_ticks_per_second"], value["page_bytes"])
    _check(all(_same(value[key], amount) for key, amount in calculated.items()), "resource-accounting")
    _check(value["scope"] == "single-linux-process" and value["consistency"] == "sequential-copied-read"
           and value["memory_accuracy"] == "kernel-approximate" and value["source_only"] is True,
           "resource-boundary")


def _pressure(value):
    _keys(value, {"schema_version", "read_start_ns", "read_end_ns", "scope", "attribution", "source_only", "metrics"})
    _schema(value["schema_version"])
    for key in ("read_start_ns", "read_end_ns"):
        _integer(value[key], 1)
    _check(value["read_start_ns"] <= value["read_end_ns"], "resource-time")
    _check(value["scope"] == "linux-system" and value["attribution"] == "unattributed"
           and value["source_only"] is True, "resource-pressure-scope")
    _keys(value["metrics"], {"cpu", "memory", "io"})
    for kind, row in value["metrics"].items():
        _keys(row, {"state", "error", "raw", "some", "full", "full_valid"})
        if row["state"] == "AVAILABLE":
            _check(_same(row, parse_pressure(row["raw"], kind)), "resource-pressure")
        else:
            _check(row["state"] == "UNAVAILABLE" and row["some"] is None and row["full"] is None
                   and row["full_valid"] is False and row["error"] in
                   {"unsupported-platform", "pressure-missing", "pressure-io", "pressure-format", "proc-size", "proc-file-type"},
                   "resource-pressure")
            if row["error"] == "pressure-format":
                _check(type(row["raw"]) is str and len(row["raw"].encode("utf-8")) <= 1024, "resource-pressure")
                try:
                    parse_pressure(row["raw"], kind)
                except ResourceError:
                    pass
                else:
                    raise ResourceError("resource-pressure")
            else:
                _check(row["raw"] is None, "resource-pressure")


def _frame(value, relation):
    _keys(value, {"main", "backend", "pressure", "backend_proof"})
    for name in ("main", "backend"):
        _sample(value[name])
    _pressure(value["pressure"])
    _proof(value["backend_proof"])
    main = {key: value["main"][key] for key in IDENTITY_KEYS}
    desc = value["backend_proof"]["descriptor"]
    _check(_same(main, relation["main_identity"]) and _same(desc, relation["backend_proof"]["descriptor"]),
           "resource-participant")
    backend = value["backend"]
    _check(all(_same(backend[key], desc[key]) for key in IDENTITY_KEYS - {"uid"})
           and backend["uid"] == value["backend_proof"]["peer_uid"] == main["uid"], "resource-participant")


def _cpu(before, after):
    _check(all(_same(before[key], after[key]) for key in IDENTITY_KEYS | {"clock_ticks_per_second", "page_bytes"}),
           "resource-process")
    _check(before["read_end_ns"] <= after["read_start_ns"]
           and before["read_start_ns"] < after["read_start_ns"], "resource-time")
    user, system = after["user_ticks"] - before["user_ticks"], after["system_ticks"] - before["system_ticks"]
    _check(user >= 0 and system >= 0, "cpu-regression")
    total = user + system
    cpu_ns = total * 1_000_000_000 // before["clock_ticks_per_second"]
    _integer(total)
    _integer(cpu_ns)
    return {"elapsed_ns": after["read_start_ns"] - before["read_start_ns"], "user_ticks": user,
            "system_ticks": system, "total_ticks": total, "cpu_time_ns": cpu_ns}


def build_observation(*, kind, request_id, relation_before, relation_after, before, after,
                      source_before, source_after, management_before, management_after, config):
    """Build one successful same-participant window; errors never mutate MAIN."""
    _check(kind in ("sample", "request"), "resource-kind")
    if kind == "request":
        _uuid(request_id)
    else:
        _check(request_id is None, "resource-request")
    _check(_same(relation_before, relation_after), "resource-stale")
    for relation, frame, source, management in (
            (relation_before, before, source_before, management_before),
            (relation_after, after, source_after, management_after)):
        validate_relation(relation)
        _frame(frame, relation)
        identity = {key: frame["main"][key] for key in IDENTITY_KEYS}
        _check(is_current(relation, management, source, identity, frame["backend_proof"], config), "resource-stale")
    _check(all(_same(source_before[key], source_after[key]) for key in SOURCE_KEYS - {"completed_requests"})
           and source_after["completed_requests"] == source_before["completed_requests"] + (kind == "request"),
           "resource-request-counter")
    _check(before["backend_proof"]["nonce"] != after["backend_proof"]["nonce"]
           and before["backend_proof"]["observed_monotonic_ns"] < after["backend_proof"]["observed_monotonic_ns"],
           "resource-proof-replay")
    _check(before["pressure"]["read_end_ns"] <= after["pressure"]["read_start_ns"], "resource-time")
    for name in ("cpu", "memory", "io"):
        first, last = before["pressure"]["metrics"][name], after["pressure"]["metrics"][name]
        if first["state"] == last["state"] == "AVAILABLE":
            for measure in ("some", "full"):
                if first[measure] is not None and last[measure] is not None:
                    _check(first[measure]["total_us"] <= last[measure]["total_us"], "pressure-regression")
    result = {"schema_version": 1, "observation_id": str(uuid.uuid4()), "kind": kind, "request_id": request_id,
        "relation_before": relation_before, "relation_after": relation_after, "before": before, "after": after,
        "source_before": source_before, "source_after": source_after, "management_before": management_before,
        "management_after": management_after, "cpu": {name: _cpu(before[name], after[name]) for name in ("main", "backend")},
        "observation_only": True, "ownership_valid": False, "resource_actions": "UNSUPPORTED",
        "consistency": "best-effort-process-window"}
    return copy.deepcopy(result)
