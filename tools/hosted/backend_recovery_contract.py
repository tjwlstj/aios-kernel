"""Independent expected-fault acceptance for a CLI-owned backend recovery."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from backend_output_contract import validate_backend_result
from newagent_output_contract import same
from resource_output_contract import validate_sample

RECOVERY_COMMANDS = ["backend start", "agent start", "room discover", "room bind", "resources link",
    "backend status", "agent status", "ask Say hello.", "resources sample", "backend restart",
    "backend recover", "backend status", "backend start", "agent restart", "room discover", "room reconcile",
    "resources link", "ask What is the capital of France? Answer in one short sentence.",
    "agent stop", "backend stop", "resolve example.com", "fetch https://example.com/", "exit"]
IDENTITY = ("host_boot_id", "process_id", "process_start_ticks", "uid")


def require(condition, reason):
    if not condition:
        raise ValueError("backend_recovery:" + reason)


def keys(value, expected, reason):
    require(type(value) is dict and set(value) == set(expected), reason)


def identity(value):
    return {key: value[key] for key in IDENTITY}


def parent(value):
    return int(value["raw_stat"].rsplit(")", 1)[1].split()[1])


def verify_recovery_workflow(directory: Path, commands: list, runs: list, backend: dict):
    from verify_agent import record, decode, read

    require([" ".join([row["name"], *row["args"]]) for row in commands] == RECOVERY_COMMANDS, "command_plan")
    require([row["outcome"] for row in commands] == ["ERROR" if i in (5, 7, 8, 9) else "OK"
            for i in range(len(RECOVERY_COMMANDS))], "command_outcomes")
    require(len(runs) == 2 and len(backend["managed_runs"]) == 2, "run_count")
    old, new = backend["managed_runs"]
    require(old["state"] == "RECOVERED" and old["result"] is None and new["state"] == "STOPPED", "separate_outcomes")
    receipt = old["recovery"]
    proof = record(directory / "fault.json")
    keys(proof, {"schema_version", "scenario", "capture_kind", "source_only", "injector_source_sha256",
        "backend_status", "cli", "supervisor_before", "child_before", "child_after", "child_after_fresh",
        "signal", "fresh_recover"}, "fault_keys")
    require(same(proof["schema_version"], 1) and proof["scenario"] == "running-supervisor-loss"
        and proof["capture_kind"] == "live" and proof["source_only"] is True, "fault_boundary")
    require(proof["injector_source_sha256"] == hashlib.sha256(
        (directory / "verification-source/backend_recovery_guest.py").read_bytes()).hexdigest(), "injector_source")
    validate_backend_result(proof["backend_status"], require_live=True)
    require(same(proof["backend_status"], {**commands[0]["result"], "action": "status"}), "fault_authenticated_run")
    for role in ("cli", "supervisor_before", "child_before", "child_after", "child_after_fresh"):
        validate_sample(proof[role])
    cli, supervisor, child = proof["cli"], proof["supervisor_before"], proof["child_before"]
    events = [decode(line) for line in read(directory / "session/session.events.jsonl").splitlines()]
    owner = events[0]["data"]["source_process"]
    require(same(identity(cli), identity(owner)) and same(identity(cli), identity(receipt["lease"]["owner"]))
        and owner["read_end_ns"] <= cli["read_start_ns"], "fault_cli_owner")
    running_record = receipt["lease"]["service_record"]
    require(same(identity(supervisor), running_record["supervisor_identity"])
        and same(identity(child), running_record["child_identity"])
        and parent(supervisor) == cli["process_id"] and parent(child) == supervisor["process_id"], "fault_parent_chain")
    require(cli["host_boot_id"] == supervisor["host_boot_id"] == child["host_boot_id"]
        and cli["uid"] == supervisor["uid"] == child["uid"] and cli["uid"] > 0, "fault_same_unprivileged_user")
    signal = proof["signal"]
    keys(signal, {"name", "via", "pidfd_acquired_monotonic_ns", "sent_monotonic_ns",
        "supervisor_exit_observed", "supervisor_exit_monotonic_ns"}, "fault_signal_keys")
    require(signal["name"] == "SIGKILL" and signal["via"] == "authenticated-pidfd"
        and signal["supervisor_exit_observed"] is True, "fault_signal")
    for field in ("pidfd_acquired_monotonic_ns", "sent_monotonic_ns", "supervisor_exit_monotonic_ns"):
        require(type(signal[field]) is int and signal[field] > 0, "fault_time_type")
    require(receipt["lease"]["acquired_monotonic_ns"] <= signal["pidfd_acquired_monotonic_ns"]
        <= min(proof[role]["read_start_ns"] for role in ("cli", "supervisor_before", "child_before"))
        <= max(proof[role]["read_end_ns"] for role in ("cli", "supervisor_before", "child_before"))
        <= signal["sent_monotonic_ns"] <= signal["supervisor_exit_monotonic_ns"]
        <= proof["child_after"]["read_start_ns"] <= proof["child_after"]["read_end_ns"]
        <= proof["child_after_fresh"]["read_start_ns"] <= proof["child_after_fresh"]["read_end_ns"]
        <= receipt["recovery"]["started_monotonic_ns"], "fault_time_order")
    require(all(same(identity(proof[role]), identity(child)) for role in ("child_after", "child_after_fresh")),
        "orphan_child_identity")
    fresh = proof["fresh_recover"]
    keys(fresh, {"process_exit_code", "stdout", "stderr", "response"}, "fresh_keys")
    require(same(fresh["process_exit_code"], 1) and fresh["stderr"] == "" and type(fresh["stdout"]) is str
        and same(decode(fresh["stdout"].encode("utf-8")), fresh["response"]), "fresh_execution")
    validate_backend_result(fresh["response"], require_live=True)
    require(fresh["response"]["action"] == "recover" and fresh["response"]["state"] == "FAILED"
        and fresh["response"]["error"] == "recovery-owner-required"
        and fresh["response"]["service_record"] is None and fresh["response"]["descriptor"] is None,
        "fresh_owner_rejected")
    require(receipt["recovery"]["supervisor_returncode"] == -9 and receipt["recovery"]["signal"] == "SIGTERM",
        "fault_recovery_signal")
    require(commands[5]["result"]["error"] == "process-not-running"
        and commands[7]["result"]["error"] == "model-not-ready"
        and commands[7]["result"]["inference_receipt"] is None
        # Sampling authenticates the backend before testing the stored relation.
        # A dead supervisor's attester therefore reports backend-unavailable.
        and commands[8]["result"]["error"] == "backend-unavailable"
        and commands[9]["result"]["error"] == "stop-failed", "stale_rejection")
    resource = commands[8]["result"]["resource_result"]
    require(resource["error"] == "backend-unavailable" and resource["outcome"] == "ERROR"
        and resource["relation_current"] is False and resource["observation"] is None
        and same(resource["relation"], commands[4]["result"]["resource_result"]["relation"]),
        "unavailable_resource_observation")
    invalid = [event for event in runs[0]["events"] if event["event"] == "BACKEND_INVALIDATED"]
    require(len(invalid) == 1 and not runs[0]["requests"] and len(runs[1]["requests"]) == 1, "no_old_model_request")
    require(signal["supervisor_exit_monotonic_ns"] <= invalid[0]["monotonic_ns"]
        <= receipt["recovery"]["started_monotonic_ns"]
        <= receipt["recovery"]["completed_monotonic_ns"] <= new["events"][0]["monotonic_ns"], "recovery_order")
    require(same(commands[6]["result"]["source_record"], invalid[0]["source_record"])
        and same(commands[6]["result"]["management_snapshot"], invalid[0]["management_snapshot"]), "invalidation_delivery")
    require([row["execution_binding"]["descriptor"] for row in runs] == [old["descriptor"], new["descriptor"]]
        and old["descriptor"]["endpoint"] == new["descriptor"]["endpoint"]
        and old["descriptor"]["source_instance"] != new["descriptor"]["source_instance"], "replacement_binding")
    require(commands[10]["result"]["state"] == commands[11]["result"]["state"] == "RECOVERED"
        and commands[12]["result"]["service_record"]["start_generation"] == 2
        and commands[15]["result"]["management_snapshot"]["binding"]["generation"] == 2, "explicit_new_generation")
    require(same(commands[17]["result"]["inference_receipt"], runs[1]["requests"][0])
        and runs[1]["requests"][0]["outcome"] == "OK", "new_model_answer")
    require(commands[18]["result"]["state"] == commands[19]["result"]["state"] == "STOPPED", "normal_cleanup")
