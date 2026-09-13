"""Actual Linux MAIN/resource integration with explicitly fixture inference."""
from __future__ import annotations

import os
import platform
import socket
import unittest

# Share only the managed fixture and Task helpers. Importing the module (rather
# than its TestCase class) does not collect the Cell tests a second time here.
import test_hosted_cell_runtime as cell_fixture


@unittest.skipUnless(platform.system() == "Linux" and hasattr(os, "pidfd_open") and hasattr(socket, "SO_PEERCRED"),
                     "Linux MAIN and backend resource IPC required")
class ResourceRuntimeTests(unittest.TestCase):
    backend = cell_fixture.CellRuntimeTests.backend
    command = cell_fixture.CellRuntimeTests.command
    okay = cell_fixture.CellRuntimeTests.okay
    admit = cell_fixture.CellRuntimeTests.admit
    finish = cell_fixture.CellRuntimeTests.finish
    request_evidence = cell_fixture.CellRuntimeTests.request_evidence

    def test_actual_link_sample_request_restart_and_failed_observation_preserve_main(self):
        with self.backend():
            first = self.okay("start", config=self.config, fixture_backend=True)
            self.okay("room-discover")
            self.okay("room-bind")
            linked = self.okay("resources-link", backend_dir=self.output)
            self.assertTrue(linked["resource_result"]["relation_current"])
            self.assertFalse(linked["resource_result"]["ownership_valid"])
            self.assertEqual(linked["resource_result"]["resource_actions"], "UNSUPPORTED")
            repeated = self.okay("resources-link", backend_dir=self.output)
            self.assertEqual(repeated["resource_result"]["relation"], linked["resource_result"]["relation"])
            self.assertEqual(repeated["resource_result"]["relation"]["relation_generation"], 1)
            sampled = self.okay("resources-sample")
            self.assertIsNotNone(sampled["resource_result"]["observation"])
            self.assertEqual(sampled["resource_result"]["observation"]["kind"], "sample")
            self.assertEqual(sampled["resource_result"]["observation"]["source_before"], sampled["source_record"])
            answer = self.finish(self.admit("Describe AIOS."))
            receipt, measured = self.request_evidence(answer)
            self.assertEqual(receipt["outcome"], "OK")
            self.assertEqual(measured["outcome"], "OK", measured)
            self.assertIsNotNone(measured["observation"])
            self.assertGreater(measured["observation"]["cpu"]["backend"]["cpu_time_ns"], 0)
            self.assertEqual(measured["observation"]["request_id"], receipt["request_id"])
            cached = self.okay("resources-status")
            self.assertEqual(cached["resource_result"]["action"], "status")
            self.assertEqual(cached["resource_result"]["observation"], measured["observation"])
            self.assertEqual(cached["source_record"], answer["source_record"])
            self.assertEqual(answer["source_record"]["source_generation"], 1)

            restarted = self.okay("restart", fixture_backend=True)
            self.assertNotEqual(restarted["source_record"]["source_instance"], first["source_record"]["source_instance"])
            stale = self.command("resources-status")
            self.assertEqual(stale["outcome"], "ERROR")
            self.assertFalse(stale["resource_result"]["relation_current"])
            self.okay("room-discover")
            self.okay("room-reconcile")
            relinked = self.okay("resources-link", backend_dir=self.output)
            relation = relinked["resource_result"]["relation"]
            self.assertEqual(relation["relation_generation"], 2)
            second = self.finish(self.admit("After explicit relink."))
            _, second_resources = self.request_evidence(second)
            self.assertEqual(second_resources["outcome"], "OK", second_resources)

            # A retained process/listener proof can keep MAIN ready while a
            # new Task cannot authenticate its target. Hide only this fixture's
            # observer socket, then restore the exact inode before any cleanup.
            before = self.okay("status")
            actual_requests = self.request_log.read_bytes()
            observer = self.output / "backend.sock"
            hidden = self.output / "backend.sock-hidden"
            original = observer.lstat()
            self.assertFalse(hidden.exists())
            observer.rename(hidden)
            try:
                moved = hidden.lstat()
                self.assertEqual((moved.st_dev, moved.st_ino), (original.st_dev, original.st_ino))
                failed_sample = self.command("resources-sample")
                self.assertEqual(failed_sample["outcome"], "ERROR", failed_sample)
                self.assertEqual(failed_sample["resource_result"]["outcome"], "ERROR")
                self.assertIsNone(failed_sample["resource_result"]["observation"])
                rejected = self.admit("This request must not pass unavailable target authentication.")
                self.assertEqual((rejected["outcome"], rejected["error"]),
                                 ("ERROR", "request-backend-not-ready"), rejected)
                self.assertIsNone(rejected["task"])
                self.assertEqual(self.request_log.read_bytes(), actual_requests)
                self.assertEqual(rejected["source_record"], before["source_record"])
                self.assertTrue(rejected["source_record"]["model_ready"])
                self.assertTrue(rejected["management_snapshot"]["binding_current"])
                retained = self.okay("status")
                self.assertEqual(retained["source_record"], before["source_record"])
                self.assertTrue(retained["management_snapshot"]["binding_current"])
            finally:
                hidden.rename(observer)
                restored = observer.lstat()
                self.assertEqual((restored.st_dev, restored.st_ino), (original.st_dev, original.st_ino))

            # This is an explicit new request after restoring authentication;
            # neither admission nor polling automatically resends a prompt.
            restored = self.admit("After explicitly restoring the fixture observer.")
            self.assertNotEqual(restored["request_id"], rejected["request_id"])
            final = self.finish(restored)
            final_receipt, final_resources = self.request_evidence(final)
            self.assertEqual(final_receipt["outcome"], "OK")
            self.assertEqual(final_resources["outcome"], "OK", final_resources)
            self.assertEqual(final["source_record"]["source_instance"], before["source_record"]["source_instance"])
            self.assertEqual(final["source_record"]["source_generation"], before["source_record"]["source_generation"])
            self.assertEqual(self.okay("stop")["state"], "STOPPED")
            verified = cell_fixture.verify_agent_runs(self.state, require_live=False)
            self.assertEqual(verified["outcome"], "PASS", verified)
            self.assertEqual(cell_fixture.verify_agent_runs(self.state, require_live=True)["outcome"], "FAIL")


if __name__ == "__main__":
    unittest.main()
