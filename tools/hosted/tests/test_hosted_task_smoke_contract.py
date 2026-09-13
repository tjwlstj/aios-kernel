"""Independent scenario fixtures; these records claim no live model evidence."""
from __future__ import annotations

import ast
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/hosted"))
import task_smoke_contract as contract

FIRST = "12345678-9abc-4def-8abc-123456789abc"
SECOND = "12345678-9abc-4def-8abc-123456789abd"


def state(request_id, phase, *, answer=False, cancelled=False):
    return {"request_id": request_id, "phase": phase, "model_outcome": "ANSWERED" if answer else "UNKNOWN" if cancelled else None,
            "worker_process_id": 901 if request_id == FIRST else 902,
            "updated_ns": 20 if request_id == FIRST else 40,
            "cancel_requested_ns": 35 if cancelled else None,
            "backend_stop": {"state": "STOPPED"} if cancelled else None}


def fixture_plan():
    rows = []

    def add(line, result=None):
        fields = line.split()
        rows.append({"name": fields[0], "args": fields[1:], "outcome": "OK", "result": result or {}})

    def task(line, request_id, phase, **kwargs):
        add(line, {"request_id": request_id, "task": state(request_id, phase, **kwargs)})

    for line in contract.PREFIX:
        add(line)
    task("ask " + contract.SPACE_QUESTION, FIRST, "ACCEPTED")
    task("task status " + FIRST, FIRST, "FINISHED", answer=True)
    task("task result " + FIRST, FIRST, "FINISHED", answer=True)
    add("space")
    task("ask " + contract.CANCEL_QUESTION, SECOND, "ACCEPTED")
    task("task status " + SECOND, SECOND, "RUNNING")
    rows[-1]["result"]["task"]["updated_ns"] = 30
    task("task cancel " + SECOND, SECOND, "FINISHED", cancelled=True)
    rows[-1]["result"]["task_control"] = {"cancel_outcome": "ACCEPTED",
        "backend_stop_attempt": {"outcome": "OK", "state": "STOPPED"}}
    task("task status " + SECOND, SECOND, "FINISHED", cancelled=True)
    task("task result " + SECOND, SECOND, "FINISHED", cancelled=True)
    for line in contract.SUFFIX:
        result = {"state": "STOPPED"} if line in ("backend status", "agent stop", "backend stop") else {}
        if line == "agent status":
            result = {"state": "RUNNING", "source_record": {"model_ready": False},
                      "management_snapshot": {"binding_current": False}}
        add(line, result)
    return rows


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def write_driver(directory, commands):
    source = ROOT / "tools/hosted/task_smoke_guest.py"
    (directory / "verification-source").mkdir()
    (directory / "verification-source/task_smoke_guest.py").write_bytes(source.read_bytes())
    (directory / "session").mkdir()
    stdout, stdin = bytearray(b"synthetic console\naios> "), bytearray()
    report = {"schema_version": 1, "scenario": contract.SCENARIO, "capture_kind": "live", "source_only": True,
        "driver_source_sha256": contract.digest(source.read_bytes()), "cli_process_id": 303,
        "started_monotonic_ns": 1, "finished_monotonic_ns": 1000, "outcome": "PASS", "failure": None,
        "termination": "exit", "timed_out": False, "limits": dict(contract.LIMITS), "commands": [], "files": {},
        "initial_prompt": {"event_sequence": 1, "observed_monotonic_ns": 2, "stdout_end": len(stdout)}}
    events = [{"schema_version": 10, "sequence": 1, "session_id": FIRST, "event": "START",
               "data": {"source_process": {"process_id": 303}}}]
    for index, command in enumerate(commands, 1):
        line = contract.command_text(command)
        event = {"schema_version": 10, "sequence": index + 1, "session_id": FIRST,
                 "event": "COMMAND", "data": command}
        events.append(event)
        item = {"sequence": index + 1, "command": line, "request_id": command["result"].get("request_id"),
            "sent_monotonic_ns": index * 10, "event_observed_monotonic_ns": index * 10 + 1,
            "completed_monotonic_ns": index * 10 + 2, "stdin_start": len(stdin), "stdout_start": len(stdout),
            "event_sha256": contract.digest(encoded(event)), "completion": "exit" if line == "exit" else "prompt"}
        stdin.extend((line + "\n").encode())
        stdout.extend(b"synthetic output\n" + (b"" if line == "exit" else b"aios> "))
        item.update(stdin_end=len(stdin), stdout_end=len(stdout))
        report["commands"].append(item)
    events.append({"schema_version": 10, "sequence": len(events) + 1, "session_id": FIRST, "event": "STOP", "data": {}})
    (directory / "session/session.events.jsonl").write_bytes(b"".join(map(encoded, events)))
    for name, raw in (("stdin.log", stdin), ("stdout.log", stdout), ("stderr.log", b"")):
        (directory / name).write_bytes(raw)
        report["files"][name] = {"sha256": contract.digest(raw), "bytes": len(raw)}
    (directory / "task-smoke.json").write_bytes(encoded(report))
    (directory / "execution.json").write_bytes(encoded({"mode": "smoke", "process_exit_code": 0,
        "requested_commands": [contract.command_text(row) for row in commands]}))
    return report


class TaskSmokePlanTests(unittest.TestCase):
    def test_complete_two_request_plan(self):
        result = contract.verify_plan(fixture_plan())
        self.assertEqual((result["first"], result["second"]), (FIRST, SECOND))

    def test_all_truncations_are_rejected_as_value_errors(self):
        rows = fixture_plan()
        for count in range(len(rows)):
            with self.subTest(count=count), self.assertRaises(ValueError):
                contract.verify_plan(rows[:count])
        for malformed in (None, {}, [None] * 22, [{"name": "ask"}] * 22):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                contract.verify_plan(malformed)

    def test_extra_question_or_duplicate_cancel_cannot_manufacture_pass(self):
        for index in (9, 11):
            rows = fixture_plan()
            rows.insert(index, copy.deepcopy(rows[index]))
            with self.subTest(index=index), self.assertRaises(ValueError):
                contract.verify_plan(rows)

    def test_finished_instead_of_running_and_cancelled_answer_are_rejected(self):
        for index in (10, 12):
            rows = fixture_plan()
            rows[index]["result"]["task"].update(phase="FINISHED", model_outcome="ANSWERED")
            with self.subTest(index=index), self.assertRaises(ValueError):
                contract.verify_plan(rows)

    def test_missing_stop_or_still_ready_main_are_rejected(self):
        for mutate in (lambda rows: rows[12]["result"]["task"].update(backend_stop=None),
                       lambda rows: rows[15]["result"]["source_record"].update(model_ready=True),
                       lambda rows: rows[11]["result"]["task_control"].update(cancel_outcome="ALREADY_FINISHED")):
            rows = fixture_plan()
            mutate(rows)
            with self.assertRaises(ValueError):
                contract.verify_plan(rows)


class TaskSmokeDriverContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.commands = fixture_plan()
        self.report = write_driver(self.root, self.commands)

    def save(self):
        (self.root / "task-smoke.json").write_bytes(encoded(self.report))

    def test_exact_raw_command_and_prompt_joins(self):
        self.assertEqual(contract.verify_driver(self.root, self.commands)["cli_process_id"], 303)

    def test_uuid_sequence_hash_offset_clock_and_stale_summary_mutations_fail(self):
        original = copy.deepcopy(self.report)
        mutations = (lambda r: r["commands"][6].update(request_id=SECOND),
                     lambda r: r["commands"][6].update(sequence=99),
                     lambda r: r["commands"][6].update(event_sha256="0" * 64),
                     lambda r: r["commands"][6].update(stdout_start=0),
                     lambda r: r["commands"][6].update(completed_monotonic_ns=1),
                     lambda r: r.update(outcome="FAIL"),
                     lambda r: r.update(termination="driver-killed"),
                     lambda r: r.update(driver_source_sha256="0" * 64))
        for mutate in mutations:
            self.report = copy.deepcopy(original)
            mutate(self.report)
            self.save()
            with self.assertRaises(ValueError):
                contract.verify_driver(self.root, self.commands)

    def test_prompt_only_without_command_record_is_rejected(self):
        path = self.root / "session/session.events.jsonl"
        lines = path.read_bytes().splitlines(keepends=True)
        path.write_bytes(b"".join(lines[:7] + lines[8:]))
        with self.assertRaisesRegex(ValueError, "event_order"):
            contract.verify_driver(self.root, self.commands)

    def test_trailing_bytes_and_duplicate_json_keys_are_rejected(self):
        with (self.root / "stdout.log").open("ab") as stream:
            stream.write(b"FATAL trailing evidence\n")
        with self.assertRaisesRegex(ValueError, "driver_file"):
            contract.verify_driver(self.root, self.commands)
        self.save()
        path = self.root / "task-smoke.json"
        path.write_bytes(path.read_bytes().replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1'))
        with self.assertRaises(ValueError):
            contract.verify_driver(self.root, self.commands)

    def test_tools_have_no_runtime_imports(self):
        for name in ("task_smoke_contract.py", "task_smoke_guest.py"):
            tree = ast.parse((ROOT / "tools/hosted" / name).read_text(encoding="utf-8"))
            imports = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
            self.assertFalse(any(value and value.startswith(("aios_agent", "aios_backend", "aios_console")) for value in imports))


if __name__ == "__main__":
    unittest.main()
