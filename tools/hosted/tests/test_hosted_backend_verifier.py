"""Rehashed fixture forgeries cannot become backend lifecycle evidence."""
from __future__ import annotations

import ast
import copy
import hashlib
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/hosted"))
from verify_backend import FILES, SOURCES, verify_backend_runs, verify_control


def raw(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode() + b"\n"


def save(path, value):
    path.write_bytes(raw(value))


def read(path):
    return json.loads(path.read_bytes())


def rehash(run):
    value = read(run / "result.json")
    value["files"] = {name: hashlib.sha256((run / name).read_bytes()).hexdigest() for name in FILES}
    save(run / "result.json", value)


def fixture(base, count=1):
    state, source = base / "state", base / "source"
    (state / "runs").mkdir(parents=True)
    source.mkdir()
    hashes = {}
    for name in SOURCES:
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"# Explicit fixture source\n")
        hashes[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    service_id, boot = str(uuid.uuid4()), str(uuid.uuid4())
    config = {"schema_version": 1, "endpoint": "http://127.0.0.1:18081", "model_id": "fixture-backend",
        "model_path": "/fixture/model", "model_sha256": "1" * 64, "backend_path": "/fixture/backend.py",
        "backend_sha256": "2" * 64, "provenance_sha256": "3" * 64}
    paths = []
    for generation in range(1, count + 1):
        identity = {"schema_version": 1, "service_id": service_id, "instance_id": str(uuid.uuid4()),
                    "start_generation": generation}
        run = state / "runs" / identity["instance_id"]
        run.mkdir()
        paths.append(run)
        parent = {"host_boot_id": boot, "process_id": 200 + generation, "process_start_ticks": generation * 100, "uid": 1000}
        child = {**parent, "process_id": 300 + generation, "process_start_ticks": generation * 100 + 1}
        service = {**identity, "supervisor_identity": parent, "child_identity": None,
            "lifecycle_state": "starting", "backend_ready": False, "config_sha256": hashlib.sha256(raw(config)).hexdigest(),
            "profile": "python-fixture", "source_only": True, "backend_source_instance": None}
        descriptor = {"schema_version": 1, "source_namespace": "linux-model-backend", "source_instance": str(uuid.uuid4()),
            "source_generation": 1, "lifecycle_state": "active", "source_only": True, "producer_owned": True,
            **{key: child[key] for key in ("host_boot_id", "process_id", "process_start_ticks")},
            "launcher_process_id": parent["process_id"], "launcher_start_ticks": parent["process_start_ticks"],
            **{key: config[key] for key in ("model_id", "model_sha256", "backend_sha256", "endpoint")},
            "listener_inode": 10000 + generation}
        events = []
        for index, name in enumerate(("STARTING", "CHILD_STARTED", "RUNNING", "STOPPING", "STOPPED"), 1):
            if index == 2:
                service["child_identity"] = child
            if index == 3:
                service.update(lifecycle_state="active", backend_ready=True, backend_source_instance=descriptor["source_instance"])
            if index == 4:
                service["backend_ready"] = False
            if index == 5:
                service["lifecycle_state"] = "exited"
            events.append({"schema_version": 1, "instance_id": identity["instance_id"], "sequence": index,
                "monotonic_ns": generation * 1000 + index * 100, "event": name, "outcome": "OK", "error": None,
                "service_record": copy.deepcopy(service), "descriptor": copy.deepcopy(descriptor) if index == 3 else None})
        start = {**identity, "started_at": f"2026-09-08T00:00:{generation * 10:02d}+00:00", "capture_kind": "fixture",
            "profile": "python-fixture", "config_sha256": service["config_sha256"], "source_hashes": hashes,
            "command": ["/usr/bin/python3", config["backend_path"], "18081"]}
        proof = {"schema_version": 1, "nonce": str(uuid.uuid4()), "descriptor": descriptor,
            "listener_proof": {"raw_tcp_line": f"0: 0100007F:46A1 00000000:0000 0A 00000000:00000000 00:00000000 00000000 1000 0 {10000 + generation}",
                "fd_target": f"socket:[{10000 + generation}]", "fd_number": 3},
            "peer_pid": parent["process_id"], "peer_uid": 1000,
            "observed_monotonic_ns": generation * 1000 + 350, "capture_kind": "fixture"}
        for name, value in (("start.json", start), ("config.json", config), ("service.json", service),
                            ("backend-source.json", descriptor), ("health.json", {"status": "ok"})):
            save(run / name, value)
        (run / "events.jsonl").write_bytes(b"".join(raw(event) for event in events))
        (run / "backend-attestations.jsonl").write_bytes(raw(proof))
        (run / "stdout.log").write_bytes(b"fixture output\n")
        (run / "stderr.log").write_bytes(b"")
        result = {**identity, "state": "STOPPED", "exit_code": 0, "error": None,
            "completed_at": f"2026-09-08T00:00:{generation * 10 + 1:02d}+00:00", "capture_kind": "fixture",
            "service_record": copy.deepcopy(service), "descriptor": descriptor, "child_exit_code": 0,
            "child_exit_verified": True, "forced": False, "log_bytes": {"stdout": 15, "stderr": 0}, "files": {}}
        save(run / "result.json", result)
        rehash(run)
        save(state / "registry.json", identity)
        save(state / "config.json", config)
        save(state / "latest.json", {"schema_version": 1, "action": "status", "outcome": "OK", "error": None,
            "state": "STOPPED", "service_kind": "MODEL_BACKEND", "capture_kind": "fixture",
            "service_record": service, "descriptor": None, "resource_actions": "UNSUPPORTED"})
    return state, source, paths


class BackendVerifierTests(unittest.TestCase):
    def test_complete_fixture_and_two_run_same_endpoint_history(self):
        with tempfile.TemporaryDirectory() as temporary:
            state, source, _runs = fixture(Path(temporary), 2)
            value = verify_backend_runs(state, source)
            self.assertEqual(value["outcome"], "PASS", value)
            self.assertEqual(len(value["runs"]), 2)
            self.assertEqual(verify_backend_runs(state, source, require_live=True)["outcome"], "FAIL")

    def rejected(self, mutate):
        with tempfile.TemporaryDirectory() as temporary:
            state, source, runs = fixture(Path(temporary))
            mutate(state, source, runs[0])
            rehash(runs[0])
            result = verify_backend_runs(state, source)
            self.assertEqual(result["outcome"], "FAIL", result)

    def test_exit_and_log_claims_fail_after_rehash(self):
        for changes in ({"child_exit_verified": False}, {"forced": True}, {"child_exit_code": 1},
                        {"exit_code": False}, {"log_bytes": {"stdout": 14, "stderr": 0}}):
            self.rejected(lambda _state, _source, run, changes=changes:
                          save(run / "result.json", {**read(run / "result.json"), **changes}))

    def test_changed_process_or_early_ready_rejects_complete_rehashed_event(self):
        def change(_state, _source, run):
            events = [json.loads(line) for line in (run / "events.jsonl").read_bytes().splitlines()]
            events[1]["service_record"]["backend_ready"] = True
            (run / "events.jsonl").write_bytes(b"".join(raw(event) for event in events))
        self.rejected(change)

    def test_forged_backend_listener_rejected_after_proof_rehash(self):
        def change(_state, _source, run):
            proof = read(run / "backend-attestations.jsonl")
            proof["listener_proof"]["fd_target"] = "socket:[99999]"
            save(run / "backend-attestations.jsonl", proof)
        self.rejected(change)

    def test_duplicate_nonce_and_outside_lifetime_proof_fail(self):
        def duplicate(_state, _source, run):
            path = run / "backend-attestations.jsonl"
            path.write_bytes(path.read_bytes() * 2)
        self.rejected(duplicate)
        def outside(_state, _source, run):
            proof = read(run / "backend-attestations.jsonl")
            proof["observed_monotonic_ns"] = 1501
            save(run / "backend-attestations.jsonl", proof)
        self.rejected(outside)

    def test_config_hash_and_source_snapshot_are_checked(self):
        self.rejected(lambda _state, _source, run: save(run / "config.json", {**read(run / "config.json"), "model_id": "changed"}))
        self.rejected(lambda _state, source, _run: (source / SOURCES[0]).write_bytes(b"changed source"))

    def test_unaccounted_files_and_duplicate_json_fields_fail(self):
        self.rejected(lambda _state, _source, run: (run / "extra.json").write_bytes(b"{}"))
        self.rejected(lambda state, _source, _run: (state / "registry.json").write_bytes(b'{"schema_version":1,"schema_version":1}'))

    def test_start_generation_gap_cannot_be_relabelled_latest(self):
        def change(state, _source, run):
            start = read(run / "start.json")
            start["start_generation"] = 2
            save(run / "start.json", start)
            latest = read(state / "registry.json")
            latest["start_generation"] = 2
            save(state / "registry.json", latest)
        self.rejected(change)

    def test_control_execution_requires_exact_output_and_fixture_is_not_live(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            state, _source, _runs = fixture(base)
            output = base / "control"
            output.mkdir()
            value = {**read(state / "latest.json"), "action": "stop"}
            save(output / "stdout.log", value)
            (output / "stderr.log").write_bytes(b"")
            execution = {"schema_version": 1, "action": "stop", "process_exit_code": 0,
                "stdout_sha256": hashlib.sha256((output / "stdout.log").read_bytes()).hexdigest(),
                "stderr_sha256": hashlib.sha256(b"").hexdigest()}
            save(output / "execution.json", execution)
            self.assertEqual(verify_control(output, "stop", require_live=False)["outcome"], "PASS")
            self.assertEqual(verify_control(output, "stop", require_live=True)["outcome"], "FAIL")
            execution["process_exit_code"] = False
            save(output / "execution.json", execution)
            self.assertEqual(verify_control(output, "stop", require_live=False)["outcome"], "FAIL")

    def test_independent_verifier_imports_no_runtime_producer(self):
        tree = ast.parse((ROOT / "tools/hosted/verify_backend.py").read_text(encoding="utf-8"))
        modules = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any(name and name.startswith("aios_") for name in modules))


if __name__ == "__main__":
    unittest.main()
