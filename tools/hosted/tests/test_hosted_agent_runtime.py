"""MAIN real Linux processes with explicitly fixture-labelled model responses."""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import platform
import select
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted" / "linux"))
from aios_agent import client, daemon, inference, protocol, space
from aios_backend import client as backend_client
from aios_management.binding import Authority


FIXTURE_BACKEND = '''import http.server,json,sys
from pathlib import Path
mode=Path(__file__).with_suffix('.mode')
requests=Path(__file__).with_suffix('.requests')
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        raw=b'{"status":"ok"}'
        self.send_response(200)
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_POST(self):
        request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        with requests.open('a',encoding='utf-8') as output:
            output.write(json.dumps(request)+'\\n')
        failed=mode.exists() and mode.read_text(encoding='utf-8')=='fail'
        value={'content':'' if failed else 'Fixture model response.',
               'tokens_predicted':min(request['n_predict'],4),'model':'fixture-main',
               'truncated':False,'prompt':request['prompt']}
        raw=json.dumps(value).encode()
        self.send_response(200)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def log_message(self,*args):
        pass
http.server.HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()
'''


class AgentPureTests(unittest.TestCase):
    def test_exhausted_start_history_never_creates_another_launch(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            before = protocol.reply("status", "STOPPED")
            with patch.object(client, "_status", return_value=before), \
                 patch.object(client, "_busy", return_value=False), \
                 patch.object(client, "registry_at", return_value={"service_start_generation": daemon.MAX_STARTS}), \
                 patch.object(client.subprocess, "Popen") as launch:
                result = client._start(directory, None, False)
            self.assertEqual(result["error"], "generation-exhausted")
            self.assertEqual(result["outcome"], "ERROR")
            launch.assert_not_called()
            self.assertEqual(list(directory.iterdir()), [])

    def test_source_provenance_is_explicit_and_complete(self):
        values = daemon.source_hashes()
        self.assertEqual(set(values), set(daemon.SOURCE_FILES))
        self.assertEqual(len(values), 28)
        self.assertTrue({'aios_agent/async_inference.py', 'aios_agent/request_state.py',
                         'aios_agent/request_runtime.py'} <= values.keys())
        self.assertTrue(all(inference.hash_text(value) for value in values.values()))

    def test_managed_fixture_program_compiles_without_starting_a_server(self):
        compile(FIXTURE_BACKEND, '<managed MAIN fixture>', 'exec')

    def test_unsupported_platform_never_creates_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "absent"
            with patch.object(client, "supported", return_value=False):
                value = client.control(state, "start")
            self.assertEqual(value["state"], "UNSUPPORTED")
            self.assertEqual(value["capture_kind"], "unsupported")
            self.assertIsNone(value["source_record"])
            self.assertFalse(state.exists())

    def test_fixture_reply_is_explicit_and_unknown_labels_rejected(self):
        value = protocol.reply("status", "RUNNING", capture_kind="fixture")
        self.assertEqual(protocol.validate_reply(value, "status")["capture_kind"], "fixture")
        value["capture_kind"] = "production"
        with self.assertRaises(ValueError):
            protocol.validate_reply(value, "status")

    def test_worker_rejects_unbounded_or_boolean_token_limit_before_network(self):
        with patch.object(inference.http.client, "HTTPConnection") as connection:
            for value in (True, 0, 193, "192"):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "request-token-limit"):
                    inference.worker({}, inference.encoded({"n_predict": value}))
            connection.assert_not_called()

    def test_space_success_requires_running_source_and_management_originals(self):
        for state, source, management in (("STOPPED", {}, {}), ("RUNNING", None, {}), ("RUNNING", {}, None)):
            value = protocol.reply("space", state, source=source, management=management, space_context={})
            with self.subTest(state=state, source=source, management=management), self.assertRaisesRegex(ValueError, "protocol-error"):
                protocol.validate_reply(value, "space")


@unittest.skipUnless(platform.system() == "Linux" and hasattr(socket, "SO_PEERCRED") and hasattr(os, "pidfd_open"),
                     "Linux private IPC and pidfd required")
class AgentProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aios-main-")
        self.base = Path(self.temporary.name)
        self.state = self.base / "state"
        self.backend = self.base / "backend"
        self.children_before = {owner: set(owner._CHILDREN) for owner in (client, backend_client)}
        self.addCleanup(self.cleanup_owned)
        model, backend = self.base / "fixture.model", self.base / "fixture.py"
        model.write_bytes(b"explicit fixture model bytes")
        backend.write_text(FIXTURE_BACKEND, encoding="utf-8")
        self.mode_file = backend.with_suffix(".mode")
        self.request_log = backend.with_suffix(".requests")
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        self.config = {"schema_version": 1, "endpoint": f"http://127.0.0.1:{port}",
            "model_id": "fixture-main", "model_path": str(model), "model_sha256": inference.file_hash(model),
            "backend_path": str(backend), "backend_sha256": inference.file_hash(backend), "provenance_sha256": "a" * 64}
        self.config_path = self.base / "config.json"
        self.config_path.write_bytes(inference.encoded(self.config))
        value = backend_client.control(self.backend, "start", self.config_path, fixture_backend=True)
        self.assertEqual((value["outcome"], value["state"]), ("OK", "RUNNING"), value)
        self.assertEqual(value["capture_kind"], "fixture")

    def cleanup_owned(self):
        def reap(owner):
            for instance, child in list(owner._CHILDREN.items()):
                if instance not in self.children_before[owner]:
                    owner._finish_failed_start(child)
                    owner._CHILDREN.pop(instance, None)
        try:
            client.control(self.state, "stop")
        finally:
            try:
                # A failed assertion may leave a Task active. End only this
                # test's owned MAIN session and worker before its backend.
                reap(client)
            finally:
                try:
                    backend_client.control(self.backend, "stop", fixture_backend=True)
                finally:
                    try:
                        reap(backend_client)
                    finally:
                        self.temporary.cleanup()

    def requests(self):
        return ([json.loads(line) for line in self.request_log.read_bytes().splitlines()]
                if self.request_log.exists() else [])

    def set_failure(self, failed):
        self.mode_file.write_text("fail" if failed else "answer", encoding="utf-8")

    def start(self):
        value = client.control(self.state, "start", self.config_path, fixture_backend=True,
                               backend_dir=self.backend)
        self.assertEqual((value["outcome"], value["state"]), ("OK", "RUNNING"), value)
        self.assertEqual(value["capture_kind"], "fixture")
        return value

    def admit(self, prompt):
        request_id = str(uuid.uuid4())
        value = client.control(self.state, "ask-start", prompt=prompt, request_id=request_id)
        self.assertEqual(value["schema_version"], 6)
        self.assertEqual(value["request_id"], request_id)
        self.assertIsNone(value["inference_receipt"])
        if value["outcome"] == "OK":
            self.assertEqual(value["task"]["phase"], "ACCEPTED")
            self.assertEqual(value["task"]["request_id"], request_id)
            self.assertEqual(value["task"]["user_prompt"], prompt)
        else:
            self.assertIsNone(value["task"])
        return value

    def finish(self, admitted, *, model_outcome="ANSWERED"):
        self.assertEqual(admitted["outcome"], "OK", admitted)
        request_id = admitted["request_id"]
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            value = client.control(self.state, "task-result", request_id=request_id)
            self.assertEqual(value["outcome"], "OK", value)
            self.assertEqual(value["request_id"], request_id)
            self.assertIsNone(value["inference_receipt"])
            if value["task"]["phase"] == "FINISHED":
                self.assertEqual(value["task"]["model_outcome"], model_outcome, value)
                return value
            time.sleep(0.03)
        self.fail("fixture Task did not reach FINISHED within 15 seconds")

    def bind(self):
        self.assertEqual(client.control(self.state, "room-discover")["outcome"], "OK")
        value = client.control(self.state, "room-bind")
        self.assertEqual(value["outcome"], "OK", value)
        self.assertTrue(value["management_snapshot"]["binding_current"])
        return value

    def test_real_process_warmup_explicit_binding_requests_and_clean_stop(self):
        started = self.start()
        source = started["source_record"]
        self.assertEqual(source["completed_requests"], 1)
        self.assertTrue(source["model_ready"])
        self.assertEqual(started["management_snapshot"]["state"], "UNBOUND")
        legacy = client.control(self.state, "ask", prompt="What is AIOS?")
        self.assertEqual((legacy["outcome"], legacy["error"]), ("ERROR", "request-task-required"), legacy)
        self.assertIsNone(legacy["inference_receipt"])
        self.assertIsNone(legacy["task"])
        rejected = self.admit("What is AIOS?")
        self.assertEqual((rejected["outcome"], rejected["error"]), ("ERROR", "not-discovered"), rejected)
        self.assertIsNone(rejected["inference_receipt"])
        self.assertEqual(len(self.requests()), 1)
        self.bind()
        answer = self.finish(self.admit("What is AIOS?"))
        self.assertEqual(answer["source_record"]["completed_requests"], 2)
        self.assertEqual(answer["source_record"]["source_generation"], 1)
        self.assertEqual(answer["management_snapshot"]["current_source"], answer["source_record"])
        receipt = answer["task"]["inference_receipt"]
        run = self.state / "runs" / source["source_instance"]
        saved = json.loads((run / "requests" / (answer["request_id"] + ".json")).read_bytes())
        self.assertEqual(saved["source_after"], answer["source_record"])
        self.assertEqual(saved["binding_generation"], 1)
        self.assertEqual({key: saved[key] for key in receipt}, receipt)
        self.assertEqual(receipt["schema_version"], 3)
        self.assertEqual(receipt["user_prompt"], "What is AIOS?")
        self.assertIsNotNone(receipt["space_context"])
        self.assertIsNotNone(receipt["backend_execution"])
        self.assertEqual(answer["task"]["worker_exit_code"], 0)
        stopped = client.control(self.state, "stop")
        self.assertEqual((stopped["outcome"], stopped["state"]), ("OK", "STOPPED"), stopped)
        self.assertEqual(stopped["source_record"]["source_generation"], 2)
        self.assertEqual(stopped["source_record"]["lifecycle_state"], "exited")
        self.assertFalse(stopped["source_record"]["model_ready"])
        self.assertFalse(stopped["management_snapshot"]["binding_current"])
        run = self.state / "runs" / source["source_instance"]
        result = json.loads((run / "result.json").read_bytes())
        self.assertEqual(result["capture_kind"], "fixture")
        self.assertEqual(result["exit_code"], 0)
        for name, digest in result["files"].items():
            self.assertEqual(hashlib.sha256((run / name).read_bytes()).hexdigest(), digest)
        sys.path.insert(0, str(ROOT / "tools" / "hosted"))
        from verify_agent import verify_run
        verdict = verify_run(run, require_live=False)
        self.assertEqual(verdict["outcome"], "PASS", verdict)
        self.assertEqual(verify_run(run, require_live=True)["outcome"], "FAIL")
        self.assertEqual(self.state.stat().st_mode & 0o7777, 0o700)

    def test_space_refresh_and_cached_context_reach_the_same_bound_model(self):
        started = self.start()
        observed = client.control(self.state, "space")
        self.assertEqual(observed["outcome"], "OK", observed)
        context = observed["space_context"]
        self.assertFalse(context["management"]["binding_current"])
        source = started["source_record"]
        raw = context["observation"]["raw"]
        self.assertEqual(context["observation"]["process_id"], source["process_id"])
        self.assertEqual(raw["working_directory"], os.readlink(f"/proc/{source['process_id']}/cwd"))
        self.assertEqual(raw["logical_cpu_count"], os.cpu_count())
        self.assertEqual(len(self.requests()), 1)  # Space is observation, with no model request.
        discovered = client.control(self.state, "room-discover")
        self.assertEqual(discovered["outcome"], "OK", discovered)
        refreshed_discovery = client.control(self.state, "space")
        self.assertEqual(refreshed_discovery["management_snapshot"], discovered["management_snapshot"])
        context = refreshed_discovery["space_context"]
        bound = client.control(self.state, "room-bind")
        self.assertEqual(bound["outcome"], "OK", bound)
        answer = self.finish(self.admit("What can you observe?"))
        receipt = answer["task"]["inference_receipt"]
        consumed = receipt["space_context"]
        self.assertEqual(consumed["observation"], context["observation"])
        self.assertTrue(consumed["management"]["binding_current"])
        self.assertEqual(consumed["consumer"]["source_instance"], source["source_instance"])
        body = self.requests()[-1]
        self.assertEqual(body, json.loads(receipt["request_body"]))
        self.assertEqual(body["n_predict"], 192)
        envelope = json.loads(body["prompt"].split("<|im_start|>user\n", 1)[1].split(" /no_think", 1)[0])
        self.assertEqual(envelope["question"], "What can you observe?")
        self.assertEqual(envelope["space_data"], space.context_for_model(consumed))
        self.assertEqual(envelope["space_data"]["facts"]["network"], {"status": "UNKNOWN", "value": None})
        refreshed = client.control(self.state, "space")
        self.assertEqual(refreshed["outcome"], "OK", refreshed)
        self.assertGreater(refreshed["space_context"]["observation"]["observed_monotonic_ns"],
                           context["observation"]["observed_monotonic_ns"])
        self.assertEqual(len(self.requests()), 2)
        before = self.requests()
        run = self.state / "runs" / source["source_instance"]
        events_before = (run / "events.jsonl").read_bytes()
        rejected = self.admit("한" * 1300)
        self.assertEqual(rejected["error"], "space-budget", rejected)
        self.assertIsNone(rejected["inference_receipt"])
        self.assertEqual(self.requests(), before)
        self.assertEqual((run / "events.jsonl").read_bytes(), events_before)
        self.assertFalse((run / "tasks" / rejected["request_id"]).exists())
        self.assertEqual(rejected["source_record"], answer["source_record"])
        self.assertTrue(client.control(self.state, "status")["source_record"]["model_ready"])

        stopped = client.control(self.state, "stop")
        self.assertEqual(stopped["outcome"], "OK", stopped)
        run = self.state / "runs" / source["source_instance"]
        events = [json.loads(row) for row in (run / "events.jsonl").read_text().splitlines()]
        # A pre-admission refusal is the authenticated IPC error above; it is
        # not a completed Task or a historical synchronous COMMAND ask event.
        self.assertEqual(sum(row["event"] == "REQUEST_RESULT" for row in events), 1)
        self.assertFalse(any(row["action"] == "ask" for row in events))
        sys.path.insert(0, str(ROOT / "tools" / "hosted"))
        from verify_agent import verify_run
        verdict = verify_run(run, require_live=False)
        self.assertEqual(verdict["outcome"], "PASS", verdict)

    def test_rejected_initial_context_does_not_publish_an_unrecorded_sample(self):
        started = self.start()
        self.bind()
        run = self.state / "runs" / started["source_record"]["source_instance"]
        events_before = (run / "events.jsonl").read_bytes()
        rejected = self.admit("한" * 1300)
        self.assertEqual(rejected["error"], "space-budget", rejected)
        self.assertEqual(len(self.requests()), 1)
        self.assertEqual((run / "events.jsonl").read_bytes(), events_before)
        self.assertFalse((run / "tasks" / rejected["request_id"]).exists())
        self.assertEqual(client.control(self.state, "room-discover")["outcome"], "OK")
        self.finish(self.admit("What can you observe?"))
        self.assertEqual(client.control(self.state, "stop")["outcome"], "OK")
        run = self.state / "runs" / started["source_record"]["source_instance"]
        sys.path.insert(0, str(ROOT / "tools" / "hosted"))
        from verify_agent import verify_run
        verdict = verify_run(run, require_live=False)
        self.assertEqual(verdict["outcome"], "PASS", verdict)

    def test_restart_keeps_authority_but_requires_discovery_and_reconcile(self):
        first = self.start()
        self.bind()
        restarted = client.control(self.state, "restart", fixture_backend=True, backend_dir=self.backend)
        self.assertEqual(restarted["outcome"], "OK", restarted)
        self.assertEqual(restarted["source_record"]["source_id"], first["source_record"]["source_id"])
        self.assertNotEqual(restarted["source_record"]["source_instance"], first["source_record"]["source_instance"])
        self.assertEqual(restarted["source_record"]["service_start_generation"], 2)
        self.assertEqual(restarted["source_record"]["source_generation"], 1)
        self.assertEqual(restarted["management_snapshot"]["authority_instance"], first["management_snapshot"]["authority_instance"])
        self.assertEqual(client.control(self.state, "room-status")["error"], "stale")
        rejected = self.admit("Old binding?")
        self.assertEqual((rejected["outcome"], rejected["error"]), ("ERROR", "stale"), rejected)
        self.assertEqual(client.control(self.state, "room-reconcile")["outcome"], "ERROR")
        self.assertEqual(client.control(self.state, "room-discover")["outcome"], "OK")
        value = client.control(self.state, "room-reconcile")
        self.assertEqual(value["outcome"], "OK", value)
        self.assertEqual(value["management_snapshot"]["binding"]["generation"], 2)
        self.finish(self.admit("New binding?"))

    def test_abrupt_death_never_recovers_live_binding_from_cache(self):
        started = self.start()
        self.bind()
        child = client._CHILDREN.pop(started["source_record"]["source_instance"])
        child.kill()
        child.wait(timeout=5)
        value = client.control(self.state, "status")
        self.assertEqual((value["state"], value["error"]), ("STALE", "process-not-running"), value)
        self.assertFalse(value["management_snapshot"]["binding_current"])
        rejected = self.admit("Must not infer.")
        # Task IPC cannot authenticate the dead socket; the status call above
        # independently reports process-not-running and invalidates the cache.
        self.assertEqual((rejected["outcome"], rejected["error"]), ("ERROR", "state-io"), rejected)
        restarted = client.control(self.state, "restart", fixture_backend=True, backend_dir=self.backend)
        self.assertEqual(restarted["outcome"], "OK", restarted)
        self.assertEqual(restarted["source_record"]["service_start_generation"], 2)
        self.assertFalse(restarted["management_snapshot"]["binding_current"])

    def test_model_hash_mismatch_never_calls_backend_or_claims_ready(self):
        self.config["model_sha256"] = "b" * 64
        self.config_path.write_bytes(inference.encoded(self.config))
        value = client.control(self.state, "start", self.config_path, fixture_backend=True, backend_dir=self.backend)
        self.assertEqual(value["outcome"], "ERROR")
        self.assertEqual(value["error"], "model-hash-mismatch", value)
        self.assertFalse(value["source_record"]["model_ready"])
        self.assertEqual(value["source_record"]["completed_requests"], 0)
        self.assertEqual(len(self.requests()), 0)

    def test_failed_completion_invalidates_binding_and_requires_restart(self):
        self.start()
        self.bind()
        self.set_failure(True)
        value = self.finish(self.admit("Fail this request."), model_outcome="UNKNOWN")
        receipt = value["task"]["inference_receipt"]
        self.assertEqual((receipt["outcome"], receipt["error"]), ("ERROR", "backend-failed"), receipt)
        self.assertFalse(value["source_record"]["model_ready"])
        self.assertEqual(value["source_record"]["source_generation"], 2)
        self.assertEqual(value["source_record"]["completed_requests"], 1)
        self.assertFalse(value["management_snapshot"]["binding_current"])
        calls = len(self.requests())
        rejected = self.admit("No second inference.")
        self.assertEqual((rejected["outcome"], rejected["error"]), ("ERROR", "model-not-ready"), rejected)
        self.assertEqual(len(self.requests()), calls)
        self.set_failure(False)

    def test_empty_warmup_fails_readiness_and_retains_failed_receipt(self):
        self.set_failure(True)
        value = client.control(self.state, "start", self.config_path, fixture_backend=True, backend_dir=self.backend)
        self.assertEqual((value["state"], value["error"]), ("FAILED", "backend-failed"), value)
        self.assertFalse(value["source_record"]["model_ready"])
        self.assertEqual(value["source_record"]["completed_requests"], 0)
        self.assertFalse(value["management_snapshot"]["binding_current"])
        run = self.state / "runs" / value["source_record"]["source_instance"]
        warmup = json.loads((run / "warmup.json").read_bytes())
        self.assertEqual(warmup["outcome"], "ERROR")
        self.assertIsNone(warmup["response_sha256"])
        self.assertEqual(client.control(self.state, "stop")["error"], "backend-failed")

    def test_stale_ipc_instance_is_rejected_without_mutation(self):
        started = self.start()
        before = started["source_record"]
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(self.state / "agent.sock"))
            protocol.send(connection, {"schema_version": 6, "action": "stop",
                                       "source_instance": "00000000-0000-0000-0000-000000000001", "prompt": None,
                                       'backend_dir': None, 'request_id': None})
            rejected = protocol.receive(connection)
        self.assertEqual(rejected["error"], "stale-instance")
        after = client.control(self.state, "status")
        self.assertEqual(after["state"], "RUNNING")
        self.assertEqual(after["source_record"], before)

    def test_concurrent_start_has_one_owner(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            values = list(executor.map(lambda _: client.control(self.state, "start", self.config_path,
                fixture_backend=True, backend_dir=self.backend), range(2)))
        self.assertEqual(sum(value["outcome"] == "OK" for value in values), 1, values)
        value = client.control(self.state, "status")
        self.assertEqual(value["source_record"]["service_start_generation"], 1)
        self.assertEqual(len(self.requests()), 1)

    def test_private_state_symlink_and_corrupt_authority_rejected(self):
        self.state.symlink_to(self.base, target_is_directory=True)
        self.assertEqual(client.control(self.state, "start", self.config_path)["outcome"], "ERROR")
        self.state.unlink()
        self.start()
        self.bind()
        self.assertEqual(client.control(self.state, "stop")["outcome"], "OK")
        (self.state / "management.json").write_text('{"schema_version":1,"schema_version":1}')
        value = client.control(self.state, "restart", fixture_backend=True, backend_dir=self.backend)
        self.assertEqual(value["outcome"], "ERROR", value)

    def test_event_limit_preserves_stop_and_status_without_unbounded_history(self):
        started = self.start()
        # STARTING/RUNNING use two events; reserve three more for backend
        # invalidation and the two stop records, even if invalidation is unused.
        for _ in range(daemon.MAX_EVENTS - 5):
            self.assertEqual(client.control(self.state, "room-discover")["outcome"], "OK")
        self.assertEqual(client.control(self.state, "room-discover")["error"], "request-limit")
        self.assertEqual(client.control(self.state, "status")["state"], "RUNNING")
        self.assertEqual(client.control(self.state, "stop")["outcome"], "OK")
        run = self.state / "runs" / started["source_record"]["source_instance"]
        events = [json.loads(row) for row in (run / "events.jsonl").read_bytes().splitlines()]
        self.assertEqual(len(events), daemon.MAX_EVENTS - 1)
        self.assertEqual([row["event"] for row in events[-2:]], ["STOPPING", "STOPPED"])

    def test_persistence_failure_ends_process_instead_of_serving_uncertain_binding(self):
        started = self.start()
        self.assertEqual(client.control(self.state, "room-discover")["outcome"], "OK")
        instance = started["source_record"]["source_instance"]
        run = self.state / "runs" / instance
        # The writer must reject a changed private file contract before replace.
        # This works for both ordinary users and root without relying on EACCES.
        os.chmod(run / "management.json", 0o400)
        answer = client.control(self.state, "room-bind")
        self.assertEqual(answer["outcome"], "ERROR", answer)
        child = client._CHILDREN.pop(instance)
        self.assertEqual(child.wait(timeout=5), 1)
        result = json.loads((run / "result.json").read_bytes())
        self.assertEqual((result["state"], result["exit_code"], result["error"]), ("FAILED", 1, "state-io"))
        self.assertFalse(result["management_snapshot"]["binding_current"])
        before = self.requests()
        rejected = self.admit("Must not continue.")
        self.assertEqual((rejected["outcome"], rejected["error"]), ("ERROR", "state-io"), rejected)
        self.assertEqual(self.requests(), before)

    def test_failed_start_cleanup_reaps_owned_worker_but_not_separate_backend(self):
        marker = self.base / "worker.pid"
        script = ("import pathlib,signal,subprocess,sys,time\n"
                  "worker=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n"
                  "def stop(signum, frame):\n"
                  "    worker.wait(timeout=3)\n"
                  "    raise SystemExit(0)\n"
                  "signal.signal(signal.SIGTERM,stop)\n"
                  "pathlib.Path(sys.argv[1]).write_text(str(worker.pid))\n"
                  "time.sleep(60)\n")
        child = subprocess.Popen([sys.executable, "-c", script, str(marker)], start_new_session=True,
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        separate = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        handle = None
        try:
            deadline = time.monotonic() + 10
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(marker.exists())
            handle = os.pidfd_open(int(marker.read_text()))
            client._finish_failed_start(child)
            self.assertIsNotNone(child.poll())
            self.assertTrue(select.select([handle], [], [], 3)[0], "owned worker survived failed startup")
            self.assertIsNone(separate.poll(), "separately owned backend was selected for cleanup")
        finally:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=3)
            if handle is not None:
                if not select.select([handle], [], [], 0)[0]:
                    signal.pidfd_send_signal(handle, signal.SIGKILL)
                os.close(handle)
            separate.terminate()
            separate.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
