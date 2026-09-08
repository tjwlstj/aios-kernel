#!/usr/bin/env python3
"""Independent artifact and cross-console verification of an unbound service.

A saved STOPPED snapshot alone is not proof of process exit. The workflow gate
also checks captured control executions, whose implementation waits on the
authenticated daemon's pidfd, and independently checks both CLI executions.
VM shutdown is a separate verdict produced by qemu_console.py.
"""
from __future__ import annotations

import argparse
import copy
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from verify_boot import keys, parse, require, validate_inventory, verify_bundle
from verify_console import digest, number, service_result, verify_execution

IDENTITY = ("service_id", "instance_id", "generation")
SOURCES = ("aios-service.py", "aios_service/__init__.py", "aios_service/client.py", "aios_service/lifecycle.py",
           "aios-boot.py", "aios_hosted/__init__.py", "aios_hosted/boot.py", "aios_hosted/hardware.py")
RESULT_FILES = ("start.json", "lifecycle.events.jsonl", "latest.json", "observation.json")
FIRST = ["service status", "about", "resolve example.com", "fetch https://example.com/", "exit"]
SECOND = ["service status", "service restart", "service status", "service stop", "service status",
          "service start", "service status", "exit"]


def read(path: Path) -> bytes:
    require(not path.is_symlink(), "artifact_symlink")
    with path.open("rb") as stream:
        data = stream.read(4 * 1024 * 1024 + 1)
    require(len(data) <= 4 * 1024 * 1024, "artifact_size")
    return data


def record(path: Path) -> dict:
    result = parse(read(path))
    require(type(result) is dict, "record_type")
    return result


def same(value: dict, identity: dict) -> None:
    require(type(value.get("schema_version")) is int and value["schema_version"] == 1, "schema_version")
    require(all(type(value.get(k)) is type(identity[k]) and value[k] == identity[k] for k in IDENTITY), "run_identity")


def timestamp(value: object) -> datetime:
    require(type(value) is str and len(value) < 64, "timestamp")
    result = datetime.fromisoformat(value)
    require(result.tzinfo is not None and result.utcoffset() == timezone.utc.utcoffset(result), "timestamp_utc")
    return result


def public(value: dict, action: str = "status") -> None:
    service_result({"name": "service", "args": [action], "outcome": value.get("outcome"), "result": value}, 2)


def verify_control(directory: Path, action: str) -> dict:
    execution = record(directory / "execution.json")
    keys(execution, {"schema_version", "action", "process_exit_code", "stdout_sha256", "stderr_sha256"}, "control_execution")
    require(type(execution["schema_version"]) is int and execution["schema_version"] == 1
            and execution["action"] == action, "control_action")
    require(type(execution["process_exit_code"]) is int and execution["process_exit_code"] == 0, "control_exit")
    stdout, stderr = read(directory / "stdout.log"), read(directory / "stderr.log")
    require(digest(stdout) == execution["stdout_sha256"] and digest(stderr) == execution["stderr_sha256"], "control_hash")
    require(not stderr, "control_stderr")
    value = parse(stdout)
    require(type(value) is dict, "control_record")
    public(value, action)
    require(value["outcome"] == "OK", "control_failed")
    return value


def verify_run(directory: Path, source_root: Path, *, require_live: bool = True) -> dict:
    require(not directory.is_symlink(), "run_symlink")
    start, result = record(directory / "start.json"), record(directory / "result.json")
    keys(start, {"schema_version", *IDENTITY, "service_kind", "pid", "boot_id", "source_only",
                 "binding_status", "management_actions", "started_at", "source_hashes"}, "start")
    require(all(type(start[k]) is str and str(uuid.UUID(start[k])) == start[k] for k in ("service_id", "instance_id", "boot_id")), "start_uuid")
    require(directory.name == start["instance_id"] and number(start["generation"], 1) and number(start["pid"], 1), "start_identity")
    same(start, start)
    require(start["service_kind"] == "CONSOLE_RUNTIME" and start["source_only"] is True
            and start["binding_status"] == "UNBOUND" and start["management_actions"] == "UNSUPPORTED", "start_boundary")
    keys(start["source_hashes"], set(SOURCES), "service_sources")
    for name in SOURCES:
        require(digest(read(source_root / name)) == start["source_hashes"][name], "service_source:" + name)
    keys(result, {"schema_version", *IDENTITY, "state", "exit_code", "reason", "observation_sequence", "completed_at", "files"}, "result")
    same(result, start)
    require(result["state"] == "STOPPED" and type(result["exit_code"]) is int and result["exit_code"] == 0
            and result["reason"] is None, "service_not_stopped")
    require(timestamp(result["completed_at"]) >= timestamp(start["started_at"]), "completion_before_start")
    keys(result["files"], set(RESULT_FILES), "result_hashes")
    for name in RESULT_FILES:
        require(digest(read(directory / name)) == result["files"][name], "service_hash:" + name)
    latest, observation = record(directory / "latest.json"), record(directory / "observation.json")
    public(latest)
    same(latest, start)
    require(latest["outcome"] == "OK" and latest["state"] == "STOPPED" and latest["pid"] == start["pid"]
            and latest["boot_id"] == start["boot_id"], "latest_state")
    keys(observation, {"schema_version", *IDENTITY, "observation_sequence", "heartbeat_monotonic_ns", "cpu", "memory",
                       "source_only", "observation_only"}, "observation")
    same(observation, start)
    require(observation["source_only"] is True and observation["observation_only"] is True, "observation_boundary")
    count = observation["observation_sequence"]
    require(number(count, 1) and type(result["observation_sequence"]) is int and result["observation_sequence"] == count
            and latest["observation_sequence"] == count and number(observation["heartbeat_monotonic_ns"], 1)
            and latest["heartbeat_monotonic_ns"] == observation["heartbeat_monotonic_ns"], "observation_sequence")
    events = [parse(line) for line in read(directory / "lifecycle.events.jsonl").splitlines()]
    require(len(events) == 4, "lifecycle_count")
    previous = -1
    for i, (event, name) in enumerate(zip(events, ("STARTING", "RUNNING", "STOPPING", "STOPPED")), 1):
        keys(event, {"schema_version", *IDENTITY, "sequence", "monotonic_ns", "event", "observation_sequence", "reason"}, "lifecycle_event")
        same(event, start)
        require(type(event["sequence"]) is int and event["sequence"] == i and event["event"] == name
                and event["reason"] is None and number(event["monotonic_ns"])
                and event["monotonic_ns"] >= previous, "lifecycle_order")
        expected_count = 0 if i == 1 else 1 if i == 2 else count
        require(type(event["observation_sequence"]) is int and event["observation_sequence"] == expected_count, "lifecycle_observation")
        previous = event["monotonic_ns"]
    require(events[0]["monotonic_ns"] <= observation["heartbeat_monotonic_ns"] <= events[2]["monotonic_ns"], "heartbeat_time")
    require((observation["heartbeat_monotonic_ns"] <= events[1]["monotonic_ns"]) == (count == 1), "heartbeat_running_order")
    boot = verify_bundle(directory / "boot", require_live=require_live, source_root=source_root, process_exit=0)
    require(boot["outcome"] == "PASS", "service_boot:" + json.dumps(boot, sort_keys=True))
    require(read(directory / "boot.stdout.log") == read(directory / "boot/boot.log"), "boot_stdout")
    boot_result = record(directory / "boot/result.json")
    require(timestamp(start["started_at"]) <= timestamp(boot_result["completed_at"])
            <= timestamp(result["completed_at"]), "boot_outside_service")
    inventory = copy.deepcopy(record(directory / "boot/inventory.json")["inventory"])
    inventory.update(cpu=observation["cpu"], memory=observation["memory"])
    require(validate_inventory(inventory) == "READY", "periodic_inventory")
    return latest


def verify_service_runs(directory: Path, *, source_root: Path | None = None, allow_absent: bool = False,
                        require_live: bool = True) -> dict:
    try:
        source_root = source_root or Path(__file__).resolve().parents[2] / "hosted/linux"
        require(not directory.is_symlink(), "state_symlink")
        if allow_absent and (not directory.exists() or not list(directory.iterdir())):
            return {"outcome": "PASS", "reasons": [], "state": "ABSENT", "runs": []}
        require(not directory.is_symlink(), "state_symlink")
        registry = record(directory / "registry.json")
        keys(registry, {"schema_version", *IDENTITY}, "registry")
        run_root = directory / "runs"
        require(not run_root.is_symlink(), "runs_symlink")
        paths = list(run_root.iterdir())
        require(1 <= len(paths) <= 64 and all(p.is_dir() and not p.is_symlink() for p in paths), "run_count")
        runs = sorted([verify_run(p, source_root, require_live=require_live) for p in paths], key=lambda r: r["generation"])
        require([r["generation"] for r in runs] == list(range(1, len(runs) + 1)), "generation_gap")
        require(len({r["service_id"] for r in runs}) == 1 and len({r["instance_id"] for r in runs}) == len(runs), "service_continuity")
        require(len({record(p / "boot/result.json")["run_id"] for p in paths}) == len(paths), "reused_boot_run")
        same(registry, runs[-1])
        require(record(directory / "latest.json") == runs[-1], "current_snapshot")
        return {"outcome": "PASS", "reasons": [], "state": "STOPPED", "runs": runs}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError) as exc:
        return {"outcome": "FAIL", "reasons": [str(exc) or type(exc).__name__]}


def verify_workflow(directory: Path, *, source_root: Path | None = None) -> dict:
    try:
        source_root = source_root or directory / "runtime-source"
        service = verify_service_runs(directory / "service", source_root=source_root)
        require(service["outcome"] == "PASS", "service_runs:" + json.dumps(service, sort_keys=True))
        runs = service["runs"]
        require(len(runs) == 3, "expected_three_generations")
        start, stop = verify_control(directory / "service-start", "start"), verify_control(directory / "service-stop", "stop")
        first = verify_execution(directory, source_root=source_root, require_live=True, require_internet=True)
        second = verify_execution(directory / "second-console", source_root=source_root, require_live=True)
        require(first["outcome"] == second["outcome"] == "PASS", "console_execution")
        sessions = [directory, directory / "second-console"]
        observed = []
        for session, expected in zip(sessions, (FIRST, SECOND)):
            require(record(session / "execution.json")["requested_commands"] == expected, "workflow_commands")
            events = [parse(line) for line in read(session / "session/session.events.jsonl").splitlines()]
            observed.append([event["data"]["result"] for event in events
                             if event["event"] == "COMMAND" and event["data"]["name"] == "service"])
        one, two = observed
        require(len(one) == 1 and len(two) == 7, "workflow_service_count")
        sequence = [start, one[0], two[0], *two[1:], stop]
        expected_generations = [1, 1, 1, 2, 2, 2, 2, 3, 3, 3]
        expected_states = ["RUNNING", "RUNNING", "RUNNING", "RUNNING", "RUNNING", "STOPPED", "STOPPED",
                           "RUNNING", "RUNNING", "STOPPED"]
        for value, gen, state in zip(sequence, expected_generations, expected_states):
            expected = runs[gen - 1]
            same(value, expected)
            require(value["outcome"] == "OK" and value["state"] == state and value["pid"] == expected["pid"]
                    and value["boot_id"] == expected["boot_id"], "workflow_source")
            require(value["observation_sequence"] <= expected["observation_sequence"]
                    and value["heartbeat_monotonic_ns"] <= expected["heartbeat_monotonic_ns"], "workflow_observation")
        for before, after in zip(sequence, sequence[1:]):
            if before["generation"] == after["generation"]:
                require(before["observation_sequence"] <= after["observation_sequence"]
                        and before["heartbeat_monotonic_ns"] <= after["heartbeat_monotonic_ns"], "workflow_regressed_observation")
        require(two[0]["observation_sequence"] > one[0]["observation_sequence"]
                and two[0]["heartbeat_monotonic_ns"] > one[0]["heartbeat_monotonic_ns"], "service_did_not_progress_between_consoles")
        require(stop == runs[-1] and two[3] == two[4] == runs[1], "stop_snapshot")
        return {"outcome": "PASS", "reasons": [], "service_id": start["service_id"],
                "generations": [r["generation"] for r in runs], "instances": [r["instance_id"] for r in runs],
                "console_survival": True, "heartbeat_progress": True, "state": "STOPPED"}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError) as exc:
        return {"outcome": "FAIL", "reasons": [str(exc) or type(exc).__name__]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--workflow", action="store_true")
    args = parser.parse_args()
    result = (verify_workflow if args.workflow else verify_service_runs)(args.directory, source_root=args.source_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["outcome"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
