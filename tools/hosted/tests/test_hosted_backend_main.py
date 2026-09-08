"""Real Linux process integration; all generated completions are fixtures."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted/linux"))
sys.path.insert(0, str(ROOT / "tools/hosted"))
from aios_agent import client as main_client
from aios_backend import client as backend_client
from aios_resources.proc import ProcessReader, ResourceError
from execution_output_contract import validate_execution
from test_hosted_backend_runtime import FIXTURE_SCRIPT
from verify_agent import verify_agent_runs


@unittest.skipUnless(platform.system() == "Linux" and hasattr(os, "pidfd_open")
                     and hasattr(socket, "SO_PEERCRED"), "Linux authenticated process lifetimes required")
class BackendMainTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aios-bm-")
        self.base = Path(self.temporary.name)
        self.main = self.base / "main"
        self.backend = self.base / "backend"
        self.script = self.base / "fixture.py"
        # Preserve the shared fixture's owned-process launch interface, adding
        # an independently counted request log for rejected-request evidence.
        self.script.write_text(FIXTURE_SCRIPT.replace(
            "request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))",
            "request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))\n"
            "        with Path(__file__).with_suffix('.requests').open('a') as out:\n"
            "            out.write(json.dumps(request)+'\\n')"), encoding="utf-8")
        self.request_log = self.script.with_suffix(".requests")
        model = self.base / "fixture.model"
        model.write_bytes(b"explicit MAIN backend replacement fixture")
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        self.config = {"schema_version": 1, "endpoint": f"http://127.0.0.1:{port}",
            "model_id": "fixture-backend", "model_path": str(model),
            "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(), "backend_path": str(self.script),
            "backend_sha256": hashlib.sha256(self.script.read_bytes()).hexdigest(), "provenance_sha256": "b" * 64}
        self.config_path = self.base / "config.json"
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def tearDown(self):
        try:
            main_client.control(self.main, "stop")
        finally:
            try:
                backend_client.control(self.backend, "stop", fixture_backend=True)
            finally:
                for owner in (main_client, backend_client):
                    for instance, child in list(owner._CHILDREN.items()):
                        owner._finish_failed_start(child)
                        owner._CHILDREN.pop(instance, None)
                self.temporary.cleanup()

    def okay(self, action, **kwargs):
        value = main_client.control(self.main, action, **kwargs)
        self.assertEqual(value["outcome"], "OK", value)
        self.assertEqual(value["capture_kind"], "fixture")
        return value

    def backend_okay(self, action, **kwargs):
        value = backend_client.control(self.backend, action, fixture_backend=True, **kwargs)
        self.assertEqual(value["outcome"], "OK", value)
        return value

    def measured_request(self, descriptor, prompt):
        value = self.okay("ask", prompt=prompt)
        receipt, resources = value["inference_receipt"], value["resource_result"]
        self.assertEqual(receipt["schema_version"], 2)
        validate_execution(receipt["backend_execution"], self.config, descriptor=descriptor)
        self.assertEqual(resources["outcome"], "OK", resources)
        self.assertEqual(resources["observation"]["request_id"], receipt["request_id"])
        self.assertEqual(resources["relation"]["backend_proof"]["descriptor"], descriptor)
        return value

    def assert_rehashed_foreign_worker_rejected(self, run):
        manifest_path = run / "result.json"
        original_manifest = manifest_path.read_bytes()
        for path in (run / "warmup.json", *sorted((run / "requests").iterdir())):
            with self.subTest(receipt=path.relative_to(run).as_posix()):
                original = path.read_bytes()
                try:
                    value = json.loads(original)
                    worker = value["backend_execution"]["send"]["client"]
                    head, tail = worker["raw_stat"].rsplit(") ", 1)
                    fields = tail.split()
                    fields[1] = str(int(fields[1]) + 1)
                    worker["raw_stat"] = head + ") " + " ".join(fields) + "\n"
                    changed = json.dumps(value, sort_keys=True, separators=(",", ":")).encode() + b"\n"
                    path.write_bytes(changed)
                    manifest = json.loads(original_manifest)
                    manifest["files"][path.relative_to(run).as_posix()] = hashlib.sha256(changed).hexdigest()
                    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
                    verified = verify_agent_runs(self.main, require_live=False)
                    self.assertEqual(verified["outcome"], "FAIL", verified)
                    self.assertTrue(any("execution_main_worker" in reason or "receipt_execution_owner" in reason
                                        for reason in verified["reasons"]), verified)
                finally:
                    path.write_bytes(original)
                    manifest_path.write_bytes(original_manifest)
        verified = verify_agent_runs(self.main, require_live=False)
        self.assertEqual(verified["outcome"], "PASS", verified)

    def test_same_endpoint_replacement_invalidates_main_until_explicit_restart_and_rebinding(self):
        first_backend = self.backend_okay("start", config=self.config_path)
        descriptor = first_backend["descriptor"]
        started = self.okay("start", config=self.config_path, fixture_backend=True, backend_dir=self.backend)
        self.okay("room-discover")
        self.okay("room-bind")
        linked = self.okay("resources-link", backend_dir=self.backend)
        self.assertEqual(linked["resource_result"]["relation"]["relation_generation"], 1)
        ready = self.measured_request(descriptor, "Before explicit backend replacement.")
        source = ready["source_record"]
        run = self.main / "runs" / source["source_instance"]
        request_files = sorted(path.name for path in (run / "requests").iterdir())
        self.assertEqual(len(request_files), 1)

        with ProcessReader(source["process_id"]) as main_reader, \
                ProcessReader(descriptor["process_id"], descriptor["process_start_ticks"], descriptor["host_boot_id"]) as old_backend:
            replacement = self.backend_okay("restart")
            new_descriptor = replacement["descriptor"]
            self.assertEqual(new_descriptor["endpoint"], descriptor["endpoint"])
            self.assertNotEqual(new_descriptor["source_instance"], descriptor["source_instance"])
            with self.assertRaises(ResourceError):
                old_backend.sample()
            request_bytes = self.request_log.read_bytes()
            invalidated = self.okay("status")
            expected = {**source, "source_generation": source["source_generation"] + 1, "model_ready": False}
            self.assertEqual(invalidated["source_record"], expected)
            self.assertEqual(main_reader.sample()["process_id"], source["process_id"])
            self.assertFalse(invalidated["management_snapshot"]["binding_current"])
            self.assertEqual(self.okay("status")["source_record"], expected)
            rejected = main_client.control(self.main, "ask", prompt="Do not deliver to the replacement.")
            self.assertEqual(rejected["outcome"], "ERROR", rejected)
            self.assertEqual(rejected["error"], "model-not-ready")
            self.assertIsNone(rejected["inference_receipt"])
            self.assertEqual(rejected["source_record"], expected)
            self.assertEqual(self.request_log.read_bytes(), request_bytes)
            self.assertEqual(sorted(path.name for path in (run / "requests").iterdir()), request_files)
            events = [json.loads(line) for line in (run / "events.jsonl").read_bytes().splitlines()]
            invalidations = [row for row in events if row["event"] == "BACKEND_INVALIDATED"]
            self.assertEqual(len(invalidations), 1)
            self.assertEqual(invalidations[0]["error"], "backend-changed")
            self.assertIsNone(invalidations[0]["receipt_file"])
            resumed = self.okay("restart", fixture_backend=True, backend_dir=self.backend)
            with self.assertRaises(ResourceError):
                main_reader.sample()

        self.assertNotEqual(resumed["source_record"]["source_instance"], started["source_record"]["source_instance"])
        self.assertEqual(resumed["source_record"]["service_start_generation"], 2)
        self.assertTrue(resumed["source_record"]["model_ready"])
        denied = main_client.control(self.main, "ask", prompt="Explicit reconciliation is still required.")
        self.assertEqual(denied["outcome"], "ERROR", denied)
        self.assertIsNone(denied["inference_receipt"])
        self.okay("room-discover")
        rebound = self.okay("room-reconcile")
        self.assertEqual(rebound["management_snapshot"]["binding"]["generation"], 2)
        relinked = self.okay("resources-link", backend_dir=self.backend)
        self.assertEqual(relinked["resource_result"]["relation"]["relation_generation"], 2)
        self.measured_request(new_descriptor, "After explicit MAIN restart, reconciliation, and resource relink.")
        self.assertEqual(len(self.request_log.read_bytes().splitlines()), 4, "two warmups and two user requests only")
        self.assertEqual(self.okay("stop")["state"], "STOPPED")
        verified = verify_agent_runs(self.main, require_live=False)
        self.assertEqual(verified["outcome"], "PASS", verified)
        self.assertEqual(len(verified["runs"]), 2)
        self.assertEqual(verify_agent_runs(self.main, require_live=True)["outcome"], "FAIL")
        self.assert_rehashed_foreign_worker_rejected(run)
        terminal = self.backend_okay("stop")
        self.assertEqual(terminal["state"], "STOPPED")


if __name__ == "__main__":
    unittest.main()
