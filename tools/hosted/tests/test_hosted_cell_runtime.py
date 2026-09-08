"""Actual Linux Cell lifecycle; model answers remain explicit fixture evidence."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import select
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted" / "linux"))
sys.path.insert(0, str(ROOT / "tools" / "hosted"))
from aios_agent import client
from aios_resources.backend import BackendAttester
from aios_resources.proc import ProcessReader, ResourceError
from verify_agent import verify_agent_runs


@unittest.skipUnless(platform.system() == "Linux" and hasattr(os, "pidfd_open")
                     and hasattr(socket, "SO_PEERCRED") and Path("/proc/self/stat").is_file(),
                     "Linux process identity and authenticated resource IPC required")
class CellRuntimeTests(unittest.TestCase):
    @contextmanager
    def backend(self):
        with tempfile.TemporaryDirectory(prefix="aios-cell-") as temporary:
            base = Path(temporary)
            self.state, self.output = base / "main", base / "backend"
            self.output.mkdir(mode=0o700)
            self.request_log = base / "requests.log"
            self.request_log.write_bytes(b"")
            script = base / "fixture_backend.py"
            script.write_text('''import http.server,json,sys,time
class Handler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        with open(sys.argv[1],'ab') as log:
            log.write(b'request\\n')
        deadline=time.process_time()+0.04
        while time.process_time()<deadline:
            pass
        value={'content':'Explicit Cell fixture completion.','tokens_predicted':4,'model':'fixture-cell-main'}
        raw=json.dumps(value).encode()
        self.send_response(200)
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def log_message(self,*args):
        pass
server=http.server.HTTPServer(('127.0.0.1',0),Handler)
print(server.server_port,flush=True)
server.serve_forever()
''', encoding="utf-8")
            model = base / "fixture.model"
            model.write_bytes(b"explicit Cell lifecycle fixture model")
            child = subprocess.Popen([sys.executable, str(script), str(self.request_log)],
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, start_new_session=True)
            attester = thread = None
            stopping = threading.Event()
            errors = []
            try:
                self.assertTrue(select.select([child.stdout], [], [], 10)[0], "backend did not listen")
                port = int(child.stdout.readline())
                config = {"schema_version": 1, "endpoint": f"http://127.0.0.1:{port}",
                    "model_id": "fixture-cell-main", "model_path": str(model),
                    "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
                    "backend_path": str(script), "backend_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
                    "provenance_sha256": "c" * 64}
                self.config = base / "config.json"
                self.config.write_text(json.dumps(config), encoding="utf-8")
                attester = BackendAttester(self.output, child, config, capture_kind="fixture")

                def poll():
                    try:
                        while not stopping.is_set():
                            attester.poll()
                            time.sleep(0.01)
                    except Exception as exc:
                        errors.append(exc)

                thread = threading.Thread(target=poll, daemon=True)
                thread.start()
                yield
                self.assertFalse(errors, errors)
            finally:
                # Stop only processes launched by this fixture. Observer cleanup
                # must not prevent cleanup of the separately owned backend.
                try:
                    client.control(self.state, "stop")
                finally:
                    stopping.set()
                    if thread is not None:
                        thread.join(timeout=5)
                    try:
                        if attester is not None:
                            attester.close()
                    finally:
                        if child.poll() is None:
                            child.terminate()
                            try:
                                child.wait(timeout=5)
                            except subprocess.TimeoutExpired:
                                child.kill()
                                child.wait(timeout=5)
                        if child.stdout is not None:
                            child.stdout.close()
                        for instance, owned in list(client._CHILDREN.items()):
                            if owned.poll() is None:
                                client._finish_failed_start(owned)
                            client._CHILDREN.pop(instance, None)

    def command(self, action, **kwargs):
        return client.control(self.state, action, **kwargs)

    def okay(self, action, **kwargs):
        value = self.command(action, **kwargs)
        self.assertEqual(value["outcome"], "OK", value)
        self.assertEqual(value["capture_kind"], "fixture")
        return value

    def initial_binding(self):
        self.okay("start", config=self.config, fixture_backend=True)
        self.okay("room-discover")
        self.okay("room-bind")
        linked = self.okay("resources-link", backend_dir=self.output)
        self.assertEqual(linked["resource_result"]["relation"]["relation_generation"], 1)
        self.measured_request("Before the Cell lifecycle change.")
        return self.okay("status")

    def measured_request(self, prompt):
        sampled = self.okay("resources-sample")
        self.assertEqual(sampled["resource_result"]["observation"]["kind"], "sample")
        value = self.okay("ask", prompt=prompt)
        receipt, resources = value["inference_receipt"], value["resource_result"]
        self.assertEqual(receipt["outcome"], "OK")
        self.assertEqual(resources["outcome"], "OK", resources)
        self.assertTrue(resources["relation_current"])
        self.assertGreater(resources["observation"]["cpu"]["backend"]["cpu_time_ns"], 0)
        self.assertEqual(resources["observation"]["request_id"], receipt["request_id"])
        return value

    def assert_cell_read_only(self, expected):
        source = expected["source_record"]
        events = self.state / "runs" / source["source_instance"] / "events.jsonl"
        state_before = (self.state / "management.json").read_bytes()
        events_before = events.read_bytes()
        value = self.okay("cell-status")
        self.assertEqual(value["management_outcome"], "accepted")
        self.assertEqual(value["source_record"], source)
        self.assertEqual(value["management_snapshot"], expected["management_snapshot"])
        self.assertEqual((self.state / "management.json").read_bytes(), state_before)
        self.assertEqual(events.read_bytes(), events_before)

    def transition(self, before, active):
        action = "cell-activate" if active else "cell-deactivate"
        value = self.okay(action)
        previous, snapshot = before["management_snapshot"], value["management_snapshot"]
        self.assertEqual(value["state"], "RUNNING")
        self.assertEqual(value["source_record"], before["source_record"])
        self.assertTrue(value["source_record"]["model_ready"])
        self.assertEqual(snapshot["authority_instance"], previous["authority_instance"])
        self.assertEqual(snapshot["parent"]["active"], active)
        self.assertEqual(snapshot["parent"]["generation"], previous["parent"]["generation"] + 1)
        self.assertEqual(snapshot["canonical"]["generation"], previous["canonical"]["generation"] + 1)
        self.assertEqual(snapshot["binding"], previous["binding"])
        self.assertFalse(snapshot["source_trusted"])
        self.assertIsNone(snapshot["discovered_source"])
        self.assertFalse(snapshot["binding_confirmed"])
        self.assertFalse(snapshot["binding_current"])
        self.assertEqual(snapshot["bound_nodes"], 0)
        repeated = self.okay(action)
        self.assertEqual(repeated["source_record"], value["source_record"])
        self.assertEqual(repeated["management_snapshot"], snapshot)
        self.assert_cell_read_only(value)
        return value

    def rejected_request(self, reason, source):
        actual_requests = self.request_log.read_bytes()
        value = self.command("ask", prompt="This request must not reach the fixture backend.")
        self.assertEqual(value["outcome"], "ERROR", value)
        self.assertEqual(value["error"], reason)
        self.assertEqual(value["management_outcome"], "rejected")
        self.assertIsNone(value["inference_receipt"])
        self.assertIsNone(value["resource_result"])
        self.assertEqual(value["source_record"], source)
        self.assertEqual(self.request_log.read_bytes(), actual_requests)
        return value

    def stale_resources(self):
        for action in ("resources-status", "resources-sample"):
            value = self.command(action)
            self.assertEqual(value["outcome"], "ERROR", value)
            self.assertEqual(value["error"], "resource-relation-stale")
            self.assertFalse(value["resource_result"]["relation_current"])
            self.assertIsNone(value["resource_result"]["observation"])

    def reconcile_resources(self, source):
        discovered = self.okay("room-discover")
        self.assertEqual(discovered["management_snapshot"]["discovered_source"], source)
        rebound = self.okay("room-reconcile")
        self.assertTrue(rebound["management_snapshot"]["binding_current"])
        self.assertEqual(rebound["management_snapshot"]["binding"]["generation"], 2)
        self.stale_resources()
        linked = self.okay("resources-link", backend_dir=self.output)
        self.assertEqual(linked["resource_result"]["relation"]["relation_generation"], 2)
        self.assertEqual(linked["source_record"], source)
        return self.measured_request("After explicit Cell reconciliation and resource relink.")

    def stop_and_verify(self, expected_runs):
        self.assertEqual(self.okay("stop")["state"], "STOPPED")
        verified = verify_agent_runs(self.state, require_live=False)
        self.assertEqual(verified["outcome"], "PASS", verified)
        self.assertEqual(len(verified["runs"]), expected_runs)
        self.assertEqual(verify_agent_runs(self.state, require_live=True)["outcome"], "FAIL")

    def test_live_cell_flip_keeps_main_alive_and_requires_explicit_reconciliation(self):
        with self.backend():
            before = self.initial_binding()
            self.assert_cell_read_only(before)
            repeated = self.okay("cell-activate")
            self.assertEqual(repeated["management_snapshot"], before["management_snapshot"])
            with ProcessReader(before["source_record"]["process_id"]) as main:
                identity = main.identity
                inactive = self.transition(before, False)
                self.assertEqual(main.sample()["process_start_ticks"], identity["process_start_ticks"])
                rejected = self.rejected_request("orphan", inactive["source_record"])
                self.stale_resources()
                active = self.transition(rejected, True)
                self.assertEqual(main.sample()["process_id"], identity["process_id"])
                self.rejected_request("stale", active["source_record"])
                self.reconcile_resources(active["source_record"])
                self.assertEqual(main.identity, identity)
                self.stop_and_verify(1)
                with self.assertRaises(ResourceError):
                    main.sample()

    def test_inactive_cell_survives_main_restart_until_latest_source_is_reconciled(self):
        with self.backend():
            before = self.initial_binding()
            inactive = self.transition(before, False)
            old_source = inactive["source_record"]
            self.rejected_request("orphan", old_source)
            with ProcessReader(old_source["process_id"]) as old_main:
                stopped = self.okay("stop")
                self.assertEqual(stopped["state"], "STOPPED")
                with self.assertRaises(ResourceError):
                    old_main.sample()
            resumed = self.okay("start", fixture_backend=True)
            source, snapshot = resumed["source_record"], resumed["management_snapshot"]
            self.assertEqual(source["source_id"], old_source["source_id"])
            self.assertNotEqual(source["source_instance"], old_source["source_instance"])
            self.assertEqual(source["service_start_generation"], old_source["service_start_generation"] + 1)
            self.assertEqual(source["source_generation"], 1)
            self.assertEqual(source["completed_requests"], 1)
            self.assertTrue(source["model_ready"])
            self.assertEqual(snapshot["parent"], inactive["management_snapshot"]["parent"])
            self.assertEqual(snapshot["canonical"], inactive["management_snapshot"]["canonical"])
            self.assertEqual(snapshot["authority_instance"], inactive["management_snapshot"]["authority_instance"])
            self.assertFalse(snapshot["binding_current"])
            self.assert_cell_read_only(resumed)
            self.rejected_request("orphan", source)
            self.stale_resources()
            active = self.transition(resumed, True)
            self.rejected_request("stale", source)
            result = self.reconcile_resources(source)
            self.assertEqual(result["management_snapshot"]["parent"]["generation"], 3)
            self.assertIn(old_source["source_instance"], result["management_snapshot"]["retired_instances"])
            self.assertEqual(active["source_record"], source)
            self.stop_and_verify(2)


if __name__ == "__main__":
    unittest.main()
