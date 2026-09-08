"""Independent artifact mutations; generated boot observations remain fixtures."""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import shutil
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/hosted"))
sys.path.insert(0, str(ROOT / "hosted/linux"))
from aios_hosted.boot import run_boot
from test_hosted_boot import fixture
import verify_service as verifier

# Deliberately independent of the daemon's public fields and producer helpers.
SOURCES = ("aios-service.py", "aios_service/__init__.py", "aios_service/client.py",
           "aios_service/lifecycle.py", "aios-boot.py", "aios_hosted/__init__.py",
           "aios_hosted/boot.py", "aios_hosted/hardware.py")
FILES = ("start.json", "lifecycle.events.jsonl", "latest.json", "observation.json")


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def get(path: Path):
    return json.loads(path.read_bytes())


def put(path: Path, value) -> None:
    path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")


class ServiceVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc, self.sysfs = fixture(self.root / "fixture")
        self.source = self.root / "runtime-source"
        for name in SOURCES:
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "hosted/linux" / name, target)
        self.state = self.root / "state"
        (self.state / "runs").mkdir(parents=True)
        self.service_id = str(uuid.uuid4())
        self.boot_id = str(uuid.uuid4())
        self.run = self.make_run(1)

    def make_run(self, generation: int) -> Path:
        identity = {"service_id": self.service_id, "instance_id": str(uuid.uuid4()),
                    "generation": generation}
        directory = self.state / "runs" / identity["instance_id"]
        directory.mkdir()
        started = datetime.now(timezone.utc)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = run_boot(directory / "boot", proc_root=self.proc, sys_root=self.sysfs,
                            test_system="Linux")
        self.assertEqual(code, 0)
        (directory / "boot.stdout.log").write_bytes(output.getvalue().encode("utf-8"))
        inventory = get(directory / "boot/inventory.json")["inventory"]
        latest = {"schema_version": 1, **identity, "service_kind": "CONSOLE_RUNTIME",
                  "outcome": "OK", "error": None, "state": "STOPPED", "pid": 100 + generation,
                  "observation_sequence": 3, "heartbeat_monotonic_ns": 300,
                  "boot_id": self.boot_id, "source_only": True, "binding_status": "UNBOUND",
                  "management_actions": "UNSUPPORTED"}
        start = {"schema_version": 1, **identity, "service_kind": "CONSOLE_RUNTIME",
                 "pid": latest["pid"], "boot_id": self.boot_id, "source_only": True,
                 "binding_status": "UNBOUND", "management_actions": "UNSUPPORTED",
                 "started_at": started.isoformat(),
                 "source_hashes": {name: sha((self.source / name).read_bytes()) for name in SOURCES}}
        observation = {"schema_version": 1, **identity, "observation_sequence": 3,
                       "heartbeat_monotonic_ns": 300, "cpu": inventory["cpu"],
                       "memory": inventory["memory"], "source_only": True, "observation_only": True}
        events = [{"schema_version": 1, **identity, "sequence": i, "monotonic_ns": clock,
                   "event": name, "observation_sequence": count, "reason": None}
                  for i, (name, clock, count) in enumerate(
                      (("STARTING", 100, 0), ("RUNNING", 200, 1),
                       ("STOPPING", 400, 3), ("STOPPED", 500, 3)), 1)]
        put(directory / "start.json", start)
        put(directory / "latest.json", latest)
        put(directory / "observation.json", observation)
        self.save_events(events, directory)
        put(directory / "result.json", {"schema_version": 1, **identity, "state": "STOPPED",
            "exit_code": 0, "reason": None, "observation_sequence": 3,
            "completed_at": datetime.now(timezone.utc).isoformat(), "files": {}})
        self.rehash(directory)
        put(self.state / "registry.json", {"schema_version": 1, **identity})
        put(self.state / "latest.json", latest)
        return directory

    def save_events(self, events, directory=None):
        directory = directory or self.run
        (directory / "lifecycle.events.jsonl").write_text(
            "".join(json.dumps(row) + "\n" for row in events), encoding="utf-8")

    def events(self):
        return [json.loads(line) for line in (self.run / "lifecycle.events.jsonl").read_bytes().splitlines()]

    def rehash(self, directory=None):
        directory = directory or self.run
        result = get(directory / "result.json")
        result["files"] = {name: sha((directory / name).read_bytes()) for name in FILES}
        put(directory / "result.json", result)
        registry = self.state / "registry.json"
        if registry.exists() and get(registry)["instance_id"] == directory.name:
            put(self.state / "latest.json", get(directory / "latest.json"))

    def verdict(self, **kwargs):
        return verifier.verify_service_runs(self.state, source_root=self.source,
                                            require_live=False, **kwargs)

    def assert_fails(self, **kwargs):
        result = self.verdict(**kwargs)
        self.assertEqual(result["outcome"], "FAIL", result)
        self.assertTrue(result["reasons"], result)
        return result

    def test_fixture_can_replay_but_cannot_prove_live_service(self):
        self.assertEqual(self.verdict()["outcome"], "PASS")
        result = verifier.verify_service_runs(self.state, source_root=self.source, require_live=True)
        self.assertEqual(result["outcome"], "FAIL")
        self.assertIn("fixture_not_live", str(result))

    def test_three_distinct_instances_keep_one_service_and_contiguous_generations(self):
        self.make_run(2)
        self.make_run(3)
        result = self.verdict()
        self.assertEqual(result["outcome"], "PASS", result)
        self.assertEqual([r["generation"] for r in result["runs"]], [1, 2, 3])
        self.assertEqual(len({r["instance_id"] for r in result["runs"]}), 3)

    def test_failed_or_running_terminal_cannot_be_rehashed_into_clean_exit(self):
        original = get(self.run / "result.json")
        for patch in ({"state": "RUNNING"}, {"state": "FAILED", "exit_code": 1},
                      {"exit_code": True}, {"exit_code": 1}, {"reason": "STOP_TIMEOUT"}):
            with self.subTest(patch=patch):
                put(self.run / "result.json", {**original, **patch})
                self.rehash()
                self.assert_fails()

    def test_mixed_identity_and_numeric_bool_are_rejected_after_rehash(self):
        original = get(self.run / "observation.json")
        for patch in ({"instance_id": str(uuid.uuid4())}, {"service_id": str(uuid.uuid4())},
                      {"generation": 2}, {"generation": True}, {"schema_version": True}):
            with self.subTest(patch=patch):
                put(self.run / "observation.json", {**original, **patch})
                self.rehash()
                self.assert_fails()

    def test_boundary_claims_cannot_change_after_rehash(self):
        original = get(self.run / "start.json")
        for patch in ({"service_kind": "AI_SERVICE"}, {"binding_status": "BOUND"},
                      {"source_only": False}, {"management_actions": "SUPPORTED"}):
            with self.subTest(patch=patch):
                put(self.run / "start.json", {**original, **patch})
                self.rehash()
                self.assert_fails()

    def test_invalid_periodic_cpu_or_memory_does_not_inherit_boot_readiness(self):
        original = get(self.run / "observation.json")
        for section, field, value in (("cpu", "logical_count", -1),
                                      ("memory", "available_bytes", 2**50)):
            with self.subTest(section=section):
                observation = copy.deepcopy(original)
                observation[section]["data"][field] = value
                put(self.run / "observation.json", observation)
                self.rehash()
                self.assert_fails()

    def test_reordered_duplicated_and_negative_event_times_fail(self):
        original = self.events()
        changes = (lambda e: e[2].update(event="RUNNING"), lambda e: e[1].update(sequence=True),
                   lambda e: e[1].update(monotonic_ns=-1), lambda e: e[3].update(monotonic_ns=99),
                   lambda e: e.append(copy.deepcopy(e[-1])))
        for change in changes:
            with self.subTest(change=change):
                events = copy.deepcopy(original)
                change(events)
                self.save_events(events)
                self.rehash()
                self.assert_fails()

    def test_clock_and_heartbeat_cannot_cross_lifecycle_limits(self):
        original = get(self.run / "observation.json")
        for heartbeat in (99, 401):
            with self.subTest(heartbeat=heartbeat):
                put(self.run / "observation.json", {**original, "heartbeat_monotonic_ns": heartbeat})
                latest = get(self.run / "latest.json")
                put(self.run / "latest.json", {**latest, "heartbeat_monotonic_ns": heartbeat})
                self.rehash()
                self.assert_fails()

    def test_only_first_observation_must_precede_running(self):
        observation = get(self.run / "observation.json")
        latest = get(self.run / "latest.json")
        result = get(self.run / "result.json")
        events = self.events()
        for value in (observation, latest, result):
            value["observation_sequence"] = 1
        for event in events[2:]:
            event["observation_sequence"] = 1
        put(self.run / "observation.json", observation)
        put(self.run / "latest.json", latest)
        put(self.run / "result.json", result)
        self.save_events(events)
        self.rehash()
        self.assert_fails()  # heartbeat 300 cannot be the initial observation before RUNNING 200.
        for value in (observation, latest):
            value["heartbeat_monotonic_ns"] = 150
        put(self.run / "observation.json", observation)
        put(self.run / "latest.json", latest)
        self.rehash()
        self.assertEqual(self.verdict()["outcome"], "PASS")

    def test_later_observation_cannot_precede_running(self):
        for name in ("observation.json", "latest.json"):
            value = get(self.run / name)
            value["heartbeat_monotonic_ns"] = 150
            put(self.run / name, value)
        self.rehash()
        self.assert_fails()

    def test_observation_counters_and_copies_must_agree(self):
        original = get(self.run / "observation.json")
        for patch in ({"observation_sequence": 2}, {"observation_sequence": True},
                      {"observation_only": False}, {"source_only": False}):
            with self.subTest(patch=patch):
                put(self.run / "observation.json", {**original, **patch})
                self.rehash()
                self.assert_fails()

    def test_service_completion_cannot_precede_start_or_use_local_time(self):
        original = get(self.run / "result.json")
        start = datetime.fromisoformat(get(self.run / "start.json")["started_at"])
        for completed in ((start - timedelta(seconds=1)).isoformat(), "2026-09-04T01:00:00", "not-a-time"):
            with self.subTest(completed=completed):
                put(self.run / "result.json", {**original, "completed_at": completed})
                self.rehash()
                self.assert_fails()

    def test_nested_boot_must_complete_within_its_service_lifetime(self):
        path = self.run / "boot/result.json"
        original = get(path)
        start = datetime.fromisoformat(get(self.run / "start.json")["started_at"])
        end = datetime.fromisoformat(get(self.run / "result.json")["completed_at"])
        for completed in ((start - timedelta(seconds=1)).isoformat(), (end + timedelta(seconds=1)).isoformat()):
            with self.subTest(completed=completed):
                put(path, {**original, "completed_at": completed})
                self.assert_fails()

    def test_missing_evidence_and_abrupt_death_are_not_clean_stops(self):
        for relative in ("observation.json", "lifecycle.events.jsonl", "result.json", "boot/result.json"):
            with self.subTest(relative=relative):
                path = self.run / relative
                content = path.read_bytes()
                path.unlink()
                self.assert_fails()
                path.write_bytes(content)
        latest = get(self.run / "latest.json")
        latest.update(state="RUNNING")
        put(self.run / "latest.json", latest)
        self.rehash()
        self.assert_fails()

    def test_source_hash_and_required_source_set_are_exact(self):
        path = self.run / "start.json"
        original = get(path)
        changes = (lambda v: v["source_hashes"].update({"aios-service.py": "0" * 64}),
                   lambda v: v["source_hashes"].pop("aios_service/client.py"),
                   lambda v: v["source_hashes"].update({"untracked.py": "0" * 64}))
        for change in changes:
            with self.subTest(change=change):
                value = copy.deepcopy(original)
                change(value)
                put(path, value)
                self.rehash()
                self.assert_fails()

    def test_boot_stdout_cannot_have_unaccounted_trailing_output(self):
        with (self.run / "boot.stdout.log").open("ab") as stream:
            stream.write(b"FATAL: unexpected trailing output\n")
        self.assert_fails()

    def test_generation_gap_is_rejected(self):
        self.make_run(3)
        self.assert_fails()

    def test_service_identity_fork_is_rejected(self):
        self.service_id = str(uuid.uuid4())
        self.make_run(2)
        self.assert_fails()

    def test_nested_boot_bundle_cannot_be_reused_by_another_generation(self):
        second = self.make_run(2)
        # Keep enclosing wall times valid to isolate duplicate nested boot identity.
        start = get(second / "start.json")
        start["started_at"] = get(self.run / "start.json")["started_at"]
        put(second / "start.json", start)
        for path in (self.run / "boot").iterdir():
            shutil.copyfile(path, second / "boot" / path.name)
        shutil.copyfile(self.run / "boot.stdout.log", second / "boot.stdout.log")
        self.rehash(second)
        self.assert_fails()

    def test_current_registry_and_snapshot_cannot_point_to_old_instance(self):
        registry = get(self.state / "registry.json")
        latest = get(self.state / "latest.json")
        self.make_run(2)
        put(self.state / "registry.json", registry)
        put(self.state / "latest.json", latest)
        self.assert_fails()

    def test_duplicate_json_key_is_rejected_even_after_rehash(self):
        path = self.run / "observation.json"
        path.write_bytes(path.read_bytes().replace(b'{', b'{"schema_version":1,', 1))
        self.rehash()
        self.assert_fails()

    def test_empty_state_requires_explicit_allow_absent(self):
        directory = self.root / "absent"
        self.assertEqual(verifier.verify_service_runs(directory)["outcome"], "FAIL")
        self.assertEqual(verifier.verify_service_runs(directory, allow_absent=True)["state"], "ABSENT")

    def test_allow_absent_cannot_accept_symlink_to_empty_or_missing_directory(self):
        target = self.root / "empty"
        target.mkdir()
        link = self.root / "linked"
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest("symlink privilege unavailable: " + str(exc))
        self.assertEqual(verifier.verify_service_runs(link, allow_absent=True)["outcome"], "FAIL")
        target.rmdir()
        self.assertEqual(verifier.verify_service_runs(link, allow_absent=True)["outcome"], "FAIL")


class ServiceWorkflowVerifierTests(unittest.TestCase):
    """Exercise cross-console semantics after separately tested prerequisite gates.

    These synthetic records never represent live service or console executions:
    only the two prerequisite verifiers are mocked. Control stdout, hashes and
    process results are still read and checked by the real control verifier.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        service_id, boot_id = str(uuid.uuid4()), str(uuid.uuid4())
        self.runs = []
        for generation, count, heartbeat in ((1, 10, 1000), (2, 3, 2400), (3, 3, 3400)):
            self.runs.append({"schema_version": 1, "service_id": service_id,
                "instance_id": str(uuid.uuid4()), "generation": generation,
                "service_kind": "CONSOLE_RUNTIME", "outcome": "OK", "error": None,
                "state": "STOPPED", "pid": 100 + generation, "observation_sequence": count,
                "heartbeat_monotonic_ns": heartbeat, "boot_id": boot_id, "source_only": True,
                "binding_status": "UNBOUND", "management_actions": "UNSUPPORTED"})

        def running(generation, count, heartbeat):
            return {**self.runs[generation - 1], "state": "RUNNING",
                    "observation_sequence": count, "heartbeat_monotonic_ns": heartbeat}

        self.start = running(1, 1, 100)
        self.first = [running(1, 2, 200)]
        self.second = [running(1, 3, 300), running(2, 1, 2000), running(2, 2, 2200),
                       copy.deepcopy(self.runs[1]), copy.deepcopy(self.runs[1]),
                       running(3, 1, 3000), running(3, 2, 3200)]

    def write_control(self, action, value):
        directory = self.root / ("service-" + action)
        directory.mkdir(exist_ok=True)
        put(directory / "stdout.log", value)
        (directory / "stderr.log").write_bytes(b"")
        put(directory / "execution.json", {"schema_version": 1, "action": action,
            "process_exit_code": 0, "stdout_sha256": sha((directory / "stdout.log").read_bytes()),
            "stderr_sha256": sha(b"")})

    def verify_fixture(self):
        self.write_control("start", self.start)
        self.write_control("stop", self.runs[-1])
        first_commands = ["service status", "about", "resolve example.com", "fetch https://example.com/", "exit"]
        second_commands = ["service status", "service restart", "service status", "service stop",
                           "service status", "service start", "service status", "exit"]
        for directory, commands, values in ((self.root, first_commands, self.first),
                                           (self.root / "second-console", second_commands, self.second)):
            (directory / "session").mkdir(parents=True, exist_ok=True)
            put(directory / "execution.json", {"requested_commands": commands})
            actions = [command.split()[1] for command in commands if command.startswith("service ")]
            rows = [{"event": "COMMAND", "data": {"name": "service", "args": [action],
                     "outcome": "OK", "result": value}} for action, value in zip(actions, values)]
            (directory / "session/session.events.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        with mock.patch.object(verifier, "verify_service_runs", return_value={"outcome": "PASS", "runs": self.runs}), \
             mock.patch.object(verifier, "verify_execution", return_value={"outcome": "PASS"}):
            return verifier.verify_workflow(self.root)

    def test_fixture_workflow_accepts_running_service_across_two_consoles(self):
        result = self.verify_fixture()
        self.assertEqual(result["outcome"], "PASS", result)
        self.assertEqual(result["generations"], [1, 2, 3])
        self.assertTrue(result["console_survival"])

    def test_stopped_second_console_status_cannot_claim_survival_with_newer_heartbeat(self):
        self.second[0]["state"] = "STOPPED"
        # Count 3 / heartbeat 300 exceed CLI1's 2 / 200, but the daemon is stopped.
        result = self.verify_fixture()
        self.assertEqual(result["outcome"], "FAIL", result)
        self.assertIn("workflow_source", str(result))

    def test_start_to_console_regression_is_not_hidden_by_later_progress(self):
        self.start.update(observation_sequence=5, heartbeat_monotonic_ns=500)
        # CLI1 2 -> CLI2 3 progresses locally while both regress from start 5.
        result = self.verify_fixture()
        self.assertEqual(result["outcome"], "FAIL", result)
        self.assertIn("workflow_regressed_observation", str(result))


if __name__ == "__main__":
    unittest.main()
