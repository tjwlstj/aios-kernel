#!/usr/bin/env python3
"""Independent backend lifecycle replay; stored records alone are not liveness.

The runtime producer and its state reader are never imported. Live QEMU workflow
checks additionally connect these records to launcher execution and model bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import uuid
from datetime import datetime
from pathlib import Path

from backend_output_contract import validate_backend_result, validate_descriptor, validate_record
from resource_output_contract import config_hash, endpoint, validate_backend_proof, validate_sample

IDENTITY = ("service_id", "instance_id", "start_generation")
SOURCES = ("aios-backend.py", "aios_backend/__init__.py", "aios_backend/protocol.py",
    "aios_backend/client.py", "aios_backend/daemon.py", "aios_agent/__init__.py", "aios_agent/inference.py",
    "aios_resources/__init__.py", "aios_resources/proc.py", "aios_resources/backend.py",
    "aios_service/__init__.py", "aios_service/lifecycle.py", "aios_hosted/__init__.py",
    "aios_hosted/boot.py", "aios_hosted/hardware.py")
FILES = {"start.json", "config.json", "events.jsonl", "service.json", "stdout.log", "stderr.log",
    "backend-source.json", "backend-attestations.jsonl", "health.json"}
RESULT_KEYS = {"schema_version", *IDENTITY, "state", "exit_code", "error", "completed_at", "capture_kind",
    "service_record", "descriptor", "child_exit_code", "child_exit_verified", "forced", "log_bytes", "files"}
EVENT_KEYS = {"schema_version", "instance_id", "sequence", "monotonic_ns", "event", "outcome", "error",
    "service_record", "descriptor"}
MODEL_ID = "aios-qwen3-0.6b-q8_0"
MODEL_SHA = "9465e63a22add5354d9bb4b99e90117043c7124007664907259bd16d043bb031"
BACKEND_SHA = "55c69c1be9d6ad2172e2d1c0acc677a60ea8ff60232009a8c8170f5bcb917611"
LIMIT = 2 * 1024 * 1024
RECOVERY_KEYS = {"schema_version", *IDENTITY, "capture_kind", "source_only", "outcome", "error",
                 "lease", "recovery", "old_run_hashes", "source_hashes"}
LEASE_KEYS = {"acquired_monotonic_ns", "owner", "supervisor", "child", "service_record", "descriptor",
              "state_directory", "sockets", "lease_kind", "child_pidfd_acquired_monotonic_ns", "child_pidfd_live_at_acquisition"}
RECOVERY_PROOF_KEYS = {"started_monotonic_ns", "completed_monotonic_ns", "supervisor_returncode",
    "supervisor_reaped", "child_exit_observed", "child_exit_code", "signal", "signal_sent_monotonic_ns",
    "child_exit_monotonic_ns", "signal_via", "sockets"}
SOCKETS = {"control.sock", "backend.sock"}


def require(condition, reason):
    if not condition:
        raise ValueError("backend_run:" + reason)


def keys(value, expected, reason):
    require(type(value) is dict and value.keys() == expected, reason)


def same(left, right):
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(right, sort_keys=True, allow_nan=False)


def integer(value, minimum=0, maximum=(1 << 64) - 1):
    return type(value) is int and minimum <= value <= maximum


def identifier(value):
    require(type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).int != 0, "uuid")


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, limit=LIMIT):
    require(path.is_file() and not path.is_symlink(), "file:" + path.name)
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    require(len(raw) <= limit, "file_size:" + path.name)
    return raw


def decode(raw):
    def pairs(items):
        value = {}
        for name, item in items:
            require(name not in value, "duplicate_key")
            value[name] = item
        return value
    value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("backend_run:nonfinite")))
    require(type(value) is dict, "object")
    pending, count = [(value, 1)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        require(count <= 4096 and depth <= 16, "complexity")
        children = item.values() if type(item) is dict else item if type(item) is list else ()
        pending.extend((child, depth + 1) for child in children)
    return value


def record(path):
    return decode(read(path))


def timestamp(value):
    require(type(value) is str, "timestamp")
    parsed = datetime.fromisoformat(value)
    require(parsed.utcoffset() is not None, "timestamp_timezone")
    return parsed


def validate_command(command, config, profile):
    require(type(command) is list and all(type(v) is str and 0 < len(v) <= 2048 for v in command), "command")
    port = endpoint(config["endpoint"])
    require(all(config[name].startswith("/") for name in ("model_path", "backend_path")), "config_path")
    if profile == "python-fixture":
        require(len(command) == 3 and command[0].startswith("/")
                and re.fullmatch(r"python(?:[0-9]+(?:\.[0-9]+)*)?", Path(command[0]).name)
                and command[1:] == [config["backend_path"], str(port)], "fixture_command")
    else:
        require((config["model_id"], config["model_sha256"], config["backend_sha256"])
                == (MODEL_ID, MODEL_SHA, BACKEND_SHA), "pinned_profile")
        expected = ["/bin/sh", config["backend_path"], "--server", "-m", config["model_path"], "--gpu", "disable",
            "--host", "127.0.0.1", "--port", str(port), "--no-webui", "-c", "1024", "-b", "64",
            "-ub", "64", "-t", "2", "-np", "1", "--alias", MODEL_ID, "--nologo"]
        require(command == expected, "live_command")


def verify_run(directory, source_root=None, *, require_live=False):
    directory = Path(directory)
    source_root = Path(source_root) if source_root is not None else Path(__file__).resolve().parents[2] / "hosted" / "linux"
    require(directory.is_dir() and not directory.is_symlink(), "run_directory")
    start, result = record(directory / "start.json"), record(directory / "result.json")
    keys(start, {"schema_version", *IDENTITY, "started_at", "capture_kind", "profile", "config_sha256",
                 "source_hashes", "command"}, "start_keys")
    keys(result, RESULT_KEYS, "result_keys")
    require(same(start["schema_version"], 1) and same(result["schema_version"], 1), "schema")
    for name in IDENTITY[:2]:
        identifier(start[name])
    require(integer(start["start_generation"], 1, 64) and directory.name == start["instance_id"], "run_identity")
    require(all(same(start[name], result[name]) for name in IDENTITY), "result_identity")
    require(start["capture_kind"] in ("fixture", "live") and result["capture_kind"] == start["capture_kind"], "capture")
    require(not require_live or start["capture_kind"] == "live", "fixture_not_live")
    require(start["profile"] == ("python-fixture" if start["capture_kind"] == "fixture" else "llamafile-pinned"), "profile")
    keys(start["source_hashes"], set(SOURCES), "source_files")
    for name, value in start["source_hashes"].items():
        require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value), "source_hash")
        require(digest(read(source_root / name)) == value, "source_hash:" + name)
    config = record(directory / "config.json")
    config_hash(config)
    require(digest(read(directory / "config.json")) == start["config_sha256"], "config_hash")
    validate_command(start["command"], config, start["profile"])
    require(timestamp(start["started_at"]) <= timestamp(result["completed_at"]), "run_time")
    require(result["state"] == "STOPPED" and same(result["exit_code"], 0) and result["error"] is None,
            "not_normal_stop")
    require(result["child_exit_verified"] is True and result["forced"] is False
            and type(result["child_exit_code"]) is int and result["child_exit_code"] in (0, -15), "child_exit")
    keys(result["log_bytes"], {"stdout", "stderr"}, "log_keys")
    for name, size in result["log_bytes"].items():
        require(integer(size, 0, 1024 * 1024) and len(read(directory / (name + ".log"), 1024 * 1024)) == size,
                "log_size")
    keys(result["files"], FILES, "result_files")
    require({path.name for path in directory.iterdir()} == FILES | {"result.json"}, "unaccounted_file")
    for name in FILES:
        require(digest(read(directory / name)) == result["files"][name], "artifact_hash:" + name)
    raw = read(directory / "events.jsonl")
    require(raw.endswith(b"\n"), "event_truncated")
    events = [decode(line) for line in raw.splitlines()]
    require([e.get("event") for e in events] == ["STARTING", "CHILD_STARTED", "RUNNING", "STOPPING", "STOPPED"],
            "event_lifecycle")
    previous = None
    for index, event in enumerate(events, 1):
        keys(event, EVENT_KEYS, "event_keys")
        require(same(event["schema_version"], 1) and event["instance_id"] == start["instance_id"]
                and same(event["sequence"], index) and integer(event["monotonic_ns"], 1), "event_identity")
        require(event["outcome"] == "OK" and event["error"] is None, "event_failure")
        if previous is not None:
            require(event["monotonic_ns"] >= previous["monotonic_ns"], "event_time")
        service = event["service_record"]
        validate_record(service)
        require(all(same(service[k], start[k]) for k in (*IDENTITY, "profile", "config_sha256")), "event_service")
        if previous is not None:
            require(same(service["supervisor_identity"], previous["service_record"]["supervisor_identity"]), "supervisor_changed")
        if index == 1:
            require(service["lifecycle_state"] == "starting" and service["child_identity"] is None
                    and service["backend_source_instance"] is None and service["backend_ready"] is False, "starting_record")
        elif index == 2:
            expected = {**previous["service_record"], "child_identity": service["child_identity"]}
            require(service["child_identity"] is not None and same(service, expected), "child_start")
        elif index == 3:
            expected = {**previous["service_record"], "lifecycle_state": "active", "backend_ready": True,
                        "backend_source_instance": service["backend_source_instance"]}
            require(same(service, expected), "ready_transition")
            validate_descriptor(event["descriptor"], service)
        elif index == 4:
            require(same(service, {**previous["service_record"], "backend_ready": False}), "stopping_transition")
        else:
            require(same(service, {**previous["service_record"], "lifecycle_state": "exited"}), "stopped_transition")
        if index != 3:
            require(event["descriptor"] is None, "nonready_descriptor")
        previous = event
    service = previous["service_record"]
    require(same(result["service_record"], service) and same(record(directory / "service.json"), service), "terminal_record")
    descriptor = record(directory / "backend-source.json")
    require(same(descriptor, result["descriptor"]) and same(descriptor, events[2]["descriptor"]), "descriptor_history")
    validate_descriptor(descriptor, service)
    require(all(same(descriptor[key], config[key]) for key in ("endpoint", "model_id", "model_sha256", "backend_sha256")),
            "descriptor_config")
    health = record(directory / "health.json")
    require(health.get("status") == "ok", "health")
    raw_proofs = read(directory / "backend-attestations.jsonl")
    require(not raw_proofs or raw_proofs.endswith(b"\n"), "proof_truncated")
    proofs = [decode(line) for line in raw_proofs.splitlines()]
    require(len(proofs) <= 128, "proof_count")
    nonces, last_time = set(), 0
    for proof in proofs:
        validate_backend_proof(proof, config=config, require_live=require_live)
        require(same(proof["descriptor"], descriptor) and proof["capture_kind"] == start["capture_kind"], "proof_descriptor")
        require(proof["peer_pid"] == service["supervisor_identity"]["process_id"]
                and proof["peer_uid"] == service["supervisor_identity"]["uid"], "proof_peer")
        require(proof["nonce"] not in nonces and proof["observed_monotonic_ns"] >= last_time
                and events[1]["monotonic_ns"] <= proof["observed_monotonic_ns"] <= events[3]["monotonic_ns"], "proof_time")
        nonces.add(proof["nonce"])
        last_time = proof["observed_monotonic_ns"]
    return {"start": start, "result": result, "events": events, "config": config, "descriptor": descriptor,
            "proofs": proofs, "service_record": service, "state": "STOPPED"}


def _sample_parent(sample):
    """The independent sample validator has already checked the full raw stat."""
    return int(re.fullmatch(r"([0-9]+) \((.*)\) (.+)\s*", sample["raw_stat"], flags=re.S)[3].split()[1])


def _sample_identity(sample):
    return {name: sample[name] for name in ("host_boot_id", "process_id", "process_start_ticks", "uid")}


def _socket_stamp(value, *, removed=False):
    keys(value, {"device", "inode"} | ({"removed"} if removed else set()), "recovery_socket_keys")
    require(integer(value["device"]) and integer(value["inode"], 1), "recovery_socket_identity")
    if removed:
        require(value["removed"] is True, "recovery_socket_not_removed")


def verify_recovered_run(directory, receipt_path, source_root=None, *, require_live=False, recovery_owner=None):
    """An explicit fault receipt closes an interrupted run, never a normal STOPPED result.

    The raw lease is acquired while the supervisor is authenticated and its
    child is alive. This replay checks its ownership chain and the later crash /
    retained-handle exit evidence, preserving the original unfinished journal.
    """
    directory, receipt_path = Path(directory), Path(receipt_path)
    source_root = Path(source_root) if source_root is not None else Path(__file__).resolve().parents[2] / "hosted/linux"
    require(directory.is_dir() and not directory.is_symlink(), "run_directory")
    start, receipt = record(directory / "start.json"), record(receipt_path)
    keys(start, {"schema_version", *IDENTITY, "started_at", "capture_kind", "profile", "config_sha256",
                 "source_hashes", "command"}, "start_keys")
    keys(receipt, RECOVERY_KEYS, "recovery_keys")
    require(same(start["schema_version"], 1) and same(receipt["schema_version"], 1), "recovery_schema")
    for name in IDENTITY[:2]:
        identifier(start[name])
    require(integer(start["start_generation"], 1, 64) and directory.name == start["instance_id"]
            and receipt_path.name == start["instance_id"] + ".json", "recovery_run_identity")
    require(all(same(start[name], receipt[name]) for name in IDENTITY), "recovery_identity")
    require(receipt["source_only"] is True and receipt["outcome"] == "RECOVERED" and receipt["error"] is None,
            "recovery_not_successful")
    require(start["capture_kind"] in ("fixture", "live") and receipt["capture_kind"] == start["capture_kind"]
            and (not require_live or start["capture_kind"] == "live"), "recovery_capture")
    require(start["profile"] == ("python-fixture" if start["capture_kind"] == "fixture" else "llamafile-pinned"), "profile")
    timestamp(start["started_at"])
    keys(start["source_hashes"], set(SOURCES), "source_files")
    require(same(receipt["source_hashes"], start["source_hashes"]), "recovery_source_files")
    for name, value in start["source_hashes"].items():
        require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value)
                and digest(read(source_root / name)) == value, "source_hash:" + name)
    config = record(directory / "config.json")
    config_hash(config)
    require(digest(read(directory / "config.json")) == start["config_sha256"], "config_hash")
    validate_command(start["command"], config, start["profile"])
    keys(receipt["old_run_hashes"], FILES, "recovery_files")
    require({path.name for path in directory.iterdir()} == FILES, "recovery_original_files")
    for name in FILES:
        require(digest(read(directory / name)) == receipt["old_run_hashes"][name], "recovery_original_hash:" + name)
    for name in ("stdout.log", "stderr.log"):
        read(directory / name, 1024 * 1024)
    raw_events = read(directory / "events.jsonl")
    require(raw_events.endswith(b"\n"), "event_truncated")
    events = [decode(line) for line in raw_events.splitlines()]
    require([event.get("event") for event in events] == ["STARTING", "CHILD_STARTED", "RUNNING"], "recovery_event_lifecycle")
    previous = None
    for index, event in enumerate(events, 1):
        keys(event, EVENT_KEYS, "event_keys")
        require(same(event["schema_version"], 1) and event["instance_id"] == start["instance_id"]
                and same(event["sequence"], index) and integer(event["monotonic_ns"], 1), "event_identity")
        require(event["outcome"] == "OK" and event["error"] is None, "event_failure")
        service = event["service_record"]
        validate_record(service)
        require(all(same(service[key], start[key]) for key in (*IDENTITY, "profile", "config_sha256")), "event_service")
        if previous is not None:
            require(event["monotonic_ns"] >= previous["monotonic_ns"]
                    and same(service["supervisor_identity"], previous["service_record"]["supervisor_identity"]), "recovery_event_order")
        if index == 1:
            require(service["lifecycle_state"] == "starting" and service["child_identity"] is None
                    and service["backend_source_instance"] is None and service["backend_ready"] is False, "starting_record")
        elif index == 2:
            require(service["child_identity"] is not None and same(service, {**previous["service_record"],
                    "child_identity": service["child_identity"]}), "child_start")
        else:
            require(same(service, {**previous["service_record"], "lifecycle_state": "active", "backend_ready": True,
                    "backend_source_instance": service["backend_source_instance"]}), "ready_transition")
            validate_descriptor(event["descriptor"], service)
        if index != 3:
            require(event["descriptor"] is None, "nonready_descriptor")
        previous = event
    service = events[-1]["service_record"]
    require(same(record(directory / "service.json"), service), "recovery_original_record")
    descriptor = record(directory / "backend-source.json")
    require(same(descriptor, events[-1]["descriptor"]), "descriptor_history")
    validate_descriptor(descriptor, service)
    require(all(same(descriptor[key], config[key]) for key in ("endpoint", "model_id", "model_sha256", "backend_sha256")), "descriptor_config")
    require(record(directory / "health.json").get("status") == "ok", "health")
    lease, recovery = receipt["lease"], receipt["recovery"]
    keys(lease, LEASE_KEYS, "recovery_lease_keys")
    keys(recovery, RECOVERY_PROOF_KEYS, "recovery_proof_keys")
    require(lease["lease_kind"] == "owned-supervisor-child-pidfd" and lease["child_pidfd_live_at_acquisition"] is True
            and integer(lease["child_pidfd_acquired_monotonic_ns"], 1), "recovery_retained_handle")
    require(same(lease["service_record"], service) and same(lease["descriptor"], descriptor), "recovery_lease_record")
    for name in ("owner", "supervisor", "child"):
        validate_sample(lease[name])
    owner, parent, child = (lease[name] for name in ("owner", "supervisor", "child"))
    require(same(_sample_identity(parent), service["supervisor_identity"])
            and same(_sample_identity(child), service["child_identity"]), "recovery_sample_identity")
    require(owner["host_boot_id"] == parent["host_boot_id"] == child["host_boot_id"]
            and owner["uid"] == parent["uid"] == child["uid"]
            and len({owner["process_id"], parent["process_id"], child["process_id"]}) == 3
            and _sample_parent(parent) == owner["process_id"] and _sample_parent(child) == parent["process_id"],
            "recovery_owner_chain")
    if recovery_owner is not None:
        require(same(_sample_identity(owner), recovery_owner), "recovery_console_owner")
    require(owner["process_start_ticks"] <= parent["process_start_ticks"] <= child["process_start_ticks"], "recovery_birth_order")
    require(len({row["clock_ticks_per_second"] for row in (owner, parent, child)}) == 1
            and len({row["page_bytes"] for row in (owner, parent, child)}) == 1, "recovery_sample_units")
    for name in ("acquired_monotonic_ns",):
        require(integer(lease[name], 1), "recovery_acquisition_time")
    for name in ("started_monotonic_ns", "completed_monotonic_ns", "child_exit_monotonic_ns"):
        require(integer(recovery[name], 1), "recovery_time")
    require(events[-1]["monotonic_ns"] <= min(row["read_start_ns"] for row in (owner, parent, child))
            and events[-1]["monotonic_ns"] <= lease["child_pidfd_acquired_monotonic_ns"] <= child["read_start_ns"]
            and max(row["read_end_ns"] for row in (owner, parent, child)) <= lease["acquired_monotonic_ns"]
            <= recovery["started_monotonic_ns"] <= recovery["child_exit_monotonic_ns"]
            <= recovery["completed_monotonic_ns"], "recovery_time_order")
    require(type(recovery["supervisor_returncode"]) is int and -64 <= recovery["supervisor_returncode"] <= 255
            and recovery["supervisor_returncode"] != 0 and recovery["supervisor_reaped"] is True, "recovery_parent_not_crashed")
    require(recovery["child_exit_observed"] is True and recovery["child_exit_code"] is None, "recovery_child_exit")
    require(recovery["signal"] in (None, "SIGTERM"), "recovery_signal")
    if recovery["signal"] is None:
        require(recovery["signal_sent_monotonic_ns"] is None and recovery["signal_via"] is None, "recovery_unsent_signal")
    else:
        require(recovery["signal_via"] == "retained-pidfd" and integer(recovery["signal_sent_monotonic_ns"], 1)
                and recovery["started_monotonic_ns"] <= recovery["signal_sent_monotonic_ns"]
                <= recovery["child_exit_monotonic_ns"], "recovery_signal_time")
    _socket_stamp(lease["state_directory"])
    keys(lease["sockets"], SOCKETS, "recovery_lease_sockets")
    keys(recovery["sockets"], SOCKETS, "recovery_sockets")
    for name in SOCKETS:
        _socket_stamp(lease["sockets"][name])
        _socket_stamp(recovery["sockets"][name], removed=True)
        require(same({key: recovery["sockets"][name][key] for key in ("device", "inode")}, lease["sockets"][name]), "recovery_socket_replaced")
    require(lease["sockets"]["control.sock"] != lease["sockets"]["backend.sock"], "recovery_socket_alias")
    raw_proofs = read(directory / "backend-attestations.jsonl")
    require(not raw_proofs or raw_proofs.endswith(b"\n"), "proof_truncated")
    proofs = [decode(line) for line in raw_proofs.splitlines()]
    require(len(proofs) <= 128, "proof_count")
    nonces, last_time = set(), 0
    for proof in proofs:
        validate_backend_proof(proof, config=config, require_live=require_live)
        require(same(proof["descriptor"], descriptor) and proof["capture_kind"] == start["capture_kind"], "proof_descriptor")
        require(proof["peer_pid"] == parent["process_id"] and proof["peer_uid"] == parent["uid"], "proof_peer")
        require(proof["nonce"] not in nonces and proof["observed_monotonic_ns"] >= last_time
                and events[1]["monotonic_ns"] <= proof["observed_monotonic_ns"] <= recovery["started_monotonic_ns"], "proof_time")
        nonces.add(proof["nonce"])
        last_time = proof["observed_monotonic_ns"]
    terminal = {**service, "lifecycle_state": "exited", "backend_ready": False}
    return {"start": start, "result": None, "recovery": receipt, "events": events, "config": config,
            "descriptor": descriptor, "proofs": proofs, "service_record": terminal, "state": "RECOVERED"}


def verify_backend_runs(state_dir, source_root=None, require_live=False, *, allow_recovered=False, recovery_owner=None):
    try:
        state_dir = Path(state_dir)
        require(state_dir.is_dir() and not state_dir.is_symlink(), "state_directory")
        registry = record(state_dir / "registry.json")
        keys(registry, {"schema_version", *IDENTITY}, "registry_keys")
        require(same(registry["schema_version"], 1), "registry_schema")
        root = state_dir / "runs"
        require(root.is_dir() and not root.is_symlink(), "runs_directory")
        paths = list(root.iterdir())
        require(1 <= len(paths) <= 64, "run_count")
        recovery_root = state_dir / "recoveries"
        receipts = {}
        if recovery_root.exists():
            require(allow_recovered and recovery_root.is_dir() and not recovery_root.is_symlink(), "recovery_not_allowed")
            receipt_paths = list(recovery_root.iterdir())
            require(1 <= len(receipt_paths) <= len(paths), "recovery_count")
            receipts = {path.stem: path for path in receipt_paths}
            require(len(receipts) == len(receipt_paths) and set(receipts) <= {path.name for path in paths}, "recovery_orphan")
        runs = [verify_recovered_run(path, receipts[path.name], source_root, require_live=require_live, recovery_owner=recovery_owner)
                if path.name in receipts else verify_run(path, source_root, require_live=require_live) for path in paths]
        runs.sort(key=lambda run: run["start"]["start_generation"])
        require([run["start"]["start_generation"] for run in runs] == list(range(1, len(runs) + 1)), "start_generation_gap")
        require(len({run["start"]["service_id"] for run in runs}) == 1
                and len({run["start"]["instance_id"] for run in runs}) == len(runs)
                and len({run["descriptor"]["source_instance"] for run in runs}) == len(runs), "service_lineage")
        for old, new in zip(runs, runs[1:]):
            if old["state"] == "RECOVERED":
                require(old["recovery"]["recovery"]["completed_monotonic_ns"] <= new["events"][0]["monotonic_ns"]
                        and old["service_record"]["supervisor_identity"]["host_boot_id"]
                        == new["service_record"]["supervisor_identity"]["host_boot_id"], "recovery_overlapping_runs")
            else:
                require(timestamp(old["result"]["completed_at"]) <= timestamp(new["start"]["started_at"]), "overlapping_runs")
            parent_old, parent_new = old["service_record"]["supervisor_identity"], new["service_record"]["supervisor_identity"]
            if parent_old["host_boot_id"] == parent_new["host_boot_id"]:
                require(parent_new["process_start_ticks"] > parent_old["process_start_ticks"], "supervisor_reused")
        require(all(same(registry[name], runs[-1]["start"][name]) for name in IDENTITY), "latest_registry")
        latest = record(state_dir / "latest.json")
        validate_backend_result(latest, action="status", require_live=require_live)
        # Recovery preserves the interrupted daemon's latest.json. It is never
        # rewritten as an invented successful daemon termination record.
        last = runs[-1]
        expected_state = "RUNNING" if last["state"] == "RECOVERED" else "STOPPED"
        expected_record = last["events"][2]["service_record"] if last["state"] == "RECOVERED" else last["service_record"]
        require(latest["state"] == expected_state and latest["outcome"] == "OK"
                and same(latest["service_record"], expected_record), "latest_state")
        require(same(latest["descriptor"], last["descriptor"] if last["state"] == "RECOVERED" else None),
                "latest_descriptor")
        require(same(record(state_dir / "config.json"), runs[-1]["config"]), "latest_config")
        nonces = [p["nonce"] for run in runs for p in run["proofs"]]
        require(len(nonces) == len(set(nonces)), "cross_run_proof_reuse")
        value = {"outcome": "PASS", "reasons": [], "runs": runs, "state": runs[-1]["state"]}
        if allow_recovered:
            value.update(recovered_runs=[run for run in runs if run["state"] == "RECOVERED"],
                         normal_runs=[run for run in runs if run["state"] == "STOPPED"])
        return value
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        return {"outcome": "FAIL", "reasons": [str(exc) or type(exc).__name__]}


def verify_control(output, action, require_live=True):
    try:
        output = Path(output)
        execution = record(output / "execution.json")
        keys(execution, {"schema_version", "action", "process_exit_code", "stdout_sha256", "stderr_sha256"}, "execution_keys")
        require(same(execution["schema_version"], 1) and execution["action"] == action
                and same(execution["process_exit_code"], 0), "execution_exit")
        stdout, stderr = read(output / "stdout.log", 16384), read(output / "stderr.log", 16384)
        require(digest(stdout) == execution["stdout_sha256"] and digest(stderr) == execution["stderr_sha256"], "execution_hash")
        require(not stderr and stdout.endswith(b"\n") and stdout.count(b"\n") == 1, "execution_output")
        value = decode(stdout)
        validate_backend_result(value, action=action, require_live=require_live)
        require(value["outcome"] == "OK", "control_failed")
        return {"outcome": "PASS", "reasons": [], "result": value, "execution": execution}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        return {"outcome": "FAIL", "reasons": [str(exc) or type(exc).__name__]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("state_dir", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--require-live", action="store_true")
    args = parser.parse_args()
    value = verify_backend_runs(args.state_dir, args.source_root, args.require_live)
    # A CLI verdict stays compact; programmatic callers get full replay records.
    print(json.dumps({key: value[key] for key in value if key != "runs"}, indent=2))
    return 0 if value["outcome"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
