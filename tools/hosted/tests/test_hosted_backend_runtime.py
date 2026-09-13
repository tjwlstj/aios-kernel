"""Bounded backend supervisor tests; Python answers are fixture evidence only."""
from __future__ import annotations

import hashlib
import http.client
import concurrent.futures
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
from aios_backend import BackendError
from aios_backend import client, daemon, protocol
from aios_resources.backend import attest
from aios_resources.proc import ProcessReader, ResourceError


FIXTURE_SCRIPT = '''import http.server,json,os,signal,sys,time
from pathlib import Path
mode=Path(__file__).with_suffix('.mode')
if mode.exists() and mode.read_text()=='ignore-term':
    signal.signal(signal.SIGTERM,signal.SIG_IGN)
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        raw=b'{"status":"ok"}'
        self.send_response(200)
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def do_POST(self):
        request=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        deadline=time.process_time()+0.03
        while time.process_time()<deadline:
            pass
        value={'content':'Explicit backend fixture.','tokens_predicted':4,'model':'fixture-backend'}
        if request.get('n_predict')==192:
            value.update(prompt=request['prompt'],truncated=False)
        raw=json.dumps(value).encode()
        self.send_response(200)
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
    def log_message(self,*args):
        pass
server=http.server.HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler)
server.timeout=0.03
while True:
    if mode.exists():
        action=mode.read_text()
        if action=='exit':
            sys.exit(0)
        if action=='noise':
            os.write(1,b'x'*(2*1024*1024))
    server.handle_request()
'''


class BackendPureTests(unittest.TestCase):
    def test_unsupported_is_explicit_and_does_not_touch_state(self):
        with patch.object(protocol, "supported", return_value=False):
            value = client.control(Path("not-created-backend"), "start")
        self.assertEqual(value["state"], "UNSUPPORTED")
        self.assertIsNone(value["service_record"])
        self.assertEqual(value["capture_kind"], "unsupported")

    def test_public_contract_rejects_boolean_schema_and_unbounded_errors(self):
        value = protocol.reply("status", "ABSENT")
        protocol.validate_reply(value, "status")
        for changes in ({"schema_version": True}, {"extra": 1}, {"error": "x" * 65, "outcome": "ERROR"}):
            with self.assertRaises(BackendError):
                protocol.validate_reply({**value, **changes}, "status")

    def test_live_profile_rejects_arbitrary_hashes_and_fixture_is_explicit(self):
        config = {"endpoint": "http://127.0.0.1:18081", "backend_path": str(ROOT / "fixture.py"),
            "model_path": str(ROOT / "fixture.model"), "model_id": "fixture", "model_sha256": "1" * 64,
            "backend_sha256": "2" * 64}
        with self.assertRaisesRegex(BackendError, "backend-profile"):
            daemon.launch_command(config)
        self.assertEqual(daemon.launch_command(config, fixture_backend=True),
                         [sys.executable, config["backend_path"], "18081"])
        config["endpoint"] = "http://10.0.2.2:18081"
        with self.assertRaises(BackendError):
            daemon.launch_command(config, fixture_backend=True)

    def test_source_provenance_lists_complete_runtime_dependencies(self):
        hashes = daemon.source_hashes()
        self.assertEqual(len(hashes), 15)
        self.assertEqual(set(hashes), set(daemon.SOURCE_FILES))
        self.assertTrue(all(len(value) == 64 for value in hashes.values()))


@unittest.skipUnless(platform.system() == "Linux" and hasattr(os, "pidfd_open")
                     and hasattr(socket, "SO_PEERCRED") and Path("/proc/self/stat").is_file(),
                     "Linux Popen, pidfd, /proc and private Unix IPC required")
class BackendProcessTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="aios-be-")
        self.base = Path(self.temporary.name)
        self.state = self.base / "state"
        self.script = self.base / "fixture.py"
        self.script.write_text(FIXTURE_SCRIPT, encoding="utf-8")
        self.mode = self.script.with_suffix(".mode")
        model = self.base / "fixture.model"
        model.write_bytes(b"explicit backend lifecycle fixture model")
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            self.port = reserve.getsockname()[1]
        self.config = {"schema_version": 1, "endpoint": f"http://127.0.0.1:{self.port}",
            "model_id": "fixture-backend", "model_path": str(model),
            "model_sha256": hashlib.sha256(model.read_bytes()).hexdigest(), "backend_path": str(self.script),
            "backend_sha256": hashlib.sha256(self.script.read_bytes()).hexdigest(), "provenance_sha256": "b" * 64}
        self.config_path = self.base / "config.json"
        self.config_path.write_text(json.dumps(self.config), encoding="utf-8")

    def tearDown(self):
        try:
            client.control(self.state, "stop", fixture_backend=True)
        finally:
            for instance, owned in list(client._CHILDREN.items()):
                client._finish_failed_start(owned)
                client._CHILDREN.pop(instance, None)
            self.temporary.cleanup()

    def okay(self, action, **kwargs):
        value = client.control(self.state, action, fixture_backend=True, **kwargs)
        self.assertEqual(value["outcome"], "OK", value)
        self.assertEqual(value["capture_kind"], "fixture")
        protocol.validate_reply(value, action)
        return value

    def start(self):
        return self.okay("start", config=self.config_path)

    def result(self, value):
        return json.loads((self.state / "runs" / value["service_record"]["instance_id"] / "result.json").read_bytes())

    def wait_terminal(self, started):
        child = client._CHILDREN.get(started["service_record"]["instance_id"])
        if child is not None:
            child.wait(timeout=15)
        return self.result(started)

    def test_actual_lifecycle_reconnect_same_port_restart_and_stale_tuple_rejection(self):
        first = self.start()
        one = first["service_record"]
        proof = attest(self.state, self.config)
        self.assertEqual(proof["descriptor"], first["descriptor"])
        run = self.state / "runs" / one["instance_id"]
        self.assertTrue((self.state / "backend.sock").is_socket())
        self.assertFalse((run / "backend.sock").exists())
        self.assertEqual(json.loads((run / "backend-source.json").read_bytes()), first["descriptor"])
        with ProcessReader(one["supervisor_identity"]["process_id"]) as supervisor, \
                ProcessReader(one["child_identity"]["process_id"]) as backend:
            reconnect = subprocess.run([sys.executable, str(ROOT / "hosted/linux/aios-backend.py"), "status",
                "--state-dir", str(self.state)], capture_output=True, timeout=10)
            self.assertEqual(reconnect.returncode, 0, reconnect.stderr)
            self.assertEqual(json.loads(reconnect.stdout)["service_record"], one)
            second = self.okay("restart")
            with self.assertRaises(ResourceError):
                supervisor.sample()
            with self.assertRaises(ResourceError):
                backend.sample()
        two = second["service_record"]
        self.assertEqual(two["service_id"], one["service_id"])
        self.assertNotEqual(two["instance_id"], one["instance_id"])
        self.assertEqual(two["start_generation"], 2)
        self.assertNotEqual(two["backend_source_instance"], one["backend_source_instance"])
        self.assertEqual(second["descriptor"]["endpoint"], first["descriptor"]["endpoint"])
        self.assertNotEqual(attest(self.state, self.config)["descriptor"], proof["descriptor"])
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.connect(str(self.state / "control.sock"))
            protocol.send(connection, {"schema_version": 1, "action": "stop",
                **{k: one[k] for k in client.IDENTITY_KEYS}})
            stale = protocol.receive(connection)
        self.assertEqual(stale["error"], "stale-instance")
        self.assertEqual(self.okay("status")["service_record"], two)
        stopped = self.okay("stop")
        result = self.result(stopped)
        self.assertEqual((result["state"], result["exit_code"]), ("STOPPED", 0))
        self.assertTrue(result["child_exit_verified"])
        self.assertFalse(result["forced"])
        self.assertIsNone(stopped["descriptor"])
        self.assertEqual(result["descriptor"], second["descriptor"])
        self.assertFalse((self.state / "backend.sock").exists())
        self.assertEqual([json.loads(line)["event"] for line in
            (self.state / "runs" / two["instance_id"] / "events.jsonl").read_bytes().splitlines()],
            ["STARTING", "CHILD_STARTED", "RUNNING", "STOPPING", "STOPPED"])
        sys.path.insert(0, str(ROOT / "tools" / "hosted"))
        from verify_backend import verify_backend_runs
        verified = verify_backend_runs(self.state, ROOT / "hosted" / "linux", require_live=False)
        self.assertEqual(verified["outcome"], "PASS", verified)
        self.assertEqual(verify_backend_runs(self.state, require_live=True)["outcome"], "FAIL")

    def test_hash_failure_has_terminal_evidence_and_consumes_start_generation(self):
        self.config["model_sha256"] = "0" * 64
        self.config_path.write_text(json.dumps(self.config))
        failed = client.control(self.state, "start", self.config_path, fixture_backend=True)
        self.assertEqual(failed["outcome"], "ERROR", failed)
        self.assertEqual(failed["error"], "model-hash-mismatch")
        result = self.result(failed)
        self.assertEqual(result["state"], "FAILED")
        self.assertIsNone(result["service_record"]["child_identity"])
        self.assertIsNone(result["child_exit_code"])
        self.config["model_sha256"] = hashlib.sha256(Path(self.config["model_path"]).read_bytes()).hexdigest()
        self.config_path.write_text(json.dumps(self.config))
        good = self.start()
        self.assertEqual(good["service_record"]["start_generation"], 2)
        self.assertEqual(good["service_record"]["service_id"], failed["service_record"]["service_id"])
        self.okay("stop")

    def test_unexpected_child_exit_does_not_remain_ready(self):
        started = self.start()
        self.mode.write_text("exit")
        result = self.wait_terminal(started)
        self.assertEqual(result["state"], "FAILED")
        # Exit can close the listener before pidfd reports death. Either first
        # observation invalidates readiness; cleanup may deliver TERM meanwhile.
        self.assertIn(result["error"], ("backend-exited", "backend-listener"))
        self.assertTrue(result["child_exit_verified"])
        self.assertIn(result["child_exit_code"], (0, -signal.SIGTERM))
        self.assertFalse(result["service_record"]["backend_ready"])
        value = client.control(self.state, "status")
        self.assertEqual(value["outcome"], "ERROR")
        self.assertFalse(value["service_record"]["backend_ready"])
        self.assertIsNone(value["descriptor"])
        with self.assertRaises(ResourceError):
            attest(self.state, self.config)

    def test_output_overflow_is_bounded_and_terminates_owned_child(self):
        started = self.start()
        self.mode.write_text("noise")
        result = self.wait_terminal(started)
        self.assertEqual((result["state"], result["error"]), ("FAILED", "log-limit"))
        self.assertTrue(result["child_exit_verified"])
        run = self.state / "runs" / started["service_record"]["instance_id"]
        self.assertEqual((run / "stdout.log").stat().st_size, daemon.MAX_LOG_BYTES)
        self.assertLessEqual((run / "stderr.log").stat().st_size, daemon.MAX_LOG_BYTES)
        self.assertEqual(result["log_bytes"]["stdout"], daemon.MAX_LOG_BYTES)

    def test_persistence_failure_still_reaps_child_and_cannot_ack_successful_stop(self):
        started = self.start()
        run = self.state / "runs" / started["service_record"]["instance_id"]
        os.chmod(run / "service.json", 0o400)
        value = client.control(self.state, "stop")
        self.assertEqual(value["outcome"], "ERROR", value)
        result = self.wait_terminal(started)
        self.assertEqual((result["state"], result["error"]), ("FAILED", "state-io"))
        self.assertTrue(result["child_exit_verified"])

    def test_forced_child_shutdown_is_a_recorded_failure(self):
        self.mode.write_text("ignore-term")
        started = self.start()
        value = client.control(self.state, "stop")
        self.assertEqual(value["outcome"], "ERROR", value)
        result = self.result(started)
        self.assertEqual((result["state"], result["error"]), ("FAILED", "stop-forced"))
        self.assertTrue(result["forced"] and result["child_exit_verified"])
        self.assertEqual(result["child_exit_code"], -9)

    def test_concurrent_start_at_launch_limit_reserves_one_attempt_and_reaps_owned_handle(self):
        from aios_service.lifecycle import prepare_directory
        import fcntl
        prepare_directory(self.state, create=True)
        launches = prepare_directory(self.state / "launches", create=True)
        for index in range(client.MAX_LAUNCHES - 1):
            (launches / f"reserved-fixture-{index:02d}").mkdir(mode=0o700)
        barrier = threading.Barrier(6)
        original_reserve, original_popen = client._reserve_launch, subprocess.Popen
        owned_handles = []

        def together(directory):
            barrier.wait(timeout=10)
            return original_reserve(directory)

        def owned_popen(*args, **kwargs):
            handle = original_popen(*args, **kwargs)
            owned_handles.append(handle)
            return handle

        try:
            # Every contender passes the earlier state check before reservation.
            # Only the reservation itself may choose the one remaining slot.
            # There is no incumbent: suppress incidental probe-lock contention
            # so all contenders exercise the reservation race deterministically.
            with patch.object(client, "_reserve_launch", side_effect=together), \
                    patch.object(client, "_busy", return_value=False), \
                    patch.object(client.subprocess, "Popen", side_effect=owned_popen), \
                    patch.object(client, "START_SECONDS", 20), \
                    concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
                futures = [pool.submit(client.control, self.state, "start", self.config_path,
                                       fixture_backend=True) for _ in range(6)]
                values = [future.result(timeout=60) for future in futures]
            self.assertEqual(len(list(launches.iterdir())), client.MAX_LAUNCHES)
            winners = [value for value in values if value["outcome"] == "OK"]
            self.assertEqual(len(winners), 1, values)
            self.assertEqual(len(owned_handles), 1)
            self.assertTrue(all(value["error"] in ("launch-limit", "start-in-progress")
                                for value in values if value["outcome"] == "ERROR"), values)
            # A held launch reservation lock has no part in status/stop IPC.
            # Even a long start cannot hold a lock those operations acquire.
            fd = os.open(self.state / "launch.lock", os.O_RDWR | os.O_NOFOLLOW)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertEqual(self.okay("status")["state"], "RUNNING")
                self.assertEqual(self.okay("stop")["state"], "STOPPED")
            finally:
                os.close(fd)
            self.assertIsNotNone(owned_handles[0].poll())
            full = client.control(self.state, "start", self.config_path, fixture_backend=True)
            self.assertEqual(full["error"], "launch-limit")
            self.assertEqual(len(list(launches.iterdir())), client.MAX_LAUNCHES)
        finally:
            client.control(self.state, "stop", fixture_backend=True)
            # Only test-created Popen handles are eligible for fallback cleanup.
            for handle in owned_handles:
                client._finish_failed_start(handle)
                self.assertIsNotNone(handle.poll())


if __name__ == "__main__":
    unittest.main()
