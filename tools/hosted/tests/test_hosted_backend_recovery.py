"""Post-ready supervisor loss: recovery uses a previously owned live capability."""
from __future__ import annotations

import json
import os
import platform
import select
import signal
import socket
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import test_hosted_backend_runtime as backend_fixture
from aios_backend import client, protocol
from aios_service.lifecycle import atomic_json

ROOT = backend_fixture.ROOT


class RecoveryContractTests(unittest.TestCase):
    def test_recover_unsupported_never_touches_state(self):
        with patch.object(protocol, "supported", return_value=False):
            value = client.control(Path("not-created-recovery"), "recover")
        self.assertEqual((value["state"], value["error"]), ("UNSUPPORTED", "unsupported-platform"))
        protocol.validate_reply(value, "recover")

    def test_recovered_requires_an_exited_not_ready_record_and_no_descriptor(self):
        with self.assertRaises(ValueError):
            protocol.validate_reply(protocol.reply("recover", "RECOVERED"), "recover")


@unittest.skipUnless(platform.system() == "Linux" and hasattr(os, "pidfd_open")
    and hasattr(signal, "pidfd_send_signal") and hasattr(socket, "SO_PEERCRED"),
    "Linux owned Popen, retained pidfd signals and private IPC required")
class RecoveryProcessTests(unittest.TestCase):
    okay = backend_fixture.BackendProcessTests.okay

    def setUp(self):
        backend_fixture.BackendProcessTests.setUp(self)
        self.cleanup_pidfds = []

    def tearDown(self):
        # Test-owned duplicates are cleanup capabilities, never reconstructed PIDs.
        # The product intentionally has no forced-recovery path on timeout.
        try:
            for handle in self.cleanup_pidfds:
                try:
                    if not select.select([handle], [], [], 0)[0]:
                        signal.pidfd_send_signal(handle, signal.SIGKILL)
                        self.assertTrue(select.select([handle], [], [], 5)[0])
                finally:
                    os.close(handle)
            client.close_leases()
        finally:
            backend_fixture.BackendProcessTests.tearDown(self)

    def start(self):
        value = self.okay("start", config=self.config_path)
        instance = value["service_record"]["instance_id"]
        lease = client._LEASES[instance]
        self.cleanup_pidfds.append(os.dup(lease["child_reader"]._pidfd))
        return value, client._CHILDREN[instance], lease

    def crash(self, owned, lease):
        owned.kill()
        self.assertEqual(owned.wait(timeout=5), -signal.SIGKILL)
        self.assertTrue(select.select([lease["supervisor_reader"]._pidfd], [], [], 0)[0])
        self.assertFalse(select.select([lease["child_reader"]._pidfd], [], [], 0)[0])

    def fresh(self, action):
        script = ("import json,sys;from pathlib import Path;"
                  "sys.path.insert(0,sys.argv[1]);from aios_backend.client import control;"
                  "print(json.dumps(control(Path(sys.argv[2]),sys.argv[3],fixture_backend=True)))")
        result = subprocess.run([sys.executable, "-c", script, str(ROOT / "hosted/linux"), str(self.state), action],
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def test_supervisor_loss_recovers_exact_child_preserves_failed_run_and_allows_explicit_restart(self):
        started, owned, lease = self.start()
        record = started["service_record"]
        instance = record["instance_id"]
        self.crash(owned, lease)
        original = client._run_hashes(self.state, instance)
        latest = (self.state / "latest.json").read_bytes()
        denied = self.fresh("recover")
        self.assertEqual(denied["error"], "recovery-owner-required", denied)
        self.assertFalse(select.select([lease["child_reader"]._pidfd], [], [], 0)[0])
        self.assertEqual(client.control(self.state, "restart", fixture_backend=True)["error"], "stop-failed")
        child_fd, parent_fd = lease["child_reader"]._pidfd, lease["supervisor_reader"]._pidfd
        recovered = self.okay("recover")
        self.assertEqual(recovered["state"], "RECOVERED")
        self.assertFalse(recovered["service_record"]["backend_ready"])
        self.assertIsNone(recovered["descriptor"])
        self.assertEqual(client._run_hashes(self.state, instance), original)
        self.assertEqual((self.state / "latest.json").read_bytes(), latest)
        self.assertFalse((self.state / "runs" / instance / "result.json").exists())
        self.assertNotIn(instance, client._CHILDREN)
        self.assertNotIn(instance, client._LEASES)
        for handle in (child_fd, parent_fd):
            with self.assertRaises(OSError):
                os.fstat(handle)
        receipt = json.loads((self.state / "recoveries" / (instance + ".json")).read_bytes())
        self.assertEqual(receipt["recovery"]["signal_via"], "retained-pidfd")
        self.assertEqual(receipt["recovery"]["supervisor_returncode"], -signal.SIGKILL)
        self.assertTrue(receipt["recovery"]["supervisor_reaped"] and receipt["recovery"]["child_exit_observed"])
        self.assertIsNone(receipt["recovery"]["child_exit_code"])
        self.assertEqual(self.fresh("status")["state"], "RECOVERED")
        self.assertEqual(client.control(self.state, "recover")["error"], "already-recovered")
        replacement = self.fresh("start")
        self.assertEqual(replacement["outcome"], "OK", replacement)
        self.assertEqual(replacement["service_record"]["service_id"], record["service_id"])
        self.assertEqual(replacement["service_record"]["start_generation"], 2)
        self.assertNotEqual(replacement["service_record"]["instance_id"], instance)
        self.assertEqual(self.okay("stop")["state"], "STOPPED")
        sys.path.insert(0, str(ROOT / "tools/hosted"))
        from verify_backend import verify_backend_runs
        verified = verify_backend_runs(self.state, ROOT / "hosted/linux", allow_recovered=True, require_live=False)
        self.assertEqual(verified["outcome"], "PASS", verified)
        self.assertEqual([run["state"] for run in verified["runs"]], ["RECOVERED", "STOPPED"])
        self.assertEqual(verify_backend_runs(self.state, ROOT / "hosted/linux")["outcome"], "FAIL")

    def test_child_exit_before_recovery_is_observed_without_sending_a_signal(self):
        started, owned, lease = self.start()
        self.crash(owned, lease)
        signal.pidfd_send_signal(self.cleanup_pidfds[-1], signal.SIGTERM)
        self.assertTrue(select.select([self.cleanup_pidfds[-1]], [], [], 5)[0])
        with patch.object(signal, "pidfd_send_signal") as send:
            self.assertEqual(self.okay("recover")["state"], "RECOVERED")
            send.assert_not_called()
        instance = started["service_record"]["instance_id"]
        receipt = json.loads((self.state / "recoveries" / (instance + ".json")).read_bytes())
        self.assertTrue(receipt["recovery"]["child_exit_observed"])
        self.assertIsNone(receipt["recovery"]["signal"])
        self.assertIsNone(receipt["recovery"]["signal_via"])

    def test_live_supervisor_recovery_is_rejected_and_normal_stop_releases_lease(self):
        started, owned, lease = self.start()
        handles = [lease["child_reader"]._pidfd, lease["supervisor_reader"]._pidfd]
        with patch.object(signal, "pidfd_send_signal") as send:
            value = client.control(self.state, "recover")
            self.assertEqual(value["error"], "supervisor-running", value)
            send.assert_not_called()
        self.assertIsNone(owned.poll())
        self.assertIn(started["service_record"]["instance_id"], client._LEASES)
        self.okay("stop")
        self.assertNotIn(started["service_record"]["instance_id"], client._LEASES)
        for handle in handles:
            with self.assertRaises(OSError):
                os.fstat(handle)

    def test_mutated_generation_or_child_identity_never_receives_signal(self):
        started, owned, lease = self.start()
        self.crash(owned, lease)
        path = self.state / "latest.json"
        original = path.read_bytes()
        for mutation in ("generation", "child"):
            value = json.loads(original)
            if mutation == "generation":
                value["service_record"]["start_generation"] += 1
            else:
                value["service_record"]["child_identity"]["process_start_ticks"] += 1
                value["descriptor"]["process_start_ticks"] += 1
            atomic_json(path, value)
            with patch.object(signal, "pidfd_send_signal") as send:
                denied = client.control(self.state, "recover")
                self.assertEqual(denied["outcome"], "ERROR", denied)
                send.assert_not_called()
            path.write_bytes(original)
        self.assertFalse(select.select([lease["child_reader"]._pidfd], [], [], 0)[0])
        self.okay("recover")

    def test_socket_replacement_is_not_unlinked_or_signalled(self):
        started, owned, lease = self.start()
        self.crash(owned, lease)
        path = self.state / "backend.sock"
        path.unlink()
        with socket.socket(socket.AF_UNIX) as replacement:
            replacement.bind(str(path))
            os.chmod(path, 0o600)
            inode = path.stat().st_ino
            with patch.object(signal, "pidfd_send_signal") as send:
                value = client.control(self.state, "recover")
                self.assertEqual(value["error"], "recovery-socket", value)
                send.assert_not_called()
            self.assertEqual(path.stat().st_ino, inode)
            self.assertIn(started["service_record"]["instance_id"], client._LEASES)

    def test_term_timeout_is_preserved_consumes_capability_and_cannot_restart(self):
        self.mode.write_text("ignore-term")
        started, owned, lease = self.start()
        self.crash(owned, lease)
        instance = started["service_record"]["instance_id"]
        with patch.object(client, "RECOVERY_SECONDS", 0.05):
            value = client.control(self.state, "recover", fixture_backend=True)
        self.assertEqual(value["error"], "recovery-timeout", value)
        self.assertNotIn(instance, client._LEASES)
        receipt_path = self.state / "recoveries" / (instance + ".json")
        before = receipt_path.read_bytes()
        receipt = json.loads(before)
        self.assertEqual(receipt["outcome"], "FAILED")
        self.assertFalse(receipt["recovery"]["child_exit_observed"])
        self.assertFalse(select.select([self.cleanup_pidfds[-1]], [], [], 0)[0])
        self.assertEqual(client.control(self.state, "recover")["error"], "recovery-owner-required")
        self.assertEqual(client.control(self.state, "start", self.config_path)["error"], "stop-failed")
        self.assertEqual(receipt_path.read_bytes(), before)

    def test_signal_error_is_preserved_and_cannot_borrow_a_new_pidfd(self):
        started, owned, lease = self.start()
        self.crash(owned, lease)
        with patch.object(signal, "pidfd_send_signal", side_effect=PermissionError("fixture denial")) as send:
            value = client.control(self.state, "recover", fixture_backend=True)
        self.assertEqual(value["error"], "recovery-signal", value)
        send.assert_called_once()
        instance = started["service_record"]["instance_id"]
        self.assertNotIn(instance, client._LEASES)
        receipt = json.loads((self.state / "recoveries" / (instance + ".json")).read_bytes())
        self.assertEqual((receipt["outcome"], receipt["error"]), ("FAILED", "recovery-signal"))
        self.assertFalse(receipt["recovery"]["child_exit_observed"])
        self.assertIsNone(receipt["recovery"]["signal"])
        self.assertEqual(client.control(self.state, "recover")["error"], "recovery-owner-required")

    def test_rehashed_type_or_source_or_child_death_mutation_does_not_unlock_start(self):
        started, owned, lease = self.start()
        self.crash(owned, lease)
        self.okay("recover")
        instance = started["service_record"]["instance_id"]
        path = self.state / "recoveries" / (instance + ".json")
        original = path.read_bytes()
        mutations = [lambda row: row.update(schema_version=True),
            lambda row: row["recovery"].update(child_exit_observed=1),
            lambda row: row["lease"]["owner"].update(uid=False),
            lambda row: row["source_hashes"].update({"aios_backend/client.py": "0" * 64}),
            lambda row: row["recovery"].update(signal_via="stored-pid")]
        for mutate in mutations:
            value = json.loads(original)
            mutate(value)
            atomic_json(path, value)
            with patch.object(client.subprocess, "Popen") as spawn:
                denied = client.control(self.state, "start", self.config_path)
                self.assertEqual(denied["outcome"], "ERROR", denied)
                spawn.assert_not_called()
            path.write_bytes(original)
        self.assertEqual(self.okay("status")["state"], "RECOVERED")


if __name__ == "__main__":
    unittest.main()
