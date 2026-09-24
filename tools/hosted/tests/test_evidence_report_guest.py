"""Bounded report driver tests with synthetic command responses, no model."""
from __future__ import annotations

from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/hosted"))
import evidence_report_guest as guest


FIRST = "12345678-9abc-4def-8abc-123456789abc"
SECOND = "12345678-9abc-4def-8abc-123456789abd"
GOOD_FIRST = "comparison: SAME\neffect: NOT_EXECUTED\nnext: SAVE\nevidence: E0"
GOOD_SECOND = "stored: CONFIRMED\nnext: FINISH\nevidence: E1"


class FakeConsole:
    def __init__(self, first=GOOD_FIRST, second=GOOD_SECOND):
        self.first, self.second, self.lines, self.asks = first, second, [], 0
        self.pause = mock.Mock()
        self.context = {"validity": "CURRENT", "observation": {"observation_id": "fixed"},
                        "consumer": {"model_id": "test"}}

    def exchange(self, command):
        self.lines.append(command)
        result = {}
        if command == "space":
            result = {"space_context": self.context}
        elif command.startswith("ask "):
            self.asks += 1
            request_id = FIRST if self.asks == 1 else SECOND
            result = {"request_id": request_id,
                      "task": {"request_id": request_id, "phase": "ACCEPTED"}}
        elif command.startswith("task "):
            request_id = command.split()[-1]
            result = {"task": {"request_id": request_id, "phase": "FINISHED",
                              "model_outcome": "ANSWERED"}}
            if command.startswith("task result "):
                content = self.first if request_id == FIRST else self.second
                question = next(line[4:] for line in reversed(self.lines) if line.startswith("ask "))
                request_sha = guest.digest(b"{}")
                result["task"].update(
                    request_body="{}", request_sha256=request_sha, space_context=self.context,
                    user_prompt=question,
                    inference_receipt={"outcome": "OK", "request_id": request_id,
                                       "content": content, "request_sha256": request_sha,
                                       "request_body": "{}", "space_context": self.context,
                                       "response_body": '{"tokens_evaluated":100,"truncated":false}',
                                       "user_prompt": question})
        return {"outcome": "OK", "result": result}


def empty_report():
    return {"task_count": 0, "first_request_id": None, "second_request_id": None,
            "first_content_sha256": None, "second_content_sha256": None,
            "first_fields": None, "second_fields": None, "action_id": None, "writer": None,
            "report_expected_sha256": None, "report_readback_sha256": None,
            "report_bytes": None, "question2": None, "stage": "INITIAL"}


class EvidenceReportGuestTests(unittest.TestCase):
    def plan(self):
        return {"question1": "E0 evidence report question",
                "e0": {"run_id": "51ad65ad-7081-4c90-8bb6-419e47f307f2"}}

    def test_two_completed_tasks_after_exclusive_storage_readback(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            console, report = FakeConsole(), empty_report()
            with mock.patch.object(guest, "preflight", return_value={"request_sha256": "a" * 64}), \
                 mock.patch.object(guest.os, "getuid", create=True, return_value=1000):
                guest.run_scenario(console, path, self.plan(), report)
            self.assertEqual(console.asks, 2)
            self.assertEqual(report["stage"], "SECOND_TASK_VERIFIED")
            self.assertEqual(report["first_request_id"], FIRST)
            self.assertEqual(report["second_request_id"], SECOND)
            self.assertEqual(report["report_expected_sha256"], report["report_readback_sha256"])
            self.assertIn(b"request_id=" + FIRST.encode(), (path / "saved-report.txt").read_bytes())
            self.assertLess(console.lines.index("task result " + FIRST),
                            console.lines.index("ask " + report["question2"]))
            self.assertEqual(console.pause.call_count, 0)

    def test_first_semantic_failure_does_not_write_or_admit_second(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            console, report = FakeConsole(first="comparison: DIFFERENT\neffect: NOT_EXECUTED\nnext: SAVE\nevidence: E0"), empty_report()
            with mock.patch.object(guest, "preflight", return_value={"request_sha256": "a" * 64}):
                with self.assertRaisesRegex(guest.SmokeFailure, "first_response_incorrect_or_stop"):
                    guest.run_scenario(console, path, self.plan(), report)
            self.assertEqual(console.asks, 1)
            self.assertFalse((path / "saved-report.txt").exists())

    def test_cleanup_error_cannot_overwrite_completed_tasks_with_pass(self):
        class CleanupConsole(FakeConsole):
            def __init__(self, process, destination, report, *captures):
                super().__init__()
                self.process, self.report = process, report

            def initial(self):
                pass

            def exchange(self, command):
                result = super().exchange(command)
                self.report["commands"].append(
                    {"command": command, "completion": "exit" if command == "exit" else "prompt"})
                if command == "agent stop":
                    result["outcome"] = "ERROR"
                elif command == "exit":
                    self.process.returncode = 0
                return result

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            plan = dict(self.plan(), schema_version=1, scenario=guest.SCENARIO, source_files={})
            plan_path = path / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            process = mock.Mock(pid=1234, returncode=None)
            process.poll.side_effect = lambda: process.returncode
            with mock.patch.object(guest.subprocess, "Popen", return_value=process), \
                 mock.patch.object(guest, "Capture"), \
                 mock.patch.object(guest, "Console", CleanupConsole), \
                 mock.patch.object(guest, "preflight", return_value={"request_sha256": "a" * 64}), \
                 mock.patch.object(guest.os, "getuid", create=True, return_value=1000):
                exit_code = guest.run(path / "result", plan_path, command=["synthetic-cli"])
            report = json.loads((path / "result/evidence-report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["task_count"], 2)
            self.assertEqual(report["stage"], "MODEL_TASKS_COMPLETE")
            self.assertEqual(report["termination"], "exit")
            self.assertEqual(process.returncode, 0)
            self.assertEqual([row["command"] for row in report["commands"]][-3:], list(guest.SUFFIX))
            self.assertEqual(exit_code, 1)
            self.assertEqual(report["outcome"], "FAIL")
            self.assertEqual(report["failure"], "cleanup:SmokeFailure:cleanup_command:agent stop")
            process.terminate.assert_not_called()
            process.kill.assert_not_called()

    def test_exclusive_report_cannot_be_overwritten(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            e0 = self.plan()["e0"]
            first = ("SAME", "NOT_EXECUTED", "SAVE")
            a = guest.create_report(path, e0, FIRST, "a" * 64, first)
            self.assertEqual(a[0], a[1])
            with self.assertRaisesRegex(guest.SmokeFailure, "report_target_exists"):
                guest.create_report(path, e0, FIRST, "b" * 64, first)
            self.assertIn(b"response_sha256=" + b"a" * 64, (path / "saved-report.txt").read_bytes())

    def test_question_preflight_rejects_budget_before_tokenizer(self):
        with tempfile.TemporaryDirectory() as folder:
            with mock.patch.object(guest.http.client, "HTTPConnection") as endpoint:
                with self.assertRaisesRegex(guest.SmokeFailure, "question_budget"):
                    guest.preflight(Path(folder), 1, "x" * 2048, {"validity": "CURRENT"})
                endpoint.assert_not_called()

    def test_output_format_requires_exact_fields_and_evidence_reference(self):
        self.assertIsNotNone(guest.FIRST_FORMAT.fullmatch(GOOD_FIRST))
        self.assertIsNone(guest.FIRST_FORMAT.fullmatch(GOOD_FIRST + "\ncomment"))
        self.assertIsNone(guest.FIRST_FORMAT.fullmatch(GOOD_FIRST.replace("E0", "E1")))
        self.assertIsNone(guest.SECOND_FORMAT.fullmatch(GOOD_SECOND + "\nextra"))


if __name__ == "__main__":
    unittest.main()
