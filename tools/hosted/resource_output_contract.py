"""Independent replay of MAIN/backend relations and raw Linux observations.

No runtime producer, process sampler or management implementation is imported.
These are evidence consistency checks. Live capture, process termination and
backend launcher proof delivery remain execution-verifier responsibilities.
"""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit

from newagent_output_contract import identity, same, validate_snapshot, validate_source, receipt as validate_receipt

U64 = (1 << 64) - 1
IDENTITY = {"host_boot_id", "process_id", "process_start_ticks", "uid"}
CONFIG = {"schema_version", "endpoint", "model_id", "model_path", "model_sha256", "backend_path",
          "backend_sha256", "provenance_sha256"}
DESCRIPTOR = {"schema_version", "source_namespace", "source_instance", "source_generation", "lifecycle_state",
    "source_only", "producer_owned", "host_boot_id", "process_id", "process_start_ticks", "launcher_process_id",
    "launcher_start_ticks", "model_id", "model_sha256", "backend_sha256", "endpoint", "listener_inode"}
PROOF = {"schema_version", "nonce", "descriptor", "listener_proof", "peer_pid", "peer_uid", "observed_monotonic_ns",
         "capture_kind"}
RELATION = {"schema_version", "relation_id", "relation_generation", "kind", "authority_namespace", "authority_instance",
    "canonical", "parent", "binding_generation", "source_record", "main_identity", "backend_proof", "config_sha256",
    "observation_only", "ownership_valid", "resource_actions"}
SAMPLE = IDENTITY | {"schema_version", "read_start_ns", "read_end_ns", "clock_ticks_per_second", "page_bytes",
    "raw_stat", "raw_status", "user_ticks", "system_ticks", "cpu_total_ns", "rss_pages", "rss_bytes_estimate",
    "virtual_bytes", "scope", "consistency", "memory_accuracy", "source_only"}
OBSERVATION = {"schema_version", "observation_id", "kind", "request_id", "relation_before", "relation_after", "before",
    "after", "source_before", "source_after", "management_before", "management_after", "cpu", "observation_only",
    "ownership_valid", "resource_actions", "consistency"}
PUBLIC = {"schema_version", "action", "outcome", "error", "relation", "relation_current", "observation",
          "observation_only", "ownership_valid", "resource_actions", "capture_kind"}


def require(condition, reason):
    if not condition:
        raise ValueError("resource_contract:" + reason)


def keys(value, expected):
    require(type(value) is dict and value.keys() == expected, "keys")


def uint(value, minimum=0, maximum=U64):
    require(type(value) is int and minimum <= value <= maximum, "integer")
    return value


def schema(value):
    require(type(value) is int and value == 1, "schema")


def text(value, maximum=1024, *, empty=False):
    require(type(value) is str and (empty or bool(value)) and len(value.encode("utf-8")) <= maximum, "text")
    return value


def sha(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None, "hash")


def boundary(value):
    require(value["observation_only"] is True and value["ownership_valid"] is False
            and value["resource_actions"] == "UNSUPPORTED", "boundary")


def process_identity(value):
    keys(value, IDENTITY)
    identity(value["host_boot_id"])
    uint(value["process_id"], 1)
    uint(value["process_start_ticks"], 1)
    uint(value["uid"])


def endpoint(value):
    text(value)
    address = urlsplit(value)
    require(address.scheme == "http" and address.hostname == "127.0.0.1" and address.username is None
            and address.password is None and address.path in ("", "/") and not address.query and not address.fragment
            and address.port is not None and 1024 <= address.port <= 65535, "endpoint")
    return address.port


def config_hash(value):
    keys(value, CONFIG)
    schema(value["schema_version"])
    endpoint(value["endpoint"])
    for name in ("model_id", "model_path", "backend_path"):
        text(value[name])
        require(all(c.isprintable() for c in value[name]), "config_text")
    for name in ("model_sha256", "backend_sha256", "provenance_sha256"):
        sha(value[name])
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def validate_backend_proof(value, *, require_live=False, config=None):
    keys(value, PROOF)
    schema(value["schema_version"])
    identity(value["nonce"])
    uint(value["peer_pid"], 1)
    uint(value["peer_uid"])
    uint(value["observed_monotonic_ns"], 1)
    require(value["capture_kind"] in ("live", "fixture")
            and (not require_live or value["capture_kind"] == "live"), "capture")
    desc = value["descriptor"]
    keys(desc, DESCRIPTOR)
    schema(desc["schema_version"])
    require(desc["source_namespace"] == "linux-model-backend" and desc["lifecycle_state"] == "active"
            and desc["source_only"] is True and desc["producer_owned"] is True
            and same(desc["source_generation"], 1), "backend_descriptor")
    for name in ("source_instance", "host_boot_id"):
        identity(desc[name])
    for name in ("process_id", "process_start_ticks", "launcher_process_id", "launcher_start_ticks", "listener_inode"):
        uint(desc[name], 1)
    require(desc["launcher_process_id"] == value["peer_pid"] and desc["process_id"] != desc["launcher_process_id"],
            "backend_peer")
    text(desc["model_id"])
    require(all(c.isprintable() for c in desc["model_id"]), "backend_model")
    sha(desc["model_sha256"])
    sha(desc["backend_sha256"])
    port = endpoint(desc["endpoint"])
    listener = value["listener_proof"]
    keys(listener, {"raw_tcp_line", "fd_target", "fd_number"})
    uint(listener["fd_number"])
    fields = text(listener["raw_tcp_line"], 4096).split()
    require(len(fields) >= 10 and re.fullmatch(r"[0-9]+:", fields[0]) is not None
            and fields[1] == f"0100007F:{port:04X}" and fields[2] == "00000000:0000" and fields[3] == "0A",
            "backend_listener")
    require(re.fullmatch(r"[0-9]+", fields[7]) is not None and re.fullmatch(r"[0-9]+", fields[9]) is not None,
            "backend_listener")
    require(int(fields[7]) == value["peer_uid"] and int(fields[9]) == desc["listener_inode"]
            and listener["fd_target"] == f"socket:[{desc['listener_inode']}]", "backend_listener")
    if config is not None:
        config_hash(config)
        require(all(same(desc[name], config[name]) for name in
                    ("endpoint", "model_id", "model_sha256", "backend_sha256")), "backend_config")


def validate_relation(value, *, require_live=False, config=None):
    keys(value, RELATION)
    schema(value["schema_version"])
    identity(value["relation_id"])
    uint(value["relation_generation"], 1, 64)
    identity(value["authority_instance"])
    uint(value["binding_generation"], 1, (1 << 63) - 1)
    require(value["kind"] == "uses-model-backend" and value["authority_namespace"] == "aios-hosted-management",
            "relation_identity")
    parent, node = value["parent"], value["canonical"]
    keys(parent, {"namespace", "id", "generation", "active"})
    keys(node, {"namespace", "id", "kind", "generation", "parent_cell_id"})
    require(parent["namespace"] == "cell" and same(parent["id"], 1) and parent["active"] is True
            and node["namespace"] == "node" and same(node["id"], 101) and same(node["parent_cell_id"], 1)
            and node["kind"] == "ai-service", "canonical_identity")
    uint(parent["generation"], 1, (1 << 63) - 1)
    uint(node["generation"], 1, (1 << 63) - 1)
    require(same(parent["generation"], node["generation"]), "canonical_generation")
    source, main, proof = value["source_record"], value["main_identity"], value["backend_proof"]
    validate_source(source)
    require(source["model_ready"] and source["lifecycle_state"] == "active", "source_readiness")
    process_identity(main)
    validate_backend_proof(proof, require_live=require_live, config=config)
    desc = proof["descriptor"]
    require(main["host_boot_id"] == source["host_boot_id"] == desc["host_boot_id"]
            and main["process_id"] == source["process_id"] and main["process_id"] != desc["process_id"]
            and main["uid"] == proof["peer_uid"] and source["model_sha256"] == desc["model_sha256"], "participants")
    sha(value["config_sha256"])
    if config is not None:
        require(value["config_sha256"] == config_hash(config), "config_hash")
    boundary(value)


def source_matches(captured, source):
    return (all(same(captured[key], source[key]) for key in captured if key != "completed_requests")
            and source["completed_requests"] >= captured["completed_requests"])


def validate_relation_progression(previous, current):
    """Replay retained explicit links, including unchanged idempotent replies."""
    validate_relation(previous)
    validate_relation(current)
    if same(previous, current):
        return
    require(all(same(previous[key], current[key]) for key in
                ("authority_namespace", "authority_instance", "relation_id")), "relation_lineage")
    require(current["relation_generation"] == previous["relation_generation"] + 1, "relation_generation")
    require(current["canonical"]["generation"] >= previous["canonical"]["generation"]
            and current["parent"]["generation"] >= previous["parent"]["generation"]
            and current["binding_generation"] >= previous["binding_generation"], "generation_rollback")
    old, new = previous["source_record"], current["source_record"]
    require(old["source_id"] == new["source_id"], "source_lineage")
    semantics_same = all(same(old[key], new[key]) for key in old if key != "completed_requests")
    if old["source_instance"] == new["source_instance"]:
        require(same(previous["main_identity"], current["main_identity"])
                and old["service_start_generation"] == new["service_start_generation"], "instance_changed")
        require(new["source_generation"] >= old["source_generation"], "source_generation_rollback")
        require(new["completed_requests"] >= old["completed_requests"], "source_counter_regression")
        require(new["source_generation"] != old["source_generation"] or semantics_same, "source_semantics")
    else:
        require(new["service_start_generation"] > old["service_start_generation"], "start_generation_rollback")
    require(current["binding_generation"] != previous["binding_generation"]
            or same(previous["canonical"], current["canonical"])
            and same(previous["parent"], current["parent"]) and semantics_same, "binding_generation_changed")


def current_context(relation, source=None, snapshot=None):
    if source is not None:
        validate_source(source)
        require(source_matches(relation["source_record"], source), "stale_source")
    if snapshot is not None:
        validate_snapshot(snapshot)
        require(snapshot["binding_current"] is True and all(same(relation[key], snapshot[key]) for key in
                ("authority_namespace", "authority_instance", "canonical", "parent"))
                and relation["binding_generation"] == snapshot["binding"]["generation"], "stale_management")
        require(source_matches(relation["source_record"], snapshot["current_source"]), "stale_management_source")
        if source is not None:
            require(same(source, snapshot["current_source"]), "source_snapshot")


def decimal(value):
    require(type(value) is str and len(value) <= 20 and re.fullmatch(r"[0-9]+", value) is not None, "decimal")
    return uint(int(value))


def validate_sample(value):
    keys(value, SAMPLE)
    schema(value["schema_version"])
    process_identity({key: value[key] for key in IDENTITY})
    for name in ("read_start_ns", "read_end_ns", "clock_ticks_per_second", "page_bytes"):
        uint(value[name], 1)
    require(value["read_start_ns"] <= value["read_end_ns"], "sample_time")
    raw = text(value["raw_stat"], 4096)
    match = re.fullmatch(r"([0-9]+) \((.*)\) (.+)\s*", raw, flags=re.S)
    require(match is not None, "stat_format")
    require(decimal(match[1]) == value["process_id"], "stat_pid")
    fields = match[3].split()
    require(len(fields) >= 22 and fields[0] in ("R", "S", "D", "T", "t", "K", "W", "P", "I"), "stat_state")
    decimal(fields[1])
    user, system, start, virtual, pages = (decimal(fields[i]) for i in (11, 12, 19, 20, 21))
    require(start == value["process_start_ticks"], "stat_start")
    ticks = uint(user + system)
    expected = {"user_ticks": user, "system_ticks": system,
        "cpu_total_ns": uint(ticks * 1_000_000_000 // value["clock_ticks_per_second"]),
        "rss_pages": pages, "rss_bytes_estimate": uint(pages * value["page_bytes"]), "virtual_bytes": virtual}
    require(all(same(value[key], amount) for key, amount in expected.items()), "raw_accounting")
    status = {}
    for line in text(value["raw_status"], 8192).splitlines():
        require(":" in line, "status_format")
        name, contents = line.split(":", 1)
        if name in ("Pid", "Tgid", "Uid"):
            require(name not in status, "status_duplicate")
            status[name] = contents.split()
    require(status.keys() == {"Pid", "Tgid", "Uid"} and len(status["Pid"]) == len(status["Tgid"]) == 1
            and len(status["Uid"]) == 4, "status_fields")
    require(decimal(status["Pid"][0]) == decimal(status["Tgid"][0]) == value["process_id"]
            and all(decimal(uid) == value["uid"] for uid in status["Uid"]), "status_identity")
    require(value["scope"] == "single-linux-process" and value["consistency"] == "sequential-copied-read"
            and value["memory_accuracy"] == "kernel-approximate" and value["source_only"] is True, "sample_scope")


def pressure_rows(raw, kind):
    rows = {}
    for line in text(raw, 1024).splitlines():
        parts = line.split()
        require(len(parts) == 5 and parts[0] in ("some", "full") and parts[0] not in rows, "pressure_row")
        row = {}
        for item in parts[1:]:
            require(item.count("=") == 1, "pressure_field")
            name, value = item.split("=")
            require(name not in row, "pressure_duplicate")
            if name in ("avg10", "avg60", "avg300"):
                require(re.fullmatch(r"[0-9]{1,3}\.[0-9]{2}", value) is not None, "pressure_average")
                row[name] = uint(int(value.replace(".", "")), maximum=10000)
            elif name == "total":
                row[name] = decimal(value)
            else:
                raise ValueError("resource_contract:pressure_field")
        require(row.keys() == {"avg10", "avg60", "avg300", "total"}, "pressure_fields")
        rows[parts[0]] = {"avg10_bp": row["avg10"], "avg60_bp": row["avg60"],
                         "avg300_bp": row["avg300"], "total_us": row["total"]}
    require("some" in rows and (kind == "cpu" or "full" in rows), "pressure_missing")
    return rows


def validate_pressure(value):
    keys(value, {"schema_version", "read_start_ns", "read_end_ns", "scope", "attribution", "source_only", "metrics"})
    schema(value["schema_version"])
    uint(value["read_start_ns"], 1)
    uint(value["read_end_ns"], 1)
    require(value["read_start_ns"] <= value["read_end_ns"], "pressure_time")
    require(value["scope"] == "linux-system" and value["attribution"] == "unattributed"
            and value["source_only"] is True, "pressure_scope")
    keys(value["metrics"], {"cpu", "memory", "io"})
    for kind, metric in value["metrics"].items():
        keys(metric, {"state", "error", "raw", "some", "full", "full_valid"})
        if metric["state"] == "AVAILABLE":
            rows = pressure_rows(metric["raw"], kind)
            require(metric["error"] is None and same(metric["some"], rows["some"])
                    and same(metric["full"], rows.get("full")) and metric["full_valid"] is (kind != "cpu"),
                    "pressure_raw")
        else:
            require(metric["state"] == "UNAVAILABLE" and metric["some"] is None and metric["full"] is None
                    and metric["full_valid"] is False and metric["error"] in
                    ("unsupported-platform", "pressure-missing", "pressure-io", "pressure-format", "proc-size", "proc-file-type"),
                    "pressure_unavailable")
            if metric["error"] == "pressure-format":
                text(metric["raw"], 1024, empty=True)
                try:
                    pressure_rows(metric["raw"], kind)
                except ValueError:
                    pass
                else:
                    raise ValueError("resource_contract:pressure_available_disguised")
            else:
                require(metric["raw"] is None, "pressure_unavailable_raw")


def validate_frame(value, relation, *, require_live=False, config=None):
    keys(value, {"main", "backend", "pressure", "backend_proof"})
    for name in ("main", "backend"):
        validate_sample(value[name])
    validate_pressure(value["pressure"])
    proof = value["backend_proof"]
    validate_backend_proof(proof, require_live=require_live, config=config)
    require(same(proof["descriptor"], relation["backend_proof"]["descriptor"])
            and proof["capture_kind"] == relation["backend_proof"]["capture_kind"]
            and proof["peer_uid"] == relation["backend_proof"]["peer_uid"], "frame_backend")
    require(same({key: value["main"][key] for key in IDENTITY}, relation["main_identity"]), "frame_main")
    require(all(same(value["backend"][key], proof["descriptor"][key]) for key in IDENTITY - {"uid"})
            and value["backend"]["uid"] == proof["peer_uid"] == value["main"]["uid"], "frame_backend_process")


def validate_observation(value, *, source=None, snapshot=None, receipt=None, require_live=False, config=None):
    keys(value, OBSERVATION)
    schema(value["schema_version"])
    identity(value["observation_id"])
    require(value["kind"] in ("sample", "request"), "observation_kind")
    if value["kind"] == "request":
        identity(value["request_id"])
    else:
        require(value["request_id"] is None and receipt is None, "sample_request")
    boundary(value)
    require(value["consistency"] == "best-effort-process-window", "consistency")
    require(same(value["relation_before"], value["relation_after"]), "relation_changed")
    for suffix in ("before", "after"):
        relation, frame = value["relation_" + suffix], value[suffix]
        validate_relation(relation, require_live=require_live, config=config)
        current_context(relation, value["source_" + suffix], value["management_" + suffix])
        validate_frame(frame, relation, require_live=require_live, config=config)
    first_source, last_source = value["source_before"], value["source_after"]
    require(all(same(first_source[key], last_source[key]) for key in first_source if key != "completed_requests")
            and last_source["completed_requests"] == first_source["completed_requests"] + (value["kind"] == "request"),
            "request_counter")
    before, after = value["before"], value["after"]
    require(before["backend_proof"]["nonce"] != after["backend_proof"]["nonce"]
            and before["backend_proof"]["observed_monotonic_ns"] < after["backend_proof"]["observed_monotonic_ns"],
            "proof_replay")
    keys(value["cpu"], {"main", "backend"})
    for name in ("main", "backend"):
        first, last = before[name], after[name]
        require(all(same(first[key], last[key]) for key in IDENTITY | {"clock_ticks_per_second", "page_bytes"}),
                "process_changed")
        require(first["read_end_ns"] <= last["read_start_ns"] and first["read_start_ns"] < last["read_start_ns"],
                "window_time")
        user, system = last["user_ticks"] - first["user_ticks"], last["system_ticks"] - first["system_ticks"]
        uint(user)
        uint(system)
        total = uint(user + system)
        expected = {"elapsed_ns": last["read_start_ns"] - first["read_start_ns"], "user_ticks": user,
            "system_ticks": system, "total_ticks": total,
            "cpu_time_ns": uint(total * 1_000_000_000 // first["clock_ticks_per_second"])}
        require(same(value["cpu"][name], expected), "cpu_delta")
    require(before["pressure"]["read_end_ns"] <= after["pressure"]["read_start_ns"], "pressure_window")
    for kind in ("cpu", "memory", "io"):
        first, last = before["pressure"]["metrics"][kind], after["pressure"]["metrics"][kind]
        if first["state"] == last["state"] == "AVAILABLE":
            for measure in ("some", "full"):
                if first[measure] is not None and last[measure] is not None:
                    require(first[measure]["total_us"] <= last[measure]["total_us"], "pressure_regression")
    if source is not None:
        validate_source(source)
        require(same(source, last_source), "outer_source")
    if snapshot is not None:
        validate_snapshot(snapshot)
        require(same(snapshot, value["management_after"]), "outer_management")
    if receipt is not None:
        require(value["kind"] == "request", "receipt_kind")
        validate_receipt(receipt, purpose="user", source=last_source)
        require(receipt["outcome"] == "OK" and receipt["request_id"] == value["request_id"]
                and same(receipt.get("source_before"), first_source) and same(receipt.get("source_after"), last_source)
                and receipt.get("authority_instance") == value["relation_after"]["authority_instance"]
                and same(receipt.get("binding_generation"), value["relation_after"]["binding_generation"]), "receipt_context")
        desc = value["relation_after"]["backend_proof"]["descriptor"]
        require(all(same(receipt[name], desc[name]) for name in ("model_id", "model_sha256", "backend_sha256")),
                "receipt_backend")
        require(all(receipt["elapsed_ns"] <= value["cpu"][name]["elapsed_ns"] for name in ("main", "backend")),
                "receipt_outside_window")
        if config is not None:
            require(receipt["provenance_sha256"] == config["provenance_sha256"], "receipt_provenance")


def validate_resource_result(value, *, source=None, snapshot=None, receipt=None, require_live=False, config=None):
    """Replay exact public resource payload; no fixture becomes live evidence."""
    keys(value, PUBLIC)
    schema(value["schema_version"])
    boundary(value)
    require(value["action"] in ("link", "status", "sample", "request"), "action")
    require(value["outcome"] in ("OK", "ERROR") and type(value["relation_current"]) is bool, "outcome")
    require(value["capture_kind"] in ("live", "fixture")
            and (not require_live or value["capture_kind"] == "live"), "capture")
    if source is not None:
        validate_source(source)
    if snapshot is not None:
        validate_snapshot(snapshot)
    if value["outcome"] == "ERROR":
        require(type(value["error"]) is str and len(value["error"]) <= 64
                and re.fullmatch(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", value["error"]) is not None
                and value["observation"] is None and value["relation_current"] is False, "error")
    else:
        require(value["error"] is None, "success_error")
    relation = value["relation"]
    if relation is not None:
        validate_relation(relation, require_live=require_live, config=config)
        require(relation["backend_proof"]["capture_kind"] == value["capture_kind"], "relation_capture")
    require(not value["relation_current"] or relation is not None, "missing_relation")
    if value["relation_current"]:
        current_context(relation, source, snapshot)
    if value["outcome"] == "OK" and value["action"] != "status":
        require(value["relation_current"], "success_not_current")
    observation = value["observation"]
    if observation is not None:
        require(value["outcome"] == "OK" and value["relation_current"] and value["action"] in ("sample", "request", "status"),
                "observation_result")
        require(same(observation.get("relation_after"), relation), "observation_relation")
        if value["action"] != "status":
            require(observation.get("kind") == value["action"], "observation_action")
        cached = value["action"] == "status"
        validate_observation(observation, source=None if cached else source, snapshot=None if cached else snapshot,
                             receipt=None if cached else receipt, require_live=require_live, config=config)
        if cached:
            for current in (source, snapshot["current_source"] if snapshot is not None else None):
                if current is not None:
                    require(source_matches(observation["source_after"], current), "cached_source_regression")
    elif value["outcome"] == "OK" and value["action"] in ("sample", "request"):
        raise ValueError("resource_contract:missing_observation")
