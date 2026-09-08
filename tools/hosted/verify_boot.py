#!/usr/bin/env python3
"""Independent fail-closed verifier for a single AIOS userspace startup bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SECTIONS = ("cpu", "memory", "pci", "usb", "block", "net")
ARTIFACTS = ("boot.log", "events.jsonl", "inventory.json", "result.json")
STATES = {"READY": 0, "FAILED": 1, "DEGRADED": 2, "UNSUPPORTED": 3}
LIMIT = 4 * 1024 * 1024


def object_pairs(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_key:" + key)
        result[key] = value
    return result


def parse(raw: bytes) -> object:
    return json.loads(raw.decode("utf-8"), object_pairs_hook=object_pairs,
                      parse_constant=lambda value: (_ for _ in ()).throw(ValueError("non_finite")))


def require(condition: bool, reason: str) -> None:
    if not condition:
        raise ValueError(reason)


def keys(value: object, expected: set, name: str) -> None:
    require(type(value) is dict and set(value) == expected, "fields:" + name)


def integer(value: object, minimum: int = 0) -> bool:
    return type(value) is int and minimum <= value < 1 << 63


def strings(value: object, maximum: int = 32) -> bool:
    return type(value) is list and len(value) <= maximum and all(type(v) is str and len(v) <= 1024 for v in value)


def validate_inventory(inventory: dict) -> str:
    keys(inventory, {"schema_version", "scope", "source_only", "observation_only", "consistency", *SECTIONS}, "inventory")
    require(type(inventory["schema_version"]) is int and inventory["schema_version"] == 1, "inventory_schema")
    require(inventory["scope"] == "linux-visible" and inventory["source_only"] is True and
            inventory["observation_only"] is True and inventory["consistency"] == "best-effort", "inventory_boundary")
    for name in SECTIONS:
        value = inventory[name]
        keys(value, {"status", "source", "data", "errors"}, name)
        require(value["status"] in ("observed", "partial", "unavailable") and
                type(value["source"]) is str and value["source"].startswith(("/sys/", "/proc/")), "section_status:" + name)
        require(strings(value["errors"]), "section_errors:" + name)
        require((value["status"] == "observed") == (not value["errors"]), "section_error_status:" + name)
        data = value["data"]
        if name == "cpu":
            keys(data, {"logical_count", "model"}, "cpu_data")
            require(data["model"] is None or (type(data["model"]) is str and len(data["model"]) <= 256), "cpu_model")
            if value["status"] == "observed":
                require(integer(data["logical_count"], 1), "cpu_count")
        elif name == "memory":
            keys(data, {"total_bytes", "available_bytes", "available_is_estimate"}, "memory_data")
            require(data["available_is_estimate"] is True, "memory_estimate")
            if value["status"] == "observed":
                require(integer(data["total_bytes"], 1), "memory_total")
                available = data["available_bytes"]
                require(available is None or (integer(available) and available <= data["total_bytes"]), "memory_available")
        else:
            require(type(data) is list and len(data) <= 256, "device_capacity:" + name)
            names = set()
            for row in data:
                base = {"name", "source_path", "driver", "driver_bound", "usability", "errors"}
                fields = {"pci": {"vendor", "device", "class_code", "subsystem_vendor", "subsystem_device"},
                          "usb": {"vendor", "device", "class_code", "bus_number", "device_number"},
                          "block": {"size_bytes", "partition", "logical_block_size"},
                          "net": {"ifindex", "type", "operstate"}}[name]
                require(type(row) is dict and base <= set(row) <= base | fields, "device_fields:" + name)
                require(type(row["name"]) is str and row["name"] not in names and 0 < len(row["name"]) <= 255, "device_name")
                names.add(row["name"])
                require(strings(row["errors"]) and row["usability"] == "UNTESTED", "device_boundary")
                require(type(row["driver_bound"]) is bool and row["driver_bound"] == (row["driver"] is not None), "driver_binding")
                require(row["driver"] is None or (type(row["driver"]) is str and 0 < len(row["driver"]) <= 255), "driver_name")
                if row["errors"]:
                    require(value["status"] != "observed", "device_error_status")
                    continue
                require(set(row) == base | fields and type(row["source_path"]) is str and
                        row["source_path"].startswith("/sys/") and "/../" not in row["source_path"], "device_source")
                if name in ("pci", "usb"):
                    for field, digits in (("vendor", 4), ("device", 4), ("class_code", 6 if name == "pci" else 2)):
                        require(type(row[field]) is str and bool(re.fullmatch(r"0x[0-9a-f]{" + str(digits) + "}", row[field])), "device_id")
                    if name == "pci":
                        for field in ("subsystem_vendor", "subsystem_device"):
                            require(row[field] is None or (type(row[field]) is str and bool(re.fullmatch(r"0x[0-9a-f]{4}", row[field]))), "subsystem_id")
                    else:
                        require(integer(row["bus_number"], 1) and integer(row["device_number"], 1), "usb_address")
                elif name == "block":
                    require(integer(row["size_bytes"]) and row["size_bytes"] % 512 == 0, "block_size")
                    for field in ("partition", "logical_block_size"):
                        require(row[field] is None or integer(row[field], 1), "block_attribute")
                else:
                    require(integer(row["ifindex"], 1) and integer(row["type"]) and row["operstate"] in
                            ("unknown", "notpresent", "down", "lowerlayerdown", "testing", "dormant", "up"), "net_status")
    if any(inventory[name]["status"] != "observed" for name in SECTIONS[:2]):
        return "FAILED"
    return "READY" if all(inventory[name]["status"] == "observed" for name in SECTIONS) else "DEGRADED"


def verify_bundle(directory: Path, *, require_live: bool = False, expected_run_id: str | None = None,
                  process_exit: int | None = None, source_root: Path | None = None) -> dict:
    try:
        raw = {}
        for name in ARTIFACTS:
            path = directory / name
            require(not path.is_symlink(), "artifact_symlink")
            with path.open("rb") as stream:
                raw[name] = stream.read(LIMIT + 1)
            require(len(raw[name]) <= LIMIT, "artifact_size")
        result, snapshot = parse(raw["result.json"]), parse(raw["inventory.json"])
        keys(result, {"schema_version", "run_id", "state", "exit_code", "completed_at", "evidence_only", "source_hashes", "files"}, "result")
        keys(snapshot, {"schema_version", "run_id", "inventory"}, "snapshot")
        run_id = result["run_id"]
        require(type(run_id) is str and bool(re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", run_id)), "run_id")
        require(expected_run_id is None or run_id == expected_run_id, "stale_run")
        require(snapshot["run_id"] == run_id and type(snapshot["schema_version"]) is int and snapshot["schema_version"] == 1, "snapshot_identity")
        require(type(result["schema_version"]) is int and result["schema_version"] == 1 and result["evidence_only"] is True, "result_schema")
        keys(result["files"], set(ARTIFACTS[:-1]), "hashes")
        for name, digest in result["files"].items():
            require(hashlib.sha256(raw[name]).hexdigest() == digest, "artifact_hash:" + name)
        hashes = result["source_hashes"]
        require(type(hashes) is dict and set(hashes) == {"aios-boot.py", "aios_hosted/__init__.py", "aios_hosted/boot.py", "aios_hosted/hardware.py"}, "source_files")
        require(all(type(v) is str and re.fullmatch(r"[0-9a-f]{64}", v) for v in hashes.values()), "source_hashes")
        source_root = source_root or Path(__file__).resolve().parents[2] / "hosted/linux"
        for name, digest in hashes.items():
            require(hashlib.sha256((source_root / name).read_bytes()).hexdigest() == digest, "source_hash_mismatch:" + name)
        events = [parse(line) for line in raw["events.jsonl"].splitlines()]
        require(4 <= len(events) <= 10, "event_count")
        previous_time = -1
        for sequence, event in enumerate(events, 1):
            keys(event, {"schema_version", "run_id", "sequence", "elapsed_ns", "event", "data"}, "event")
            require(type(event["data"]) is dict and type(event["event"]) is str, "event_payload")
            require(type(event["schema_version"]) is int and event["schema_version"] == 1 and event["run_id"] == run_id,
                    "event_identity")
            require(type(event["sequence"]) is int and event["sequence"] == sequence and integer(event["elapsed_ns"]) and
                    event["elapsed_ns"] >= previous_time, "event_sequence")
            previous_time = event["elapsed_ns"]
        expected_log = "".join("[AIOS-BOOT] " + json.dumps(event, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n" for event in events)
        require(raw["boot.log"] == expected_log.encode("utf-8"), "log_event_mismatch")
        start = events[0]["data"]
        keys(start, {"product", "runtime_version", "boot_kind", "capture_kind", "source_only", "observation_only", "binding_status", "action_support"}, "start")
        require(start["product"] == "AIOS" and start["runtime_version"] == "0.1.0" and start["boot_kind"] == "userspace-startup" and
                start["capture_kind"] in ("live", "fixture") and start["source_only"] is True and start["observation_only"] is True and
                start["binding_status"] == "UNBOUND" and start["action_support"] == "UNSUPPORTED", "startup_boundary")
        require(not require_live or start["capture_kind"] == "live", "fixture_not_live")
        environment = events[1]["data"]
        keys(environment, {"system", "kernel_release", "architecture", "python", "visibility", "wsl", "physical_host_inventory"}, "substrate")
        require(environment["physical_host_inventory"] is False and type(environment["wsl"]) is bool, "visibility_boundary")
        for field in ("system", "kernel_release", "architecture", "python", "visibility"):
            require(type(environment[field]) is str and bool(environment[field]), "environment_value")
        state = result["state"]
        require(type(state) is str and state in STATES and type(result["exit_code"]) is int and result["exit_code"] == STATES[state], "state_exit")
        require(process_exit is None or process_exit == result["exit_code"], "process_exit_mismatch")
        inventory = snapshot["inventory"]
        if inventory is None:
            unsupported = state == "UNSUPPORTED" and environment["system"] != "Linux" and environment["visibility"] == "unsupported"
            diagnostic = events[-2]["data"].get("diagnostic")
            exception = state == "FAILED" and environment["system"] == "Linux" and type(diagnostic) is str and diagnostic.startswith("collector_exception:")
            require(unsupported or exception, "missing_inventory")
            expected = ["START", "SUBSTRATE", state, "STOP"]
        else:
            require(environment["system"] == "Linux" and environment["visibility"] == "linux-visible", "linux_required")
            require(state == validate_inventory(inventory), "inventory_state_mismatch")
            expected = ["START", "SUBSTRATE", *(name.upper() for name in SECTIONS), state, "STOP"]
            for event, name in zip(events[2:8], SECTIONS):
                section = inventory[name]
                detail = {"section": name, "status": section["status"], "errors": section["errors"]}
                if name in SECTIONS[:2]:
                    detail["values"] = section["data"]
                else:
                    detail.update(count=len(section["data"]), driver_bound_count=sum(row["driver_bound"] for row in section["data"]), devices=section["data"])
                # Canonical serialization distinguishes bool from integer.
                require(json.dumps(event["data"], sort_keys=True) == json.dumps(detail, sort_keys=True), "section_event_mismatch")
        require([event["event"] for event in events] == expected, "terminal_order")
        terminal = events[-2]["data"]
        keys(terminal, {"exit_code", "diagnostic", "inventory_sha256"}, "terminal")
        require(type(terminal["exit_code"]) is int and terminal["exit_code"] == result["exit_code"] and
                terminal["inventory_sha256"] == result["files"]["inventory.json"], "terminal_hash_exit")
        require(state != "READY" or terminal["diagnostic"] is None, "ready_diagnostic")
        keys(events[-1]["data"], {"exit_code", "state"}, "stop")
        require(type(events[-1]["data"]["exit_code"]) is int and events[-1]["data"] == {"exit_code": result["exit_code"], "state": state}, "stop_state")
        return {"schema_version": 1, "outcome": "PASS" if state == "READY" else state,
                "run_id": run_id, "capture_kind": start["capture_kind"], "state": state, "reasons": []}
    except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError) as exc:
        return {"schema_version": 1, "outcome": "FAIL", "reasons": [str(exc)]}


def verify_execution(directory: Path, *, require_live: bool = True, source_root: Path | None = None,
                     expected_run_id: str | None = None) -> dict:
    """Recheck saved process evidence as well as the runtime's own result."""
    try:
        raw = {}
        for name in ("process.json", "stdout.log", "stderr.log"):
            with (directory / name).open("rb") as stream:
                raw[name] = stream.read(LIMIT + 1)
            require(len(raw[name]) <= LIMIT, "process_artifact_size")
        process = parse(raw["process.json"])
        keys(process, {"outcome", "exit_code", "command", "stdout_sha256", "stderr_sha256"}, "process")
        require(process["outcome"] == "exited" and type(process["exit_code"]) is int and process["exit_code"] == 0, "process_not_clean")
        require(strings(process["command"], 32) and bool(process["command"]), "process_command")
        for name in ("stdout", "stderr"):
            require(hashlib.sha256(raw[name + ".log"]).hexdigest() == process[name + "_sha256"], "process_hash:" + name)
        require(not raw["stderr.log"], "process_stderr")
        verdict = verify_bundle(directory / "run", require_live=require_live, process_exit=process["exit_code"],
                                source_root=source_root, expected_run_id=expected_run_id)
        require(verdict["outcome"] == "PASS", "runtime_verdict:" + str(verdict))
        require(raw["stdout.log"] == (directory / "run/boot.log").read_bytes(), "stdout_log_mismatch")
        return verdict
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        return {"schema_version": 1, "outcome": "FAIL", "reasons": [str(exc)]}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("artifact_dir", type=Path)
    parser.add_argument("--require-live", action="store_true")
    parser.add_argument("--run-id")
    parser.add_argument("--source-root", type=Path, help="Retained runtime snapshot; defaults to the current checkout's hosted/linux.")
    parser.add_argument("--execution", action="store_true", help="Also check process.json/stdout/stderr, with runtime artifacts in run/.")
    args = parser.parse_args()
    if args.execution:
        result = verify_execution(args.artifact_dir, require_live=args.require_live, source_root=args.source_root,
                                  expected_run_id=args.run_id)
    else:
        result = verify_bundle(args.artifact_dir, require_live=args.require_live, expected_run_id=args.run_id, source_root=args.source_root)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["outcome"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
