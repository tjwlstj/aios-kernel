"""MAIN real Linux processes with explicitly fixture-labelled model responses."""
from __future__ import annotations

import concurrent.futures
import hashlib
import http.server
import json
import os
import platform
import select
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted" / "linux"))
from aios_agent import client, daemon, inference, protocol
from aios_management.binding import Authority


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
        self.assertEqual(len(values), 24)
        self.assertTrue(all(inference.hash_text(value) for value in values.values()))

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


@unittest.skipUnless(platform.system() == "Linux" and hasattr(socket, "SO_PEERCRED") and hasattr(os, "pidfd_open"),
                     "Linux private IPC and pidfd required")
class AgentProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aios-main-")
        self.base = Path(self.temporary.name)
        self.state = self.base / "state"
        self.mode = {"fail": False, "calls": 0}
        mode = self.mode

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                request = json.loads(body)
                mode["calls"] += 1
                value = {"content": "" if mode["fail"] else "Fixture model response.",
                         "tokens_predicted": min(request["n_predict"], 4), "model": "fixture-main"}
                raw = json.dumps(value).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def log_message(self, *_args):
                pass

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        model, backend = self.base / "fixture.model", self.base / "fixture.backend"
        model.write_bytes(b"explicit fixture model bytes")
        backend.write_bytes(b"explicit fixture backend bytes")
        self.config = {"schema_version": 1, "endpoint": f"http://127.0.0.1:{self.server.server_port}",
            "model_id": "fixture-main", "model_path": str(model), "model_sha256": inference.file_hash(model),
            "backend_path": str(backend), "backend_sha256": inference.file_hash(backend), "provenance_sha256": "a" * 64}
        self.config_path = self.base / "config.json"
        self.config_path.write_bytes(inference.encoded(self.config))

    def tearDown(self):
        try:
            current = client.control(self.state, "status")
            if current["state"] == "RUNNING":
                client.control(self.state, "stop")
            # Only test-owned Popen handles may be terminated after a failed test.
            for instance, child in list(client._CHILDREN.items()):
                if child.poll() is None:
                    child.terminate()
                    child.wait(timeout=5)
                client._CHILDREN.pop(instance, None)
        finally:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
            self.temporary.cleanup()

    def start(self):
        value = client.control(self.state, "start", self.config_path, fixture_backend=True)
        self.assertEqual((value["outcome"], value["state"]), ("OK", "RUNNING"), value)
        self.assertEqual(value["capture_kind"], "fixture")
        return value

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
        rejected = client.control(self.state, "ask", prompt="What is AIOS?")
        self.assertEqual(rejected["outcome"], "ERROR")
        self.assertIsNone(rejected["inference_receipt"])
        self.assertEqual(self.mode["calls"], 1)
        self.bind()
        answer = client.control(self.state, "ask", prompt="What is AIOS?")
        self.assertEqual(answer["outcome"], "OK", answer)
        self.assertEqual(answer["source_record"]["completed_requests"], 2)
        self.assertEqual(answer["source_record"]["source_generation"], 1)
        self.assertEqual(answer["management_snapshot"]["current_source"], answer["source_record"])
        self.assertEqual(answer["inference_receipt"]["source_after"], answer["source_record"])
        self.assertEqual(answer["inference_receipt"]["binding_generation"], 1)
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

    def test_restart_keeps_authority_but_requires_discovery_and_reconcile(self):
        first = self.start()
        self.bind()
        restarted = client.control(self.state, "restart", fixture_backend=True)
        self.assertEqual(restarted["outcome"], "OK", restarted)
        self.assertEqual(restarted["source_record"]["source_id"], first["source_record"]["source_id"])
        self.assertNotEqual(restarted["source_record"]["source_instance"], first["source_record"]["source_instance"])
        self.assertEqual(restarted["source_record"]["service_start_generation"], 2)
        self.assertEqual(restarted["source_record"]["source_generation"], 1)
        self.assertEqual(restarted["management_snapshot"]["authority_instance"], first["management_snapshot"]["authority_instance"])
        self.assertEqual(client.control(self.state, "room-status")["error"], "stale")
        self.assertEqual(client.control(self.state, "ask", prompt="Old binding?")["outcome"], "ERROR")
        self.assertEqual(client.control(self.state, "room-reconcile")["outcome"], "ERROR")
        self.assertEqual(client.control(self.state, "room-discover")["outcome"], "OK")
        value = client.control(self.state, "room-reconcile")
        self.assertEqual(value["outcome"], "OK", value)
        self.assertEqual(value["management_snapshot"]["binding"]["generation"], 2)
        self.assertEqual(client.control(self.state, "ask", prompt="New binding?")["outcome"], "OK")

    def test_abrupt_death_never_recovers_live_binding_from_cache(self):
        started = self.start()
        self.bind()
        child = client._CHILDREN.pop(started["source_record"]["source_instance"])
        child.kill()
        child.wait(timeout=5)
        value = client.control(self.state, "status")
        self.assertEqual((value["state"], value["error"]), ("STALE", "process-not-running"), value)
        self.assertFalse(value["management_snapshot"]["binding_current"])
        self.assertEqual(client.control(self.state, "ask", prompt="Must not infer.")["outcome"], "ERROR")
        restarted = client.control(self.state, "restart", fixture_backend=True)
        self.assertEqual(restarted["outcome"], "OK", restarted)
        self.assertEqual(restarted["source_record"]["service_start_generation"], 2)
        self.assertFalse(restarted["management_snapshot"]["binding_current"])

    def test_model_hash_mismatch_never_calls_backend_or_claims_ready(self):
        self.config["model_sha256"] = "b" * 64
        self.config_path.write_bytes(inference.encoded(self.config))
        value = client.control(self.state, "start", self.config_path, fixture_backend=True)
        self.assertEqual(value["outcome"], "ERROR")
        self.assertEqual(value["error"], "model-hash-mismatch", value)
        self.assertFalse(value["source_record"]["model_ready"])
        self.assertEqual(value["source_record"]["completed_requests"], 0)
        self.assertEqual(self.mode["calls"], 0)

    def test_failed_completion_invalidates_binding_and_requires_restart(self):
        self.start()
        self.bind()
        self.mode["fail"] = True
        value = client.control(self.state, "ask", prompt="Fail this request.")
        self.assertEqual(value["outcome"], "ERROR", value)
        self.assertIsNotNone(value["inference_receipt"])
        self.assertFalse(value["source_record"]["model_ready"])
        self.assertEqual(value["source_record"]["source_generation"], 2)
        self.assertEqual(value["source_record"]["completed_requests"], 1)
        self.assertFalse(value["management_snapshot"]["binding_current"])
        calls = self.mode["calls"]
        self.assertEqual(client.control(self.state, "ask", prompt="No second inference.")["outcome"], "ERROR")
        self.assertEqual(self.mode["calls"], calls)
        self.mode["fail"] = False

    def test_empty_warmup_fails_readiness_and_retains_failed_receipt(self):
        self.mode["fail"] = True
        value = client.control(self.state, "start", self.config_path, fixture_backend=True)
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
            protocol.send(connection, {"schema_version": 4, "action": "stop",
                                       "source_instance": "00000000-0000-0000-0000-000000000001", "prompt": None,
                                       'backend_dir': None})
            rejected = protocol.receive(connection)
        self.assertEqual(rejected["error"], "stale-instance")
        after = client.control(self.state, "status")
        self.assertEqual(after["state"], "RUNNING")
        self.assertEqual(after["source_record"], before)

    def test_concurrent_start_has_one_owner(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            values = list(executor.map(lambda _: client.control(self.state, "start", self.config_path, fixture_backend=True), range(2)))
        self.assertEqual(sum(value["outcome"] == "OK" for value in values), 1, values)
        value = client.control(self.state, "status")
        self.assertEqual(value["source_record"]["service_start_generation"], 1)
        self.assertEqual(self.mode["calls"], 1)

    def test_private_state_symlink_and_corrupt_authority_rejected(self):
        self.state.symlink_to(self.base, target_is_directory=True)
        self.assertEqual(client.control(self.state, "start", self.config_path)["outcome"], "ERROR")
        self.state.unlink()
        self.start()
        self.bind()
        self.assertEqual(client.control(self.state, "stop")["outcome"], "OK")
        (self.state / "management.json").write_text('{"schema_version":1,"schema_version":1}')
        value = client.control(self.state, "restart", fixture_backend=True)
        self.assertEqual(value["outcome"], "ERROR", value)

    def test_event_limit_preserves_stop_and_status_without_unbounded_history(self):
        started = self.start()
        for _ in range(daemon.MAX_EVENTS - 4):
            self.assertEqual(client.control(self.state, "room-discover")["outcome"], "OK")
        self.assertEqual(client.control(self.state, "room-discover")["error"], "request-limit")
        self.assertEqual(client.control(self.state, "status")["state"], "RUNNING")
        self.assertEqual(client.control(self.state, "stop")["outcome"], "OK")
        run = self.state / "runs" / started["source_record"]["source_instance"]
        self.assertEqual(len((run / "events.jsonl").read_bytes().splitlines()), daemon.MAX_EVENTS)

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
        before = self.mode["calls"]
        self.assertEqual(client.control(self.state, "ask", prompt="Must not continue.")["outcome"], "ERROR")
        self.assertEqual(self.mode["calls"], before)

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
