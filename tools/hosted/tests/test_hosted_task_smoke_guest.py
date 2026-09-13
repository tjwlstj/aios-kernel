"""Bounded harness tests; only tiny synthetic CLI children, never a model/VM."""
from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/hosted"))
import task_smoke_guest as guest

FIRST = "12345678-9abc-4def-8abc-123456789abc"
SECOND = "12345678-9abc-4def-8abc-123456789abd"


class Scenario:
    def __init__(self, *, early_second=False, first_unknown=False, never_stopped=False):
        self.lines, self.asks = [], 0
        self.early_second, self.first_unknown, self.never_stopped = early_second, first_unknown, never_stopped
        self.cancelled = False

    def exchange(self, line):
        self.lines.append(line)
        result = {}
        if line.startswith("ask "):
            self.asks += 1
            identity = FIRST if self.asks == 1 else SECOND
            result = {"request_id": identity, "task": {"request_id": identity, "phase": "ACCEPTED"}}
        elif line.startswith("task status "):
            identity = line.split()[-1]
            phase = "FINISHED" if identity == FIRST or self.early_second or self.cancelled else "RUNNING"
            outcome = "UNKNOWN" if self.first_unknown or self.cancelled else "ANSWERED"
            result = {"task": {"request_id": identity, "phase": phase, "model_outcome": outcome,
                               "backend_stop": {} if self.cancelled and not self.never_stopped else None}}
        elif line.startswith("task cancel "):
            self.cancelled = True
            result = {"task_control": {"cancel_outcome": "ACCEPTED"}}
        return {"outcome": "OK", "result": result}


class TaskSmokeScenarioTests(unittest.TestCase):
    def test_exactly_two_admissions_and_one_explicit_cancel(self):
        scenario = Scenario()
        pause = mock.Mock()
        guest.run_scenario(scenario.exchange, pause=pause)
        self.assertEqual(scenario.asks, 2)
        self.assertEqual(scenario.lines.count("task cancel " + SECOND), 1)
        self.assertEqual(scenario.lines[0], "backend start")
        self.assertEqual(scenario.lines[-3:], ["agent stop", "backend stop", "exit"])
        index = scenario.lines.index("task cancel " + SECOND)
        self.assertEqual(scenario.lines[index - 1], "task status " + SECOND)
        pause.assert_not_called()

    def test_finished_second_question_fails_without_cancel_or_retry(self):
        scenario = Scenario(early_second=True)
        with self.assertRaisesRegex(guest.SmokeFailure, "no_retry"):
            guest.run_scenario(scenario.exchange, pause=mock.Mock())
        self.assertEqual(scenario.asks, 2)
        self.assertFalse(any(line.startswith("task cancel ") for line in scenario.lines))

    def test_first_failure_does_not_submit_a_second_question(self):
        scenario = Scenario(first_unknown=True)
        with self.assertRaisesRegex(guest.SmokeFailure, "first_task_not_answered"):
            guest.run_scenario(scenario.exchange, pause=mock.Mock())
        self.assertEqual(scenario.asks, 1)

    def test_cancel_missing_independent_stop_is_bounded_and_never_recancelled(self):
        scenario = Scenario(never_stopped=True)
        pause = mock.Mock()
        with self.assertRaisesRegex(TimeoutError, "cancel_terminal_timeout"):
            guest.run_scenario(scenario.exchange, pause=pause)
        self.assertEqual(scenario.lines.count("task cancel " + SECOND), 1)
        self.assertEqual(scenario.lines.count("task status " + SECOND), 1 + guest.LIMITS["cancel_polls"])
        self.assertEqual(pause.call_count, guest.LIMITS["cancel_polls"] - 1)


class TaskSmokeJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "events.jsonl"
        self.start = {"schema_version": 10, "sequence": 1, "session_id": FIRST, "event": "START", "data": {}}
        self.journal = guest.EventJournal(self.path)

    def write(self, *events):
        self.path.write_bytes(b"".join((json.dumps(event) + "\n").encode() for event in events))

    def command(self, **changes):
        row = {"schema_version": 10, "sequence": 2, "session_id": FIRST, "event": "COMMAND",
               "data": {"name": "task", "args": ["status", SECOND], "outcome": "OK",
                        "result": {"request_id": SECOND}}}
        row.update(changes)
        return row

    def test_partial_json_is_not_a_completed_command(self):
        self.write(self.start)
        partial = json.dumps(self.command()).encode()
        with self.path.open("ab") as output:
            output.write(partial)
        self.assertIsNone(self.journal.command(1, "task status " + SECOND))
        with self.path.open("ab") as output:
            output.write(b"\n")
        self.assertEqual(self.journal.command(1, "task status " + SECOND)["sequence"], 2)

    def test_human_prompt_does_not_replace_command_evidence(self):
        self.write(self.start)
        self.assertIsNone(self.journal.command(1, "task status " + SECOND))
        self.assertTrue(guest.PROMPT.search(b"model says aios> \naios> "))
        self.assertIsNone(self.journal.command(1, "task status " + SECOND))

    def test_sequence_uuid_session_and_duplicate_key_impostors_fail(self):
        for change, reason in (({"sequence": 3}, "sequence"), ({"sequence": True}, "sequence"),
                               ({"session_id": SECOND}, "session_changed")):
            with self.subTest(change=change):
                self.journal = guest.EventJournal(self.path)
                self.write(self.start, self.command(**change))
                with self.assertRaisesRegex(guest.SmokeFailure, reason):
                    self.journal.command(1, "task status " + SECOND)
        wrong = self.command()
        wrong["data"]["result"]["request_id"] = FIRST
        self.write(self.start, wrong)
        with self.assertRaisesRegex(guest.SmokeFailure, "request_id"):
            guest.EventJournal(self.path).command(1, "task status " + SECOND)
        self.write(self.start)
        with self.path.open("ab") as output:
            output.write(json.dumps(self.command()).replace('"sequence": 2', '"sequence": 2, "sequence": 2').encode() + b"\n")
        with self.assertRaisesRegex(guest.SmokeFailure, "duplicate_key"):
            guest.EventJournal(self.path).refresh()

    def test_existing_event_rewrite_fails(self):
        self.write(self.start)
        self.journal.refresh()
        self.write({**self.start, "session_id": SECOND})
        with self.assertRaisesRegex(guest.SmokeFailure, "rewrite"):
            self.journal.refresh()


class TaskSmokeFailureArtifactsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_launch_failure_writes_fresh_failed_process_evidence(self):
        directory = self.root / "failed"
        self.assertEqual(guest.run(directory, command=[str(self.root / "missing-command")]), 1)
        report = json.loads((directory / "task-smoke.json").read_bytes())
        self.assertEqual((report["outcome"], report["termination"]), ("FAIL", "launch-failed"))
        self.assertIsNone(json.loads((directory / "execution.json").read_bytes())["process_exit_code"])
        self.assertEqual(set(report["files"]), {"stdin.log", "stdout.log", "stderr.log"})
        before = (directory / "task-smoke.json").read_bytes()
        with self.assertRaises(FileExistsError):
            guest.run(directory, command=["missing"])
        self.assertEqual((directory / "task-smoke.json").read_bytes(), before)

    def test_early_child_failure_preserves_both_raw_streams_and_exit(self):
        directory = self.root / "early"
        code = "import sys;sys.stdout.buffer.write(b'partial stdout\\n');sys.stderr.buffer.write(b'raw failure\\n');sys.exit(7)"
        self.assertEqual(guest.run(directory, command=[sys.executable, "-c", code]), 1)
        report = json.loads((directory / "task-smoke.json").read_bytes())
        execution = json.loads((directory / "execution.json").read_bytes())
        self.assertEqual((report["outcome"], execution["process_exit_code"]), ("FAIL", 7))
        self.assertEqual((directory / "stdout.log").read_bytes(), b"partial stdout\n")
        self.assertEqual((directory / "stderr.log").read_bytes(), b"raw failure\n")
        self.assertEqual(report["files"]["stderr.log"]["sha256"], guest.digest(b"raw failure\n"))

    def fake_child(self, directory, *, spoof=False):
        return "\n".join([
            "import json,sys,pathlib",
            "p=pathlib.Path(" + repr(str(directory / "session")) + ");p.mkdir()",
            "seq=0",
            "def event(kind,data):",
            " global seq;seq+=1",
            " with (p/'session.events.jsonl').open('ab') as out:",
            "  out.write((json.dumps({'schema_version':10,'session_id':" + repr(FIRST) +
                ",'sequence':seq,'event':kind,'data':data})+'\\n').encode())",
            "def output(raw):sys.stdout.buffer.write(raw);sys.stdout.buffer.flush()",
            "event('START',{'runtime_version':'0.10.0'})",
            "output(b'Synthetic console\\naios> ')",
            "for line in sys.stdin:",
            " name=line.strip()",
            " if name=='exit':",
            "  output(b'AIOS session closed.\\n')",
            "  event('COMMAND',{'name':'exit','args':[],'outcome':'OK','result':{'closing':True}})",
            "  event('STOP',{});break",
            " output(" + repr(b"aios> " if spoof else b"Error: Unknown command. Type help to see AIOS commands.\n") + ")",
            " event('COMMAND',{'name':name,'args':[],'outcome':'ERROR','result':{'error':'unknown_command','message':'Unknown command. Type help to see AIOS commands.'}})",
            " output(b'aios> ')",
        ])

    def test_real_pipe_exchange_joins_sequence_response_and_next_prompt(self):
        directory = self.root / "synthetic-success"
        def scenario(exchange, **kwargs):
            exchange("no-such-command")
            exchange("exit")
        with mock.patch.object(guest, "run_scenario", side_effect=scenario):
            self.assertEqual(guest.run(directory, command=[sys.executable, "-c", self.fake_child(directory)]), 0)
        report = json.loads((directory / "task-smoke.json").read_bytes())
        self.assertEqual([row["sequence"] for row in report["commands"]], [2, 3])
        self.assertEqual([row["completion"] for row in report["commands"]], ["prompt", "exit"])
        self.assertEqual((directory / "stdin.log").read_bytes(), b"no-such-command\nexit\n")
        self.assertEqual(report["termination"], "exit")

    def test_prompt_spoof_even_with_matching_command_event_fails_and_keeps_stdin(self):
        directory = self.root / "synthetic-spoof"
        with mock.patch.object(guest, "run_scenario", side_effect=lambda exchange, **kwargs: exchange("no-such-command")):
            self.assertEqual(guest.run(directory, command=[sys.executable, "-c", self.fake_child(directory, spoof=True)]), 1)
        report = json.loads((directory / "task-smoke.json").read_bytes())
        self.assertIn("command_output_mismatch", report["failure"])
        self.assertEqual((directory / "stdin.log").read_bytes(), b"no-such-command\n")
        self.assertTrue((directory / "session/session.events.jsonl").is_file())

    def test_total_deadline_applies_before_pause_and_command_submission(self):
        console = guest.Console(None, self.root, {"started_monotonic_ns": 0},
                                SimpleNamespace(error=None), SimpleNamespace(error=None))
        with mock.patch.object(guest.time, "monotonic_ns", return_value=guest.LIMITS["total_seconds"] * 1_000_000_000):
            for action in (console.check, lambda: console.pause(30), lambda: console.exchange("backend start")):
                with self.assertRaisesRegex(TimeoutError, "scenario_total_deadline"):
                    action()


if __name__ == "__main__":
    unittest.main()
