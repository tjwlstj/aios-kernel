"""AIOS userspace startup evidence producer (not a native kernel boot verdict)."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from . import VERSION
from .hardware import SECTIONS, collect_hardware


def encoded(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def source_hashes() -> dict:
    package = Path(__file__).resolve().parent
    files = [package.parent / "aios-boot.py", *sorted(package.glob("*.py"))]
    return {str(path.relative_to(package.parent)).replace("\\", "/"):
            hashlib.sha256(path.read_bytes()).hexdigest() for path in files}


def run_boot(destination: Path, *, proc_root: Path = Path("/proc"),
             sys_root: Path = Path("/sys"), test_system: str | None = None) -> int:
    # A new directory is mandatory. A previous run can never become this run's evidence.
    destination.mkdir(parents=True, exist_ok=False)
    run_id = str(uuid.uuid4())
    events: list[dict] = []
    lines: list[str] = []
    start = time.monotonic_ns()

    def emit(name: str, data: dict) -> None:
        event = {"schema_version": 1, "run_id": run_id, "sequence": len(events) + 1,
                 "elapsed_ns": time.monotonic_ns() - start, "event": name, "data": data}
        events.append(event)
        line = "[AIOS-BOOT] " + encoded(event).decode("utf-8").rstrip()
        lines.append(line)
        # Flush every phase so a startup failure remains observable immediately.
        print(line, flush=True)
        with (destination / "events.jsonl").open("ab") as stream:
            stream.write(encoded(event))
        with (destination / "boot.log").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line + "\n")

    fixture = test_system is not None or proc_root != Path("/proc") or sys_root != Path("/sys")
    system = test_system if test_system is not None else platform.system()
    emit("START", {"product": "AIOS", "runtime_version": VERSION, "boot_kind": "userspace-startup",
                   "capture_kind": "fixture" if fixture else "live", "source_only": True,
                   "observation_only": True, "binding_status": "UNBOUND",
                   "action_support": "UNSUPPORTED"})
    environment = {"system": system, "kernel_release": platform.release(),
                   "architecture": platform.machine(), "python": platform.python_version(),
                   "visibility": "linux-visible" if system == "Linux" else "unsupported",
                   "wsl": "microsoft" in platform.release().lower(),
                   "physical_host_inventory": False}
    emit("SUBSTRATE", environment)
    inventory = None
    diagnostic = None
    if system != "Linux":
        state, exit_code = "UNSUPPORTED", 3
        diagnostic = "A Linux kernel with mounted procfs/sysfs is required."
    else:
        try:
            inventory = collect_hardware(proc_root, sys_root)
            for name in SECTIONS:
                observed = inventory[name]
                data = observed["data"]
                detail = {"section": name, "status": observed["status"], "errors": observed["errors"]}
                if name in ("cpu", "memory"):
                    detail["values"] = data
                else:
                    detail["count"] = len(data)
                    detail["driver_bound_count"] = sum(item["driver_bound"] for item in data)
                    detail["devices"] = data
                emit(name.upper(), detail)
            if any(inventory[name]["status"] != "observed" for name in ("cpu", "memory")):
                state, exit_code = "FAILED", 1
            elif any(inventory[name]["status"] != "observed" for name in SECTIONS[2:]):
                state, exit_code = "DEGRADED", 2
            else:
                state, exit_code = "READY", 0
        except Exception as exc:
            # An unexpected collector failure must leave failure evidence, never READY.
            state, exit_code = "FAILED", 1
            diagnostic = "collector_exception:" + type(exc).__name__
    raw_inventory = encoded({"schema_version": 1, "run_id": run_id, "inventory": inventory})
    (destination / "inventory.json").write_bytes(raw_inventory)
    emit(state, {"exit_code": exit_code, "diagnostic": diagnostic,
                 "inventory_sha256": hashlib.sha256(raw_inventory).hexdigest()})
    emit("STOP", {"exit_code": exit_code, "state": state})
    result = {"schema_version": 1, "run_id": run_id, "state": state, "exit_code": exit_code,
              "completed_at": datetime.now(timezone.utc).isoformat(), "evidence_only": True,
              "source_hashes": source_hashes(),
              "files": {name: hashlib.sha256((destination / name).read_bytes()).hexdigest()
                        for name in ("boot.log", "events.jsonl", "inventory.json")}}
    (destination / "result.json").write_bytes(encoded(result))
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Start AIOS userspace observation and print Linux-visible hardware.")
    parser.add_argument("--artifact-dir", type=Path, required=True, help="New directory for this run; existing paths are refused.")
    args = parser.parse_args()
    try:
        return run_boot(args.artifact_dir)
    except FileExistsError:
        print("AIOS startup refused: artifact directory already exists; choose a new run directory.", file=sys.stderr)
        return 4
    except OSError as exc:
        print("AIOS startup artifact error: " + type(exc).__name__, file=sys.stderr)
        return 4
