"""Question lifecycle semantics only; fixture evidence is not Linux/model proof."""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "hosted/linux"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from aios_agent.request_state import RECEIPT_KEYS, RequestState
from test_hosted_console import agent, backend


class RequestStateTests(unittest.TestCase):
    def setUp(self):
        current = agent("space", bound=True)
        self.expected = backend()
        boot = current["source_record"]["host_boot_id"]
        for key in ("supervisor_identity", "child_identity"):
            self.expected["service_record"][key]["host_boot_id"] = boot
        self.expected["descriptor"]["host_boot_id"] = boot
        self.owner = {"host_boot_id": boot, "process_id": 303, "process_start_ticks": 102, "uid": 1000}
        self.config = {"schema_version": 1, "endpoint": "http://127.0.0.1:8089", "model_id": "fixture-main",
                       "model_sha256": "a" * 64, "backend_sha256": "d" * 64, "provenance_sha256": "e" * 64,
                       "model_path": "fixture.gguf", "backend_path": "fixture-backend"}
        self.kwargs = {"request_id": "00000000-0000-4000-8000-000000000015", "owner": self.owner,
                       "config": self.config, "source": current["source_record"],
                       "management": current["management_snapshot"], "space_context": current["space_context"],
                       "prompt": "hello", "backend_expected": self.expected, "accepted_ns": 100}
        self.state = RequestState(**self.kwargs)
        self.worker = 1009

    def answer(self):
        raw = agent("ask", bound=True, prompt="hello")["inference_receipt"]
        receipt = {key: copy.deepcopy(raw[key]) for key in RECEIPT_KEYS}
        # This test owns only state/result joins. Execution proof contents must
        # be checked by the transport and later independent Linux verifier.
        receipt["backend_execution"] = {"schema_version": 1,
            "descriptor": copy.deepcopy(self.expected["descriptor"]), "capture_kind": "fixture",
            "before": {"state_fixture": True}, "send": {"state_fixture": True}, "after": {"state_fixture": True}}
        return receipt

    def stopped(self):
        value = copy.deepcopy(self.expected)
        value.update(action="stop", state="STOPPED", descriptor=None)
        value["service_record"].update(lifecycle_state="exited", backend_ready=False)
        return value

    def test_query_and_input_mutation_do_not_change_admitted_target(self):
        before = self.state.snapshot()
        self.owner["process_start_ticks"] += 1
        self.expected["descriptor"]["source_generation"] += 1
        self.kwargs["space_context"]["validity"] = "STALE"
        copy_out = self.state.snapshot()
        copy_out["owner"]["uid"] = 0
        self.assertEqual(self.state.snapshot(), before)
        self.assertEqual(self.state.snapshot()["phase"], "ACCEPTED")
        self.assertIsNone(self.state.snapshot()["model_outcome"])

    def test_cancel_before_dispatch_prevents_worker_start(self):
        self.assertEqual(self.state.request_cancel(self.owner, now_ns=101), "ACCEPTED")
        with self.assertRaisesRegex(ValueError, "request-dispatch-state"):
            self.state.mark_running(self.worker, now_ns=102)
        self.state.finish_without_dispatch("cancel-before-dispatch", now_ns=102)
        row = self.state.snapshot()
        self.assertEqual(row["model_outcome"], "NOT_STARTED")
        self.assertIsNone(row["worker_process_id"])
        self.assertIsNone(row["backend_stop"])

    def test_wrong_owner_and_pid_reuse_cannot_cancel(self):
        for key, value in (("process_id", 304), ("process_start_ticks", 103), ("uid", 1001),
                           ("host_boot_id", "00000000-0000-4000-8000-000000000099")):
            with self.subTest(key=key):
                wrong = {**self.owner, key: value}
                with self.assertRaisesRegex(ValueError, "request-owner-mismatch"):
                    self.state.request_cancel(wrong, now_ns=101)
        self.assertEqual(self.state.snapshot()["revision"], 1)

    def test_worker_exit_does_not_confirm_backend_stop_or_answer(self):
        self.state.mark_running(self.worker, now_ns=101)
        self.state.request_cancel(self.owner, now_ns=102)
        self.assertTrue(self.state.record_worker_exit(self.worker, -15, receipt=None, now_ns=103))
        row = self.state.snapshot()
        self.assertEqual(row["model_outcome"], "UNKNOWN")
        self.assertIsNone(row["backend_stop"])
        self.assertTrue(self.state.record_backend_stop(self.stopped(), now_ns=104))
        row = self.state.snapshot()
        self.assertEqual(row["model_outcome"], "UNKNOWN")
        self.assertEqual(row["backend_stop"]["state"], "STOPPED")

    def test_backend_stop_does_not_finish_live_worker(self):
        self.state.mark_running(self.worker, now_ns=101)
        self.state.request_cancel(self.owner, now_ns=102)
        self.state.record_backend_stop(self.stopped(), now_ns=103)
        self.assertFalse(self.state.terminal)
        self.assertIsNone(self.state.snapshot()["model_outcome"])
        self.state.record_worker_exit(self.worker, -15, receipt=None, now_ns=104)
        self.assertTrue(self.state.terminal)

    def test_unknown_terminal_allows_one_late_backend_stop(self):
        self.state.mark_running(self.worker, now_ns=101)
        self.state.record_worker_exit(self.worker, -15, receipt=None, now_ns=102)
        before = self.state.snapshot()
        self.assertEqual(self.state.request_cancel(self.owner, now_ns=103), "ACCEPTED")
        row = self.state.snapshot()
        self.assertEqual(row["phase"], "FINISHED")
        self.assertEqual(row["model_outcome"], "UNKNOWN")
        self.assertEqual(row["finished_ns"], before["finished_ns"])
        self.assertEqual(self.state.request_cancel(self.owner, now_ns=104), "ALREADY_REQUESTED")
        self.assertEqual(self.state.snapshot(), row)
        self.state.record_backend_stop(self.stopped(), now_ns=105)
        self.assertEqual(self.state.snapshot()["model_outcome"], "UNKNOWN")

    def test_verified_completion_wins_without_erasing_late_cancel_evidence(self):
        self.state.mark_running(self.worker, now_ns=101)
        self.state.request_cancel(self.owner, now_ns=102)
        answer = self.answer()
        self.state.record_worker_exit(self.worker, 0, receipt=answer, now_ns=103)
        self.state.record_backend_stop(self.stopped(), now_ns=104)
        row = self.state.snapshot()
        self.assertEqual(row["model_outcome"], "ANSWERED")
        self.assertEqual(row["cancel_requested_ns"], 102)
        self.assertEqual(row["inference_receipt"], answer)
        self.assertEqual(self.state.request_cancel(self.owner, now_ns=105), "ALREADY_TERMINAL")
        self.assertEqual(self.state.snapshot(), row)

    def test_duplicate_terminal_observations_do_not_change_revision(self):
        self.state.mark_running(self.worker, now_ns=101)
        self.state.request_cancel(self.owner, now_ns=102)
        row = self.state.snapshot()
        self.assertEqual(self.state.request_cancel(self.owner, now_ns=103), "ALREADY_REQUESTED")
        self.assertEqual(self.state.snapshot(), row)
        self.state.record_worker_exit(self.worker, -15, receipt=None, now_ns=104)
        self.state.record_backend_stop(self.stopped(), now_ns=105)
        row = self.state.snapshot()
        self.assertFalse(self.state.record_worker_exit(self.worker, -15, receipt=None, now_ns=106))
        self.assertFalse(self.state.record_backend_stop(self.stopped(), now_ns=107))
        self.assertEqual(self.state.snapshot(), row)
        with self.assertRaisesRegex(ValueError, "request-terminal-rewrite"):
            self.state.record_worker_exit(self.worker, 0, receipt=self.answer(), now_ns=108)

    def test_replacement_backend_or_unconfirmed_stop_is_not_success(self):
        self.state.mark_running(self.worker, now_ns=101)
        self.state.request_cancel(self.owner, now_ns=102)
        for key, value in (("start_generation", 2), ("instance_id", "00000000-0000-4000-8000-000000000088"),
                           ("config_sha256", "0" * 64)):
            changed = self.stopped()
            changed["service_record"][key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "request-backend-stop-mismatch"):
                self.state.record_backend_stop(changed, now_ns=103)
        failed = self.stopped()
        failed.update(outcome="ERROR", error="stop-timeout")
        with self.assertRaisesRegex(ValueError, "request-backend-stop-unconfirmed"):
            self.state.record_backend_stop(failed, now_ns=103)
        self.assertIsNone(self.state.snapshot()["backend_stop"])

    def test_foreign_or_corrupt_answer_is_not_joined_to_this_question(self):
        self.state.mark_running(self.worker, now_ns=101)
        for key, value in (("request_id", "00000000-0000-4000-8000-000000000089"),
                           ("user_prompt", "another question"), ("request_sha256", "0" * 64),
                           ("response_sha256", "0" * 64), ("content", "forged answer"),
                           ("tokens_predicted", True)):
            receipt = self.answer()
            receipt[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                self.state.record_worker_exit(self.worker, 0, receipt=receipt, now_ns=102)
        self.assertFalse(self.state.terminal)

    def test_wrong_worker_clock_and_false_exit_are_refused(self):
        with self.assertRaisesRegex(ValueError, "request-worker-identity"):
            self.state.mark_running(self.owner["process_id"], now_ns=101)
        self.state.mark_running(self.worker, now_ns=101)
        with self.assertRaisesRegex(ValueError, "request-worker-identity"):
            self.state.record_worker_exit(self.worker + 1, 0, receipt=self.answer(), now_ns=102)
        with self.assertRaisesRegex(ValueError, "request-worker-exit"):
            self.state.record_worker_exit(self.worker, True, receipt=None, now_ns=102)
        with self.assertRaisesRegex(ValueError, "request-clock-regression"):
            self.state.request_cancel(self.owner, now_ns=100)
        with self.assertRaisesRegex(ValueError, "request-worker-exit"):
            self.state.record_worker_exit(self.worker, -15, receipt=self.answer(), now_ns=102)
        self.assertFalse(self.state.terminal)


if __name__ == "__main__":
    unittest.main()
