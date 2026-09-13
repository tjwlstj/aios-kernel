"""Actual Linux Cell lifecycle; model answers remain explicit fixture evidence."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import sys
import tempfile
import time
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted" / "linux"))
sys.path.insert(0, str(ROOT / "tools" / "hosted"))
from aios_agent import client
from aios_backend import client as backend_client
from resource_output_contract import validate_resource_result
from aios_resources.proc import ProcessReader, ResourceError
from verify_agent import verify_agent_runs


FIXTURE_BACKEND = '''import http.server,json,sys,time
from pathlib import Path
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        raw=b'{"status":"ok"}'
        self.send_response(200)
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_POST(self):
        request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        with Path(__file__).with_suffix('.requests').open('ab') as log:
            log.write(b'request\\n')
        deadline=time.process_time()+0.04
        while time.process_time()<deadline:
            pass
        value={'content':'Explicit Cell/resource fixture completion.','tokens_predicted':4,'model':'fixture-cell-main'}
        if request.get('n_predict')==192:
            value.update(prompt=request['prompt'],truncated=False)
        raw=json.dumps(value).encode()
        self.send_response(200)
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def log_message(self,*args):
        pass
http.server.HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()
'''


@unittest.skipUnless(platform.system() == "Linux" and hasattr(os, "pidfd_open")
                     and hasattr(socket, "SO_PEERCRED") and Path("/proc/self/stat").is_file(),
                     "Linux process identity and authenticated resource IPC required")
class CellRuntimeTests(unittest.TestCase):
    @contextmanager
    def backend(self):
        # Task admission authenticates a managed backend generation. The model
        # and HTTP replies remain explicit fixtures, never actual AI evidence.
        with tempfile.TemporaryDirectory(prefix="aios-cell-") as temporary:
            base = Path(temporary)
            self.state, self.output = base / "main", base / "backend"
            children_before = {owner: set(owner._CHILDREN) for owner in (client, backend_client)}
            script = base / "fixture_backend.py"
            self.request_log = script.with_suffix(".requests")
            self.request_log.write_bytes(b"")
            script.write_text(FIXTURE_BACKEND, encoding="utf-8")
            model = base / "fixture.model"
            model.write_bytes(b"explicit Cell/resource lifecycle fixture model")
            with socket.socket() as reserve:
                reserve.bind(("127.0.0.1", 0))
                port = reserve.getsockname()[1]
            self.backend_config = {"schema_version": 1, "endpoint": f"http://127.0.0.1:{port}",
                "model_id": "fixture-cell-main", "model_path": str(model),
                "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
                "backend_path": str(script), "backend_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
                "provenance_sha256": "c" * 64}
            self.config = base / "config.json"
            self.config.write_text(json.dumps(self.backend_config), encoding="utf-8")

            def reap(owner):
                for instance, owned in list(owner._CHILDREN.items()):
                    if instance not in children_before[owner]:
                        owner._finish_failed_start(owned)
                        owner._CHILDREN.pop(instance, None)

            try:
                started = backend_client.control(self.output, "start", self.config, fixture_backend=True)
                self.assertEqual((started["outcome"], started["state"]), ("OK", "RUNNING"), started)
                self.assertEqual(started["capture_kind"], "fixture")
                yield
            finally:
                # Even a failed assertion with an active Task only reaps this
                # fixture's owned MAIN/worker before its owned backend.
                try:
                    client.control(self.state, "stop")
                finally:
                    try:
                        reap(client)
                    finally:
                        try:
                            backend_client.control(self.output, "stop", fixture_backend=True)
                        finally:
                            reap(backend_client)

    def command(self, action, **kwargs):
        if action in {"start", "restart"}:
            kwargs.setdefault("backend_dir", self.output)
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

    def admit(self, prompt):
        request_id = str(uuid.uuid4())
        value = self.command("ask-start", prompt=prompt, request_id=request_id)
        self.assertEqual(value["schema_version"], 6)
        self.assertEqual(value["request_id"], request_id)
        for name in ("inference_receipt", "resource_result", "space_context", "management_outcome"):
            self.assertIsNone(value[name])
        if value["outcome"] == "OK":
            self.assertEqual(value["task"]["phase"], "ACCEPTED")
            self.assertEqual(value["task"]["request_id"], request_id)
            self.assertEqual(value["task"]["user_prompt"], prompt)
        else:
            self.assertIsNone(value["task"])
        return value

    def finish(self, admitted):
        self.assertEqual(admitted["outcome"], "OK", admitted)
        request_id = admitted["request_id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            value = self.okay("task-result", request_id=request_id)
            self.assertEqual(value["request_id"], request_id)
            self.assertIsNone(value["inference_receipt"])
            self.assertIsNone(value["resource_result"])
            if value["task"]["phase"] == "FINISHED":
                self.assertEqual(value["task"]["model_outcome"], "ANSWERED", value)
                return value
            time.sleep(0.03)
        self.fail("fixture Task did not reach FINISHED within 15 seconds")

    def request_evidence(self, result):
        # The public Task response stays intact. Receipt/resource results are
        # read from their UUID files and joined to the producer's final event.
        task = result["task"]
        self.assertEqual(task["phase"], "FINISHED")
        request_id = task["request_id"]
        self.assertEqual(result["request_id"], request_id)
        self.assertIsNone(result["inference_receipt"])
        self.assertIsNone(result["resource_result"])
        run = self.state / "runs" / task["source_before"]["source_instance"]
        receipt_name = "requests/" + request_id + ".json"
        receipt = json.loads((run / receipt_name).read_bytes())
        links = {"source_before", "source_after", "authority_instance", "binding_generation"}
        self.assertEqual({key: value for key, value in receipt.items() if key not in links}, task["inference_receipt"])
        self.assertEqual(receipt["source_before"], task["source_before"])
        self.assertEqual(receipt["authority_instance"], task["management_before"]["authority_instance"])
        self.assertEqual(receipt["binding_generation"], task["management_before"]["binding"]["generation"])
        events = [json.loads(line) for line in (run / "events.jsonl").read_bytes().splitlines()]
        finals = [row for row in events if row["event"] == "REQUEST_RESULT" and row["receipt_file"] == receipt_name]
        self.assertEqual(len(finals), 1)
        final = finals[0]
        self.assertEqual(final["source_record"], receipt["source_after"])
        self.assertEqual(result["source_record"], receipt["source_after"])
        self.assertEqual((final["outcome"], final["error"]), (receipt["outcome"], receipt["error"]))
        self.assertEqual(final["resource_file"], "resources/" + request_id + ".json")
        resources = json.loads((run / final["resource_file"]).read_bytes())
        validate_resource_result(resources, source=final["source_record"], snapshot=final["management_snapshot"],
                                 receipt=receipt, config=self.backend_config, require_live=False)
        self.assertEqual(resources["action"], "request")
        return receipt, resources

    def measured_request(self, prompt):
        sampled = self.okay("resources-sample")
        self.assertEqual(sampled["resource_result"]["observation"]["kind"], "sample")
        value = self.finish(self.admit(prompt))
        receipt, resources = self.request_evidence(value)
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
        value = self.admit("This request must not reach the fixture backend.")
        self.assertEqual(value["outcome"], "ERROR", value)
        self.assertEqual(value["error"], reason)
        self.assertIsNone(value["management_outcome"])
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
