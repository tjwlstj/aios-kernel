"""Service contracts plus real Linux process/socket lifecycle tests.

Only the Linux group launches a daemon. Pure wire tests also run on Windows;
they do not turn unsupported hosts into lifecycle evidence.
"""
from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted/linux"))
sys.path.insert(0, str(ROOT / "tools/hosted"))
from aios_service import client, lifecycle
from verify_service import verify_service_runs


class ServiceContractTests(unittest.TestCase):
    def identity(self):
        return {"service_id": str(uuid.uuid4()), "instance_id": str(uuid.uuid4()), "generation": 1}

    def running(self):
        return lifecycle.public("RUNNING", identity=self.identity(), pid=123,
                                boot_id=str(uuid.uuid4()), count=1, heartbeat=20)

    def test_public_valid_states_and_boundaries(self):
        for value in (lifecycle.public(), self.running(),
                      lifecycle.public("STARTING", identity=self.identity()),
                      lifecycle.public("STALE", identity=self.identity(), error="PROCESS_NOT_RUNNING"),
                      lifecycle.public("FAILED", error="STATE_CORRUPT"),
                      lifecycle.public("UNSUPPORTED", error="UNSUPPORTED_PLATFORM")):
            self.assertIs(lifecycle.validate_public(value), value)

    def test_unknown_keys_types_numeric_bools_and_noncanonical_ids_reject(self):
        for field, value in (("extra", 1), ("schema_version", True), ("generation", True),
                             ("pid", 0), ("observation_sequence", -1), ("state", []),
                             ("error", []), ("service_id", "NOT-A-UUID"),
                             ("source_only", False), ("binding_status", "BOUND")):
            with self.subTest(field=field):
                row = {**self.running(), field: value}
                with self.assertRaises(lifecycle.ServiceError):
                    lifecycle.validate_public(row)

    def test_incomplete_running_identity_or_heartbeat_rejects(self):
        for field in ("service_id", "instance_id", "generation", "pid", "boot_id", "heartbeat_monotonic_ns"):
            row = {**self.running(), field: None}
            with self.subTest(field=field), self.assertRaises(lifecycle.ServiceError):
                lifecycle.validate_public(row)
        with self.assertRaises(lifecycle.ServiceError):
            lifecycle.validate_public(lifecycle.public("RUNNING", identity=self.identity()))

    def test_stale_instance_is_distinct(self):
        with self.assertRaisesRegex(lifecycle.ServiceError, "STALE_INSTANCE"):
            lifecycle.validate_public(self.running(), self.identity())

    def test_strict_json_rejects_duplicate_nonfinite_oversize_and_deep(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'[]', b'\xff',
                    b'{"x":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}',
                    b'{"x":' + b'[' * lifecycle.MAX_JSON_DEPTH + b'0' + b']' * lifecycle.MAX_JSON_DEPTH + b'}',
                    b'{"x":[' + b'0,' * lifecycle.MAX_JSON_NODES + b'0]}',
                    b' ' * (lifecycle.MAX_JSON + 1)):
            with self.subTest(raw=raw[:30]), self.assertRaises(lifecycle.ServiceError):
                lifecycle.strict_json(raw)

    def test_receive_rejects_multiple_frames_trailing_data_and_eof(self):
        for chunks in ([b'{}\n{}\n'], [b'{}\nx'], [b'{}', b''], [b'x' * 4097]):
            connection = mock.Mock()
            connection.recv.side_effect = chunks
            with self.subTest(chunks=chunks[0][:20]), self.assertRaises(lifecycle.ServiceError):
                lifecycle.receive(connection)

    def test_nonlinux_returns_unsupported_without_state_mutation(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "state"
            with mock.patch.object(client, "supported", return_value=False):
                result = client.control(path, "start")
            self.assertEqual(result["state"], "UNSUPPORTED")
            self.assertFalse(path.exists())

    def test_source_provenance_has_eight_exact_files(self):
        hashes = lifecycle.source_hashes()
        self.assertEqual(set(hashes), {"aios-service.py", "aios_service/__init__.py",
                         "aios_service/lifecycle.py", "aios_service/client.py", "aios-boot.py",
                         "aios_hosted/__init__.py", "aios_hosted/boot.py", "aios_hosted/hardware.py"})
        for name, digest in hashes.items():
            self.assertEqual(digest, hashlib.sha256((ROOT / "hosted/linux" / name).read_bytes()).hexdigest())


@unittest.skipUnless(lifecycle.supported() and hasattr(os, "pidfd_open"),
                     "Linux AF_UNIX peer credentials and pidfd required")
class LinuxServiceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ais-")
        self.state = Path(self.temporary.name) / "s"
        self.addCleanup(self.temporary.cleanup)
        self.addCleanup(self.cleanup_service)

    def cleanup_service(self):
        client.control(self.state, "stop")
        # Reap only children created by this test process, never PIDs read from disk.
        for key, child in list(client._CHILDREN.items()):
            if child.poll() is not None:
                child.wait()
                del client._CHILDREN[key]

    def start(self):
        result = client.control(self.state, "start")
        self.assertEqual((result["outcome"], result["state"]), ("OK", "RUNNING"), result)
        return result

    def terminal_run(self, result):
        return self.state / "runs" / result["instance_id"]

    def test_start_heartbeat_stop_and_bounded_artifacts(self):
        started = self.start()
        self.assertEqual(os.stat(self.state).st_mode & 0o777, 0o700)
        self.assertEqual(os.stat(self.state / "control.sock").st_mode & 0o777, 0o600)
        run = self.terminal_run(started)
        initial_files = sorted(path.name for path in run.iterdir())
        deadline = time.monotonic() + 6
        current = started
        while current["observation_sequence"] <= started["observation_sequence"] and time.monotonic() < deadline:
            time.sleep(0.1)
            current = client.control(self.state, "status")
        self.assertGreater(current["observation_sequence"], started["observation_sequence"])
        self.assertGreater(current["heartbeat_monotonic_ns"], started["heartbeat_monotonic_ns"])
        self.assertEqual(initial_files, sorted(path.name for path in run.iterdir()))
        self.assertEqual(len((run / "lifecycle.events.jsonl").read_text().splitlines()), 2)
        stopped = client.control(self.state, "stop")
        self.assertEqual((stopped["outcome"], stopped["state"]), ("OK", "STOPPED"), stopped)
        self.assertFalse((self.state / "control.sock").exists())
        result = json.loads((run / "result.json").read_text())
        self.assertEqual(result["exit_code"], 0)
        events = [json.loads(line) for line in (run / "lifecycle.events.jsonl").read_text().splitlines()]
        self.assertEqual([event["event"] for event in events], ["STARTING", "RUNNING", "STOPPING", "STOPPED"])
        for name, digest in result["files"].items():
            self.assertEqual(hashlib.sha256((run / name).read_bytes()).hexdigest(), digest)
        verdict = verify_service_runs(self.state, source_root=ROOT / "hosted/linux", require_live=True)
        self.assertEqual(verdict["outcome"], "PASS", verdict)

    def test_restart_preserves_service_id_and_advances_instance_generation(self):
        first = self.start()
        second = client.control(self.state, "restart")
        self.assertEqual(second["state"], "RUNNING", second)
        self.assertEqual(second["outcome"], "OK", second)
        self.assertEqual(first["service_id"], second["service_id"])
        self.assertNotEqual(first["instance_id"], second["instance_id"])
        self.assertEqual(second["generation"], first["generation"] + 1)
        self.assertEqual(json.loads((self.terminal_run(first) / "result.json").read_text())["state"], "STOPPED")

    def test_concurrent_start_has_one_owner(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: client.control(self.state, "start"), range(2)))
        winners = [row for row in results if row["outcome"] == "OK"]
        self.assertEqual(len(winners), 1, results)
        self.assertEqual(winners[0]["generation"], 1)
        self.assertEqual(client.control(self.state, "status")["instance_id"], winners[0]["instance_id"])

    def test_abrupt_death_reports_stale_and_explicit_restart_recovers(self):
        first = self.start()
        child = client._CHILDREN.pop(first["instance_id"])
        child.kill()
        child.wait(timeout=3)
        dead = client.control(self.state, "status")
        self.assertEqual((dead["state"], dead["error"]), ("STALE", "PROCESS_NOT_RUNNING"), dead)
        self.assertFalse((self.terminal_run(first) / "result.json").exists())
        second = client.control(self.state, "restart")
        self.assertEqual((second["outcome"], second["state"]), ("OK", "RUNNING"), second)
        self.assertEqual(second["generation"], first["generation"] + 1)

    def test_stale_rpc_and_malformed_request_do_not_stop_current_instance(self):
        current = self.start()
        stale = {key: current[key] for key in lifecycle.IDENTITY}
        stale["generation"] += 1
        for request, expected in ((lifecycle.encoded({"schema_version": 1, "action": "stop", **stale}), "STALE_INSTANCE"),
                                  (b'{"schema_version":1,"schema_version":1}\n', "PROTOCOL_ERROR")):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(3)
                connection.connect(str(self.state / "control.sock"))
                connection.sendall(request)
                response = lifecycle.receive(connection)
            self.assertEqual(response["error"], expected)
            self.assertEqual(client.control(self.state, "status")["state"], "RUNNING")

    def test_peer_mismatch_rejects_control_without_stopping_daemon(self):
        self.start()
        with mock.patch.object(client, "peer_credentials", return_value=(os.getpid(), os.getuid() + 1, os.getgid())):
            result = client.control(self.state, "stop")
        self.assertEqual(result["error"], "PEER_MISMATCH")
        self.assertEqual(client.control(self.state, "status")["state"], "RUNNING")

    def test_pidfd_unavailable_never_sends_stop(self):
        self.start()
        with mock.patch.object(os, "pidfd_open", side_effect=OSError("unavailable")):
            result = client.control(self.state, "stop")
        self.assertEqual(result["error"], "PIDFD_UNAVAILABLE")
        self.assertEqual(client.control(self.state, "status")["state"], "RUNNING")

    def test_actual_exit_timeout_is_error_not_stopped_success(self):
        current = self.start()
        with mock.patch.object(client.select, "select", return_value=([], [], [])):
            result = client.control(self.state, "stop")
        self.assertEqual((result["outcome"], result["error"]), ("ERROR", "STOP_TIMEOUT"))
        child = client._CHILDREN[current["instance_id"]]
        child.wait(timeout=3)
        self.assertEqual(client.control(self.state, "status")["state"], "STOPPED")

    def test_daemon_survives_control_process_exit(self):
        entry = ROOT / "hosted/linux/aios-service.py"
        completed = subprocess.run([sys.executable, str(entry), "start", "--state-dir", str(self.state)],
                                   capture_output=True, timeout=15, check=True)
        current = json.loads(completed.stdout)
        self.assertEqual(completed.returncode, 0)
        after = client.control(self.state, "status")
        self.assertEqual(after["state"], "RUNNING", after)
        self.assertEqual(after["instance_id"], current["instance_id"])
        self.assertEqual(client.control(self.state, "stop")["state"], "STOPPED")

    def test_failed_observation_can_restart_with_preserved_failure_evidence(self):
        lifecycle.prepare_directory(self.state, create=True)
        code = ("import sys; from pathlib import Path; "
                "from aios_service import lifecycle; "
                "lifecycle.collect_cpu=lambda _: {'status':'unavailable'}; "
                "sys.exit(lifecycle.serve(Path(sys.argv[1])))")
        env = {**os.environ, "PYTHONPATH": str(ROOT / "hosted/linux")}
        completed = subprocess.run([sys.executable, "-c", code, str(self.state)], env=env,
                                   capture_output=True, timeout=15)
        self.assertEqual(completed.returncode, 1, completed.stderr)
        failed = client.control(self.state, "status")
        self.assertEqual((failed["state"], failed["error"]), ("FAILED", "OBSERVATION_FAILED"), failed)
        first_run = self.terminal_run(failed)
        self.assertEqual(json.loads((first_run / "result.json").read_text())["exit_code"], 1)
        restarted = client.control(self.state, "restart")
        self.assertEqual((restarted["outcome"], restarted["state"]), ("OK", "RUNNING"), restarted)
        self.assertEqual(restarted["service_id"], failed["service_id"])
        self.assertEqual(restarted["generation"], failed["generation"] + 1)
        self.assertEqual(json.loads((first_run / "result.json").read_text())["state"], "FAILED")

    def test_symlink_public_directory_and_hardlinked_state_reject(self):
        target = Path(self.temporary.name) / "target"
        target.mkdir(mode=0o700)
        self.state.symlink_to(target, target_is_directory=True)
        self.assertEqual(client.control(self.state, "start")["error"], "INVALID_STATE_DIRECTORY")
        self.state.unlink()
        self.state.mkdir(mode=0o755)
        self.assertEqual(client.control(self.state, "start")["error"], "INVALID_STATE_DIRECTORY")
        self.state.chmod(0o700)
        record = target / "record"
        record.write_text("{}")
        record.chmod(0o600)
        os.link(record, self.state / "registry.json")
        self.assertEqual(client.control(self.state, "status")["error"], "STATE_CORRUPT")
        (self.state / "registry.json").unlink()

    def test_malformed_terminal_result_rejects_status_and_restart(self):
        current = self.start()
        self.assertEqual(client.control(self.state, "stop")["state"], "STOPPED")
        path = self.terminal_run(current) / "result.json"
        original = json.loads(path.read_text())
        for field, value in (("generation", True), ("exit_code", True), ("extra", 1),
                             ("observation_sequence", -1), ("reason", "unexpected"),
                             ("completed_at", "not-a-timestamp"), ("files", {}),
                             ("files", {**original["files"], "latest.json": "0" * 64})):
            lifecycle.atomic_json(path, {**original, field: value})
            with self.subTest(field=field):
                self.assertEqual(client.control(self.state, "status")["error"], "STATE_CORRUPT")
                self.assertEqual(client.control(self.state, "restart")["error"], "STATE_CORRUPT")
        lifecycle.atomic_json(path, original)

    def test_malformed_registry_never_gets_replaced_by_start(self):
        lifecycle.prepare_directory(self.state, create=True)
        raw = b'{"schema_version":1,"schema_version":1}\n'
        path = self.state / "registry.json"
        path.write_bytes(raw)
        path.chmod(0o600)
        for action in ("start", "status", "stop", "restart"):
            self.assertEqual(client.control(self.state, action)["error"], "STATE_CORRUPT")
        self.assertEqual(path.read_bytes(), raw)

    def test_symlinked_terminal_run_is_rejected(self):
        current = self.start()
        self.assertEqual(client.control(self.state, "stop")["state"], "STOPPED")
        run = self.terminal_run(current)
        moved = Path(self.temporary.name) / "retained"
        run.rename(moved)
        run.symlink_to(moved, target_is_directory=True)
        try:
            for action in ("status", "start", "restart"):
                self.assertEqual(client.control(self.state, action)["error"], "INVALID_STATE_DIRECTORY")
        finally:
            run.unlink()
            moved.rename(run)

    def test_setgid_parent_normalizes_only_new_state_directories(self):
        parent = Path(self.temporary.name)
        parent.chmod(0o2755)
        self.state.mkdir(mode=0o700)
        self.state.chmod(0o2700)
        # Existing state directories are never silently repaired.
        self.assertEqual(client.control(self.state, "start")["error"], "INVALID_STATE_DIRECTORY")
        self.assertEqual(self.state.stat().st_mode & 0o7777, 0o2700)
        self.state.rmdir()
        self.start()
        self.assertEqual(self.state.stat().st_mode & 0o7777, 0o700)
        self.assertEqual(parent.stat().st_mode & 0o7777, 0o2755)
        stopped = client.control(self.state, "stop")
        self.assertEqual((stopped["outcome"], stopped["state"]), ("OK", "STOPPED"), stopped)


if __name__ == "__main__":
    unittest.main()
