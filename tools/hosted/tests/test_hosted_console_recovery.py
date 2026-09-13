"""Recovery output and owner evidence keep the console version boundary explicit."""
from __future__ import annotations

import copy
import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/hosted"))
sys.path.insert(0, str(ROOT / "hosted/linux"))

import verify_console as verifier
from backend_output_contract import backend_result
from test_hosted_backend_recovery_verifier import sample
from test_hosted_console import backend, fixture
import test_hosted_console_verifier as console_fixtures


class ConsoleRecoveryTests(unittest.TestCase):
    # Reuse fixture writers without inheriting unrelated test methods.
    run_session = console_fixtures.ConsoleVerifierTests.run_session
    events = console_fixtures.ConsoleVerifierTests.events
    rehash = console_fixtures.ConsoleVerifierTests.rehash
    verify_session = console_fixtures.ConsoleVerifierTests.verify_session

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.proc, self.sysfs = fixture(self.root)
        self.output = io.StringIO()
        self.serial = 0

    def make_session(self, commands, replies=None):
        self.serial += 1
        self.session = self.root / ("session-" + str(self.serial))
        options = {} if replies is None else {"backend_control": mock.Mock(side_effect=replies)}
        self.run_session(self.session, commands, **options)
        verdict = self.verify_session(self.session, expected_commands=commands)
        self.assertEqual(verdict["outcome"], "PASS", verdict)
        return self.events()

    def assert_rejected(self, reason):
        verdict = self.verify_session(self.session)
        self.assertEqual(verdict["outcome"], "FAIL", verdict)
        self.assertIn(reason, str(verdict["reasons"]))
        self.assertNotIn("artifact_hash:", str(verdict["reasons"]))

    def relabel_v06(self):
        """Construct historical schema6 input with its own literal source pin.

        It is a synthetic format fixture, not an executed historical capture.
        Subsequent rehash calls never repair mutated semantics.
        """
        sources = console_fixtures.rewrite_historical_session(self.session, 6)
        if not hasattr(self, 'history_sources'):
            self.history_sources = {}
        self.history_sources[self.session] = sources

    def claim_live_owner(self):
        """Build synthetic claimed-live inputs for raw-sample parser tests only.

        This does not run a live process or exercise the execution/lease verifier.
        All nested boot and session hashes are rebuilt before semantic checks.
        """
        boot = self.session / "boot"
        boot_events = [json.loads(line) for line in (boot / "events.jsonl").read_bytes().splitlines()]
        boot_events[0]["data"]["capture_kind"] = "live"
        encoded = [json.dumps(event, sort_keys=True, separators=(",", ":")) for event in boot_events]
        (boot / "events.jsonl").write_bytes("".join(line + "\n" for line in encoded).encode())
        (boot / "boot.log").write_bytes("".join("[AIOS-BOOT] " + line + "\n" for line in encoded).encode())
        result = json.loads((boot / "result.json").read_bytes())
        result["files"] = {name: hashlib.sha256((boot / name).read_bytes()).hexdigest()
                           for name in result["files"]}
        (boot / "result.json").write_text(json.dumps(result), encoding="utf-8")
        events = self.events()
        owner = {"host_boot_id": "00000000-0000-4000-8000-000000000031",
                 "process_id": 101, "process_start_ticks": 50, "uid": 1000}
        events[0]["data"].update(capture_kind="live", source_process=sample(owner, 1, 1400))
        self.rehash(events, lambda result: result.update(capture_kind="live"))
        verdict = self.verify_session(self.session, require_live=True)
        self.assertEqual(verdict["outcome"], "PASS", verdict)
        return self.events()

    def test_recovered_and_refused_recovery_render_readiness_separately(self):
        replies = [backend("recover", state="RECOVERED"), backend(state="RECOVERED"),
                   backend("recover", state="RUNNING", error="supervisor-running"),
                   backend("recover", state="STALE", error="recovery-owner-required")]
        events = self.make_session(["backend recover", "backend status", "backend recover",
                                    "backend recover", "help", "exit"], replies)
        self.assertEqual(events[0]["schema_version"], 10)
        self.assertIsNone(events[0]["data"]["source_process"])
        output = (self.session / "console.log").read_text(encoding="utf-8")
        self.assertEqual(output.count("AIOS model backend: RECOVERED; backend not ready\n"), 2)
        self.assertIn("Backend recover failed: supervisor-running.\n", output)
        self.assertIn("Backend recover failed: recovery-owner-required.\n", output)
        self.assertIn("backend status|start|stop|restart|recover", output)
        self.assertEqual(self.verify_session(self.session, require_live=True)["outcome"], "FAIL")

    def test_recovered_cannot_render_as_ready_even_with_rehashed_transcript(self):
        self.make_session(["backend recover", "exit"], [backend("recover", state="RECOVERED")])
        console = self.session / "console.log"
        console.write_bytes(console.read_bytes().replace(b"RECOVERED; backend not ready", b"RECOVERED; backend ready"))
        self.rehash()
        self.assert_rejected("console_output:transcript_mismatch")

    def test_schema_one_through_six_cannot_promote_recover_or_recovered(self):
        for schema in range(1, 7):
            for action in ("recover", "status"):
                with self.subTest(schema=schema, action=action):
                    command = {"name": "backend", "args": [action], "outcome": "OK",
                               "result": backend(action, state="RECOVERED")}
                    reason = "recovery_schema" if schema == 6 and action == "status" else "command_arguments"
                    with self.assertRaisesRegex(ValueError, "backend_contract:" + reason):
                        backend_result(command, schema)

    def test_rehashed_v06_sessions_reject_new_recovery_action_and_state(self):
        for action, reason in (("recover", "command_arguments"), ("status", "recovery_schema")):
            with self.subTest(action=action):
                self.make_session(["backend status", "exit"], [backend("status", state="RECOVERED")])
                self.relabel_v06()
                if action == 'recover':
                    events = self.events()
                    events[1]['data']['args'] = ['recover']
                    events[1]['data']['result']['action'] = 'recover'
                    self.rehash(events)
                self.assert_rejected("backend_contract:" + reason)

    def test_historical_v06_help_about_and_ordinary_backend_still_replay(self):
        commands = ["help", "about", "backend status", "backend start", "backend stop", "exit"]
        self.make_session(commands, [backend(state="ABSENT"), backend("start"), backend("stop", state="STOPPED")])
        self.relabel_v06()
        verdict = self.verify_session(self.session, expected_commands=commands)
        self.assertEqual(verdict["outcome"], "PASS", verdict)
        output = (self.session / "console.log").read_text(encoding="utf-8")
        self.assertIn("AIOS Console 0.6.0", output)
        self.assertIn("backend status|start|stop|restart\n", output)
        self.assertNotIn("|recover", output)
        result = json.loads((self.session / "session-result.json").read_bytes())
        self.assertEqual(result["capture_kind"], "fixture")
        self.assertEqual(set(result["source_hashes"]), set(verifier.MANAGED_SOURCES))
        self.assertNotIn("source_process", self.events()[0]["data"])

    def test_fixture_session_cannot_carry_a_process_owner_sample(self):
        events = self.make_session(["exit"])
        events[0]["data"]["source_process"] = sample(
            {"host_boot_id": "00000000-0000-4000-8000-000000000031",
             "process_id": 101, "process_start_ticks": 50, "uid": 1000}, 1, 1400)
        self.rehash(events)
        self.assert_rejected("fixture_source_process")

    def test_live_raw_owner_malformed_fields_fail_after_all_hashes_are_rebuilt(self):
        self.make_session(["help", "about", "exit"])
        baseline = self.claim_live_owner()
        mutations = [
            (lambda value: value.update(raw_stat="malformed\n"), "stat_format"),
            (lambda value: value.update(raw_stat=value["raw_stat"].replace("101 (", "999 (", 1)), "stat_pid"),
            (lambda value: value.update(process_start_ticks=51), "stat_start"),
            (lambda value: value.update(raw_status=value["raw_status"] + "Pid: 101\n"), "status_duplicate"),
            (lambda value: value.update(raw_status=value["raw_status"].replace("Uid: 1000 1000 1000 1000", "Uid: 0 0 0 0")), "status_identity"),
            (lambda value: value.update(cpu_total_ns=150000001), "raw_accounting"),
            (lambda value: value.update(uid=True), "integer"),
            (lambda value: value.update(read_end_ns=1), "sample_time"),
        ]
        for mutate, reason in mutations:
            with self.subTest(reason=reason):
                events = copy.deepcopy(baseline)
                mutate(events[0]["data"]["source_process"])
                self.rehash(events)
                self.assert_rejected("resource_contract:" + reason)
        events = copy.deepcopy(baseline)
        events[0]["data"]["source_process"] = None
        self.rehash(events)
        self.assert_rejected("resource_contract:keys")


if __name__ == "__main__":
    unittest.main()
