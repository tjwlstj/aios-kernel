from __future__ import annotations

import hashlib
import copy
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/hosted"))
sys.path.insert(0, str(ROOT / "hosted/linux"))
from aios_console import shell
import verify_console as verifier
from qemu_console import PROMPT_PATTERN, SMOKE_COMMANDS
from test_hosted_console import dns, fetch, fixture, service, agent, agent_source, resource_error, resource_display, cell, backend
import newagent_output_contract as agent_contract
import backend_output_contract as backend_contract


class ConsoleVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.proc, self.sysfs = fixture(self.root)
        self.session = self.root / "execution/session"
        self.outer = self.session.parent
        self.output = io.StringIO()
        self.commands = SMOKE_COMMANDS
        self.run_session(self.session, self.commands)
        self.execution = {"schema_version": 1, "mode": "smoke", "process_exit_code": 0,
                          "stdout_sha256": "", "stderr_sha256": "", "requested_commands": self.commands}
        (self.outer / "stdout.log").write_bytes((self.session / "console.log").read_bytes())
        (self.outer / "stderr.log").write_bytes(b"")
        self.save_execution()

    def run_session(self, destination, commands, **kwargs):
        params = {"input_stream": io.StringIO("\n".join(commands) + ("\n" if commands else "")),
                  "output_stream": self.output, "proc_root": self.proc, "sys_root": self.sysfs,
                  "test_system": "Linux", "resolver": dns, "fetcher": fetch}
        params.update(kwargs)
        self.assertEqual(shell.run_console(destination, **params), 0)

    def save_execution(self):
        for name in ("stdout", "stderr"):
            self.execution[name + "_sha256"] = hashlib.sha256((self.outer / (name + ".log")).read_bytes()).hexdigest()
        (self.outer / "execution.json").write_text(json.dumps(self.execution), encoding="utf-8")

    def events(self):
        return [json.loads(line) for line in (self.session / "session.events.jsonl").read_bytes().splitlines()]

    def rehash(self, events=None, result_change=None):
        if events is not None:
            (self.session / "session.events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events), encoding="utf-8")
        result_path = self.session / "session-result.json"
        result = json.loads(result_path.read_bytes())
        for name in result["files"]:
            result["files"][name] = hashlib.sha256((self.session / name).read_bytes()).hexdigest()
        if result_change:
            result_change(result)
        result_path.write_text(json.dumps(result), encoding="utf-8")

    def test_fixture_full_execution_passes_but_never_live(self):
        self.assertEqual(verifier.verify_execution(self.outer, require_internet=True)["outcome"], "PASS")
        self.assertEqual(verifier.verify_execution(self.outer, require_live=True)["outcome"], "FAIL")

    def test_expected_identity_and_process_exit(self):
        identity = json.loads((self.session / "session-result.json").read_bytes())["session_id"]
        self.assertEqual(verifier.verify_execution(self.outer, expected_session_id=identity)["outcome"], "PASS")
        self.assertEqual(verifier.verify_execution(self.outer, expected_session_id="stale")["outcome"], "FAIL")
        self.execution["process_exit_code"] = 1
        self.save_execution()
        self.assertEqual(verifier.verify_execution(self.outer)["outcome"], "FAIL")

    def test_extra_output_after_stop_rejected_even_after_all_hashes_updated(self):
        text = (self.session / "console.log").read_bytes() + b"UNEXPECTED TRAILING OUTPUT AFTER STOP\n"
        (self.session / "console.log").write_bytes(text)
        (self.outer / "stdout.log").write_bytes(text)
        self.rehash()
        self.save_execution()
        verdict = verifier.verify_execution(self.outer)
        self.assertEqual(verdict["outcome"], "FAIL")
        self.assertIn("transcript_mismatch", str(verdict))

    def test_output_status_cannot_claim_another_state_after_rehash(self):
        path = self.session / "console.log"
        path.write_bytes(path.read_bytes().replace(b"startup: READY", b"startup: FAILED"))
        self.rehash()
        self.assertEqual(verifier.verify_session(self.session)["outcome"], "FAIL")

    def test_stdout_stderr_and_script_mismatch(self):
        (self.outer / "stderr.log").write_bytes(b"unexpected failure")
        self.save_execution()
        self.assertEqual(verifier.verify_execution(self.outer)["outcome"], "FAIL")
        (self.outer / "stderr.log").write_bytes(b"")
        (self.outer / "stdout.log").write_bytes(b"not the console output")
        self.save_execution()
        self.assertEqual(verifier.verify_execution(self.outer)["outcome"], "FAIL")
        (self.outer / "stdout.log").write_bytes((self.session / "console.log").read_bytes())
        self.execution["requested_commands"] = ["exit"]
        self.save_execution()
        self.assertEqual(verifier.verify_execution(self.outer)["outcome"], "FAIL")

    def test_source_hash_mismatch(self):
        self.rehash(result_change=lambda r: r["source_hashes"].update({"aios-console.py": "0" * 64}))
        self.assertEqual(verifier.verify_session(self.session)["outcome"], "FAIL")

    def test_event_order_and_negative_time_are_rejected(self):
        original = self.events()
        for mutate in (lambda e: e[0].update(elapsed_ns=-1),
                       lambda e: e[-1].update(event="COMMAND"),
                       lambda e: e[1].update(sequence=True),
                       lambda e: e[1].update(session_id="stale")):
            with self.subTest(mutate=mutate):
                events = json.loads(json.dumps(original))
                mutate(events)
                self.rehash(events)
                self.assertEqual(verifier.verify_session(self.session)["outcome"], "FAIL")

    def test_boundary_hardware_and_command_result_mutations(self):
        original = self.events()
        changes = ((0, lambda d: d.update(binding_status="BOUND")),
                   (1, lambda d: d["result"].update(management_actions="SUPPORTED")),
                   (2, lambda d: d["result"].update(commands_completed=True)),
                   (3, lambda d: d["result"]["sections"]["cpu"]["data"].update(logical_count=999)))
        for index, mutate in changes:
            with self.subTest(index=index):
                events = json.loads(json.dumps(original))
                mutate(events[index]["data"])
                self.rehash(events)
                self.assertEqual(verifier.verify_session(self.session)["outcome"], "FAIL")

    def test_network_false_success_rejected_after_rehash(self):
        original = self.events()
        for name, change in (("resolve", {"addresses": []}), ("fetch", {"tls_verified": False}),
                             ("fetch", {"status": True}), ("fetch", {"received_bytes": 16385}),
                             ("fetch", {"body_preview": "\x1b[2J"}), ("fetch", {"body_preview": "가" * 1000}),
                             ("fetch", {"truncated": True}), ("fetch", {"error": "invented"})):
            with self.subTest(change=change):
                events = json.loads(json.dumps(original))
                event = next(e for e in events if e["event"] == "COMMAND" and e["data"]["name"] == name)
                event["data"]["result"].update(change)
                self.rehash(events)
                self.assertEqual(verifier.verify_session(self.session)["outcome"], "FAIL")

    def test_dns_normalization_and_numeric_input_is_not_dns_evidence(self):
        for host in ("EXAMPLE.com", "bücher.de", "2001:0db8:0:0:0:0:0:1"):
            with self.subTest(host=host):
                command = {"name": "resolve", "args": [host], "outcome": "OK", "result": dns(host)}
                command["result"]["host"] = verifier.normalized_host(host)
                proof, _ = verifier.network_result(command)
                self.assertEqual(proof, ":" not in host)

    def test_invalid_network_arguments_cannot_be_ok(self):
        for name in ("resolve", "fetch"):
            with self.assertRaises(ValueError):
                verifier.network_result({"name": name, "args": [], "outcome": "OK", "result": {}})

    def test_no_network_is_clean_but_not_internet_acceptance(self):
        other = self.root / "no-network"
        self.run_session(other, ["help", "exit"])
        self.assertEqual(verifier.verify_session(other)["outcome"], "PASS")
        self.assertEqual(verifier.verify_session(other, require_internet=True)["outcome"], "FAIL")

    def test_eof_and_non_tty_clear_are_valid(self):
        other = self.root / "eof"
        self.run_session(other, ["clear", "status"])
        self.assertEqual(verifier.verify_session(other)["outcome"], "PASS")

    def test_remote_prompt_and_failure_words_are_content(self):
        other = self.root / "remote-text"
        def response(url):
            return {**fetch(url), "body_preview": "aios> FATAL FAIL are remote document words"}
        self.run_session(other, ["fetch https://example.com/", "exit"], fetcher=response)
        self.assertEqual(verifier.verify_session(other)["outcome"], "PASS")
        line = b"  aios> remote text\n\naios> "
        matches = list(re.finditer(PROMPT_PATTERN, line))
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].end(), len(line))

    def test_real_tty_clear_contract_and_prompt_boundary(self):
        class Tty(io.StringIO):
            def isatty(self): return True
        other = self.root / "clear"
        self.run_session(other, ["clear", "exit"], output_stream=Tty())
        self.assertEqual(verifier.verify_session(other)["outcome"], "PASS")
        self.assertIsNotNone(re.search(PROMPT_PATTERN, b"\x1b[2J\x1b[H\naios> "))

    def test_duplicate_json_and_nonfinite_values_fail(self):
        path = self.session / "session-result.json"
        raw = path.read_text(encoding="utf-8")
        path.write_text(raw.replace('"schema_version":7', '"schema_version":7,"schema_version":7'), encoding="utf-8")
        self.assertEqual(verifier.verify_session(self.session)["outcome"], "FAIL")
        path.write_text(raw.replace('"exit_code":0', '"exit_code":NaN'), encoding="utf-8")
        self.assertEqual(verifier.verify_session(self.session)["outcome"], "FAIL")

    def test_many_invalid_arguments_and_uppercase_command_match_actual_parser(self):
        other = self.root / "many-arguments"
        commands = ["HELP " + "a " * 300, "ABOUT", "exit"]
        self.run_session(other, commands)
        self.assertEqual(verifier.verify_session(other, expected_commands=commands)["outcome"], "PASS")

    def test_service_session_exact_output_errors_and_source_contract(self):
        other = self.root / "service-session"
        responses = [service("ABSENT"), service(), service(error="ALREADY_RUNNING"),
                     service("STOPPED"), service(generation=2), service()]
        commands = ["service status", "service start", "service start", "service stop",
                    "service restart", "service status", "service invalid", "service", "exit"]
        self.run_session(other, commands, service_control=mock.Mock(side_effect=responses))
        verdict = verifier.verify_session(other, expected_commands=commands)
        self.assertEqual(verdict["outcome"], "PASS", verdict)
        self.assertEqual(verifier.verify_session(other, require_live=True)["outcome"], "FAIL")
        result = json.loads((other / "session-result.json").read_bytes())
        self.assertEqual(set(result["source_hashes"]), set(verifier.MANAGED_SOURCES))
        self.assertEqual(len(result["source_hashes"]), 31)
        self.assertEqual(result["schema_version"], 7)

    def test_service_corrupt_claims_fail_after_artifact_hashes_recomputed(self):
        changes = [
            {"binding_status": "BOUND"}, {"service_kind": "AI_SERVICE"}, {"source_only": False},
            {"management_actions": "SUPPORTED"}, {"generation": True}, {"generation": 0},
            {"generation": 2**63}, {"pid": True}, {"pid": None}, {"boot_id": None},
            {"instance_id": "not-a-uuid"}, {"service_id": None}, {"observation_sequence": 0},
            {"observation_sequence": True}, {"heartbeat_monotonic_ns": 0},
            {"heartbeat_monotonic_ns": None}, {"schema_version": True}, {"schema_version": 2},
            {"state": "CANONICAL"}, {"state": "ABSENT"}, {"state": "STOPPING"},
            {"error": "INVENTED"}, {"outcome": "ERROR"}, {"extra": "unsupported evidence"},
        ]
        for index, change in enumerate(changes):
            with self.subTest(change=change):
                other = self.root / f"bad-service-{index}"
                self.run_session(other, ["service status", "exit"],
                                 service_control=lambda *_: {**service(), **change})
                # The producer writes hashes from these altered objects. A
                # hash-only verifier would accept the false semantic claim.
                self.assertEqual(verifier.verify_session(other)["outcome"], "FAIL")

    def test_service_transient_and_terminal_observations_remain_distinct(self):
        accepted = [
            ("status", service("ABSENT")),
            ("stop", service("ABSENT")),
            ("status", service("STOPPED")),
            ("status", service("FAILED", error="OBSERVATION_FAILED")),
            ("status", service("STARTING", error="START_IN_PROGRESS", observations=0)),
            ("status", service("STALE", error="PROCESS_NOT_RUNNING", observations=0)),
            ("stop", service("STOPPING", error="STOP_TIMEOUT")),
            ("status", service("UNSUPPORTED", error="UNSUPPORTED_PLATFORM")),
        ]
        for action, result in accepted:
            if result["observation_sequence"] == 0:
                result["heartbeat_monotonic_ns"] = None
            if result["state"] in ("STARTING", "STALE"):
                result.update(pid=None, boot_id=None)
            with self.subTest(state=result["state"], action=action):
                verifier.service_result({"name": "service", "args": [action],
                                         "outcome": result["outcome"], "result": result}, 2)
        for action, result in [("start", service("ABSENT")), ("stop", service()),
                               ("restart", service("STOPPED")),
                               ("status", service("FAILED")), ("status", service("STALE")),
                               ("status", service("UNSUPPORTED", error="STATE_IO"))]:
            with self.subTest(rejected=(action, result["state"])):
                with self.assertRaises(ValueError):
                    verifier.service_result({"name": "service", "args": [action],
                                             "outcome": result["outcome"], "result": result}, 2)

    def test_service_transcript_tamper_fails_after_hashes_recomputed(self):
        self.session = self.root / "tampered-service"
        self.run_session(self.session, ["service status", "exit"], service_control=lambda *_: service())
        transcript = self.session / "console.log"
        transcript.write_bytes(transcript.read_bytes().replace(b"generation 1;", b"generation 2;"))
        self.rehash()
        verdict = verifier.verify_session(self.session)
        self.assertEqual(verdict["outcome"], "FAIL")
        self.assertIn("transcript_mismatch", str(verdict))

    def test_schema_version_cannot_be_relabeled_without_its_contract(self):
        self.rehash(result_change=lambda result: result.update(schema_version=1))
        verdict = verifier.verify_session(self.session)
        self.assertEqual(verdict["outcome"], "FAIL")
        self.assertIn("sources", str(verdict))

    def test_main_commands_full_output_and_receipt_match_and_never_live_fixture(self):
        other = self.root / "main-session"
        responses = [agent("status", state="ABSENT"), agent("start"), agent("room-discover"),
                     agent("room-bind", bound=True), agent("room-status", bound=True),
                     agent("ask", bound=True, prompt="Describe AIOS"), agent("restart"),
                     agent("room-reconcile", bound=True), agent("stop", state="STOPPED")]
        commands = ["agent status", "agent start", "room discover", "room bind", "room status",
                    "ask Describe AIOS", "agent restart", "room reconcile", "agent stop", "room invalid", "ask", "help", "about", "exit"]
        self.run_session(other, commands, agent_control=mock.Mock(side_effect=responses))
        verdict = verifier.verify_session(other, expected_commands=commands)
        self.assertEqual(verdict["outcome"], "PASS", verdict)
        self.assertEqual(verifier.verify_session(other, require_live=True)["outcome"], "FAIL")

    def test_main_errors_keep_clean_session_and_no_receipt_claim(self):
        other = self.root / "main-errors"
        responses = [agent("ask", error="unbound"), agent("start", state="UNSUPPORTED", error="unsupported-platform")]
        commands = ["ask hello", "agent start", "status", "exit"]
        self.run_session(other, commands, agent_control=mock.Mock(side_effect=responses))
        verdict = verifier.verify_session(other, expected_commands=commands)
        self.assertEqual(verdict["outcome"], "PASS", verdict)

    def test_main_receipt_forgery_rejected_even_with_recomputed_artifact_hashes(self):
        self.session = self.root / "receipt-forgery"
        self.run_session(self.session, ["ask hello", "exit"],
                         agent_control=mock.Mock(return_value=agent("ask", bound=True, prompt="hello")))
        baseline = self.events()
        mutations = [
            lambda r: r.update(request_sha256="0" * 64),
            lambda r: r.update(response_sha256="0" * 64),
            lambda r: r.update(model_sha256="f" * 64),
            lambda r: r.update(provenance_sha256="not-a-hash"),
            lambda r: r.update(purpose="warmup"),
            lambda r: r.update(tokens_predicted=True),
            lambda r: r.update(tokens_predicted=65),
            lambda r: r.update(elapsed_ns=-1),
            lambda r: r.update(content="another result"),
            lambda r: r.update(authority_instance="00000000-0000-4000-8000-000000000019"),
            lambda r: r.update(binding_generation=2),
            lambda r: r["source_after"].update(completed_requests=1),
            lambda r: r["source_before"].update(model_ready=False),
            lambda r: r["source_after"].update(source_generation=2),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                events = copy.deepcopy(baseline)
                mutate(events[1]["data"]["result"]["inference_receipt"])
                self.rehash(events)
                self.assertEqual(verifier.verify_session(self.session)["outcome"], "FAIL")
        for raw_name, hash_name, changed in (
                ("request_body", "request_sha256", lambda raw: raw.replace("hello", "other")),
                ("response_body", "response_sha256", lambda raw: raw.replace('"model":"fixture-main"', '"model":"wrong-model"')),
                ("response_body", "response_sha256", lambda raw: raw.replace('"tokens_predicted":4', '"tokens_predicted":4,"tokens_predicted":4'))):
            events = copy.deepcopy(baseline)
            value = events[1]["data"]["result"]["inference_receipt"]
            value[raw_name] = changed(value[raw_name])
            value[hash_name] = hashlib.sha256(value[raw_name].encode()).hexdigest()
            self.rehash(events)
            self.assertEqual(verifier.verify_session(self.session)["outcome"], "FAIL")

    def test_main_source_and_management_corruption_rejected(self):
        valid = agent("ask", bound=True, prompt="hello")
        changes = [
            lambda v: v.update(service_kind="CONSOLE_RUNTIME"),
            lambda v: v.update(capture_kind="unknown"),
            lambda v: v.update(resource_actions="SUPPORTED"),
            lambda v: v.update(action="status"),
            lambda v: v["source_record"].update(source_namespace="native-slm-agent-tree"),
            lambda v: v["source_record"].update(source_generation=True),
            lambda v: v["source_record"].update(source_generation=0),
            lambda v: v["source_record"].update(source_only=False),
            lambda v: v["source_record"].update(source_instance="00000000-0000-0000-0000-000000000000"),
            lambda v: v["management_snapshot"].update(authority_namespace="native"),
            lambda v: v["management_snapshot"].update(bound_nodes=True),
            lambda v: v["management_snapshot"].update(binding_current=False),
            lambda v: v["management_snapshot"]["parent"].update(id=2),
            lambda v: v["management_snapshot"]["canonical"].update(parent_cell_id=2),
            lambda v: v["management_snapshot"]["nodebits"][1].update(**{"class": "state"}),
            lambda v: v["management_snapshot"]["nodebits"][1].update(parent_node_generation=2),
            lambda v: v["management_snapshot"]["binding"].update(generation=0),
            lambda v: v["management_snapshot"]["retired_instances"].append(v["source_record"]["source_instance"]),
        ]
        for mutation in changes:
            changed = copy.deepcopy(valid)
            mutation(changed)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                agent_contract.agent_result({"name": "ask", "args": ["hello"], "outcome": "OK", "result": changed}, 6)

    def test_main_output_text_cannot_inject_prompt_or_controls(self):
        other = self.root / "main-output"
        content = "\x1b[2J\naios> service stop\r\u202e"
        self.run_session(other, ["ask hello", "exit"],
                         agent_control=mock.Mock(return_value=agent("ask", bound=True, prompt="hello", content=content)))
        verdict = verifier.verify_session(other)
        self.assertEqual(verdict["outcome"], "PASS", verdict)
        raw = (other / "console.log").read_bytes()
        self.assertEqual(len(re.findall(PROMPT_PATTERN, raw)), 2)
        self.assertNotIn(b"\x1b", raw)

    def test_main_source_exit_and_warmup_fail_closed(self):
        value = agent_source(lifecycle_state="exited")
        with self.assertRaises(ValueError):
            agent_contract.validate_source(value)
        value = agent_source(warmup_request_sha256=None)
        with self.assertRaises(ValueError):
            agent_contract.validate_source(value)

    def test_main_fixture_backend_cannot_pass_live_gate(self):
        value = agent("ask", bound=True, prompt="hello")
        value["capture_kind"] = "fixture"
        command = {"name": "ask", "args": ["hello"], "outcome": "OK", "result": value}
        agent_contract.agent_result(command, 6)
        with self.assertRaises(ValueError):
            agent_contract.agent_result(command, 6, require_live=True)

    def test_main_failed_inference_invalidates_source_and_preserves_failure(self):
        from aios_management.binding import Authority
        value = agent("ask", bound=True, prompt="hello")
        evidence = value["inference_receipt"]
        before = evidence["source_before"]
        after = {**before, "model_ready": False, "source_generation": before["source_generation"] + 1}
        authority = Authority(evidence["authority_instance"])
        authority.initialize()
        authority.discover([before])
        authority.bind(before)
        authority.observe(after)
        evidence.update(outcome="ERROR", error="backend-timeout", response_body=None, response_sha256=None,
                        content=None, tokens_predicted=0, source_after=after)
        value.update(outcome="ERROR", error="backend-timeout", source_record=after,
                     management_snapshot=authority.snapshot(), management_outcome="rejected")
        command = {"name": "ask", "args": ["hello"], "outcome": "ERROR", "result": value}
        agent_contract.agent_result(command, 6)
        self.assertEqual(value["management_snapshot"]["state"], "STALE")
        other = self.root / "failed-main-request"
        self.run_session(other, ["ask hello", "status", "exit"], agent_control=mock.Mock(return_value=value))
        verdict = verifier.verify_session(other)
        self.assertEqual(verdict["outcome"], "PASS", verdict)
        value["error"] = "backend-failed"
        with self.assertRaises(ValueError):
            agent_contract.agent_result(command, 6)

    def test_resource_errors_and_transport_failure_preserve_exact_console_output(self):
        other = self.root / "resource-errors"
        absent = agent("resources-status", state="ABSENT", error="process-not-running")
        unlinked = {**agent("resources-sample", bound=True, error="resource-unlinked"),
                    "capture_kind": "fixture", "resource_result": resource_error("sample")}
        asked = {**agent("ask", bound=True, prompt="hello"), "capture_kind": "fixture",
                 "resource_result": resource_error("request", "process-exited")}
        commands = ["resources status", "resources sample", "ask hello", "resources bad", "help", "about", "exit"]
        self.run_session(other, commands, agent_control=mock.Mock(side_effect=[absent, unlinked, asked]))
        result = verifier.verify_session(other, expected_commands=commands)
        self.assertEqual(result["outcome"], "PASS", result)
        self.assertEqual(verifier.verify_session(other, require_live=True)["outcome"], "FAIL")

    @staticmethod
    def resource_reply(kind="sample"):
        from test_hosted_resources_management import fixture as resource_fixture, receipt_fixture
        data = resource_fixture(kind)
        action = "ask" if kind == "request" else "resources-sample"
        value = {**agent("status"), "action": action, "source_record": data["source"],
                 "management_snapshot": data["snapshot"], "resource_result": data["result"],
                 "capture_kind": "fixture"}
        if kind == "request":
            value.update(inference_receipt={**receipt_fixture(data), "schema_version": 2, "backend_execution": None}, management_outcome="accepted")
        return value

    def test_resource_sample_request_and_cached_status_full_evidence_and_output(self):
        for kind in ("sample", "request"):
            with self.subTest(kind=kind):
                other = self.root / ("resource-" + kind)
                value = self.resource_reply(kind)
                cached = copy.deepcopy(value)
                cached.update(action="resources-status", inference_receipt=None, management_outcome=None)
                cached["resource_result"]["action"] = "status"
                commands = ["ask Say hello." if kind == "request" else "resources sample", "resources status", "exit"]
                self.run_session(other, commands, agent_control=mock.Mock(side_effect=[value, cached]))
                result = verifier.verify_session(other, expected_commands=commands)
                self.assertEqual(result["outcome"], "PASS", result)
                self.assertEqual(verifier.verify_session(other, require_live=True)["outcome"], "FAIL")
                text = (other / "console.log").read_text(encoding="utf-8")
                self.assertIn("Observed process window", text)
                self.assertIn("Last observation (cached)", text)
                self.assertIn("RSS estimate 0.12 MiB", text)
                self.assertIn("Model backend process: CPU 1750.000 ms", text)

    def test_resource_forgery_rejected_after_artifact_hashes_recomputed(self):
        self.session = self.root / "resource-forgery"
        self.run_session(self.session, ["ask Say hello.", "exit"],
                         agent_control=mock.Mock(return_value=self.resource_reply("request")))
        baseline = self.events()
        changes = (
            lambda r: r["observation"]["cpu"]["backend"].update(cpu_time_ns=1),
            lambda r: r["observation"]["after"]["main"].update(rss_bytes_estimate=0),
            lambda r: r["observation"]["after"]["backend"].update(process_start_ticks=999),
            lambda r: r["observation"]["after"]["pressure"].update(attribution="main-node"),
            lambda r: r["observation"]["after"]["pressure"]["metrics"]["cpu"].update(full_valid=True),
            lambda r: r["observation"].update(request_id="00000000-0000-4000-8000-000000000099"),
            lambda r: r["observation"]["relation_after"].update(binding_generation=99),
            lambda r: r["observation"]["after"]["main"].update(raw_stat=r["observation"]["before"]["main"]["raw_stat"]),
        )
        for mutation in changes:
            events = copy.deepcopy(baseline)
            mutation(events[1]["data"]["result"]["resource_result"])
            self.rehash(events)
            result = verifier.verify_session(self.session)
            with self.subTest(mutation=mutation):
                self.assertEqual(result["outcome"], "FAIL", result)
                self.assertIn("resource_contract:", str(result))

    def test_cell_lifecycle_and_inactive_status_have_exact_display(self):
        other = self.root / "cell-session"
        replies = [cell(), cell("deactivate"), cell(active=False), cell("activate", active=False),
                   cell("activate"), cell("deactivate", active=False)]
        commands = ["cell status", "cell deactivate", "cell status", "cell activate", "cell activate",
                    "cell deactivate", "cell stop", "help", "about", "exit"]
        self.run_session(other, commands, agent_control=mock.Mock(side_effect=replies))
        result = verifier.verify_session(other, expected_commands=commands)
        self.assertEqual(result["outcome"], "PASS", result)
        self.assertEqual(verifier.verify_session(other, require_live=True)["outcome"], "FAIL")
        text = (other / "console.log").read_text(encoding="utf-8")
        self.assertIn("AIOS Cell 1: inactive; generation 2", text)
        self.assertIn("AIOS Cell 1: active; generation 3", text)
        self.assertIn("MAIN process: RUNNING; Cell activity does not start or stop it.", text)

    def test_backend_lifecycle_and_errors_have_independent_exact_output(self):
        other = self.root / "backend-session"
        replies = [backend(state="ABSENT"), backend("start"), backend("restart", generation=2),
                   backend("stop", state="STOPPED", generation=2), backend(state="STALE", error="process-not-running")]
        commands = ["backend status", "backend start", "backend restart", "backend stop", "backend status",
                    "backend kill", "help", "about", "exit"]
        self.run_session(other, commands, backend_control=mock.Mock(side_effect=replies))
        result = verifier.verify_session(other, expected_commands=commands)
        self.assertEqual(result["outcome"], "PASS", result)
        self.assertEqual(verifier.verify_session(other, require_live=True)["outcome"], "FAIL")
        text = (other / "console.log").read_text(encoding="utf-8")
        self.assertIn("AIOS model backend: RUNNING; backend ready", text)
        self.assertIn("Start generation 2;", text)
        self.assertIn("AIOS model backend: STALE; backend not ready", text)

    def test_backend_forgery_and_output_drift_fail_after_rehash(self):
        self.session = self.root / "backend-forgery"
        self.run_session(self.session, ["backend status", "exit"], backend_control=mock.Mock(return_value=backend()))
        baseline = self.events()
        for mutation in (lambda v: v.update(capture_kind="live"), lambda v: v.update(service_kind="AI_SERVICE"),
                         lambda v: v["service_record"].update(start_generation=0),
                         lambda v: v["descriptor"].update(process_start_ticks=999),
                         lambda v: v.update(descriptor=None)):
            events = copy.deepcopy(baseline)
            mutation(events[1]["data"]["result"])
            self.rehash(events)
            result = verifier.verify_session(self.session)
            with self.subTest(mutation=mutation):
                self.assertEqual(result["outcome"], "FAIL", result)
        self.rehash(baseline)
        log = self.session / "console.log"
        log.write_bytes(log.read_bytes().replace(b"backend ready", b"backend not ready"))
        self.rehash()
        result = verifier.verify_session(self.session)
        self.assertEqual(result["outcome"], "FAIL", result)
        self.assertIn("transcript_mismatch", str(result))

    def test_current_backend_evidence_cannot_be_relabelled_as_v05(self):
        self.session = self.root / "backend-version"
        self.run_session(self.session, ["backend status", "exit"], backend_control=mock.Mock(return_value=backend()))
        events = self.events()
        for event in events:
            event["schema_version"] = 5
        events[0]["data"]["runtime_version"] = "0.5.0"
        events[0]["data"].pop("source_process")
        self.rehash(events, lambda r: r.update(schema_version=5,
            source_hashes={k:v for k,v in r["source_hashes"].items() if k in verifier.SOURCES}))
        result = verifier.verify_session(self.session)
        self.assertEqual(result["outcome"], "FAIL", result)

    def test_cell_management_and_transport_errors_are_clean_session_results(self):
        other = self.root / "cell-errors"
        replies = [cell("activate", error="overflow"),
                   agent("cell-status", state="ABSENT", error="process-not-running"),
                   agent("cell-deactivate", state="UNSUPPORTED", error="unsupported-platform")]
        commands = ["cell activate", "cell status", "cell deactivate", "status", "exit"]
        self.run_session(other, commands, agent_control=mock.Mock(side_effect=replies))
        result = verifier.verify_session(other, expected_commands=commands)
        self.assertEqual(result["outcome"], "PASS", result)

    def test_cell_response_and_transcript_cannot_disagree_after_rehash(self):
        self.session = self.root / "cell-forgery"
        self.run_session(self.session, ["cell deactivate", "exit"], agent_control=mock.Mock(return_value=cell("deactivate")))
        baseline = self.events()
        for mutation in (lambda v: v.update(management_outcome=None),
                         lambda v: v.update(resource_result=resource_error()),
                         lambda v: v.update(schema_version=2),
                         lambda v: v["management_snapshot"]["parent"].update(active=True),
                         lambda v: v["management_snapshot"].update(bound_nodes=1),
                         lambda v: v["management_snapshot"]["canonical"].update(generation=1)):
            events = copy.deepcopy(baseline)
            mutation(events[1]["data"]["result"])
            self.rehash(events)
            with self.subTest(mutation=mutation):
                self.assertEqual(verifier.verify_session(self.session)["outcome"], "FAIL")
        self.rehash(baseline)
        path = self.session / "console.log"
        path.write_bytes(path.read_bytes().replace(b"Cell 1: inactive", b"Cell 1: active"))
        self.rehash()
        result = verifier.verify_session(self.session)
        self.assertEqual(result["outcome"], "FAIL", result)
        self.assertIn("transcript_mismatch", str(result))

    def test_cell_evidence_cannot_be_relabelled_as_v04(self):
        self.session = self.root / "cell-version"
        self.run_session(self.session, ["cell status", "exit"], agent_control=mock.Mock(return_value=cell()))
        events = self.events()
        for event in events:
            event["schema_version"] = 4
        events[0]["data"]["runtime_version"] = "0.4.0"
        events[0]["data"].pop("source_process")
        events[1]["data"]["result"]["schema_version"] = 2
        self.rehash(events, lambda r: r.update(schema_version=4, source_hashes={k:v for k,v in r["source_hashes"].items() if k in verifier.SOURCES}))
        result = verifier.verify_session(self.session)
        self.assertEqual(result["outcome"], "FAIL", result)


class BackendContractTests(unittest.TestCase):
    @staticmethod
    def command(value):
        return {"name": "backend", "args": [value["action"]], "outcome": value["outcome"], "result": value}

    def test_success_actions_and_terminal_identity_are_exact(self):
        for value in (backend(state="ABSENT"), backend(state="STARTING"), backend(), backend("start"), backend("restart", generation=2),
                      backend("stop", state="STOPPED"), backend("stop", state="ABSENT"), backend(state="STOPPED")):
            backend_contract.backend_result(self.command(value), 6)
            for schema in range(1, 6):
                with self.subTest(schema=schema, action=value["action"]), self.assertRaises(ValueError):
                    backend_contract.backend_result(self.command(value), schema)

    def test_backend_fixture_is_never_live_and_profile_must_agree(self):
        value = backend()
        with self.assertRaisesRegex(ValueError, "fixture_not_live"):
            backend_contract.validate_backend_result(value, require_live=True)
        value["capture_kind"] = "live"
        with self.assertRaisesRegex(ValueError, "record_capture"):
            backend_contract.validate_backend_result(value)
        value["service_record"]["profile"] = "llamafile-pinned"
        backend_contract.validate_backend_result(value, require_live=True)

    def test_backend_state_and_action_cannot_promote_readiness(self):
        for state in ("STARTING", "STOPPING", "STALE", "FAILED", "UNSUPPORTED"):
            error = "unsupported-platform" if state == "UNSUPPORTED" else "process-not-running"
            value = backend(state=state, error=error)
            backend_contract.validate_backend_result(value)
            if state != "STARTING":
                with self.subTest(state=state), self.assertRaises(ValueError):
                    backend_contract.validate_backend_result({**value, "outcome": "OK", "error": None})
        value = backend(state="STARTING", error="start-in-progress")
        value["service_record"] = None
        backend_contract.validate_backend_result(value)
        with self.assertRaises(ValueError):
            backend_contract.validate_backend_result({**value, "error": "start-failed"})
        stopped = backend("stop", state="STOPPED")
        stopped["service_record"]["child_identity"] = None
        with self.assertRaises(ValueError):
            backend_contract.validate_backend_result(stopped)
        for action, state in (("start", "STOPPED"), ("restart", "ABSENT"), ("stop", "RUNNING")):
            with self.subTest(action=action), self.assertRaisesRegex(ValueError, "action_state"):
                backend_contract.validate_backend_result(backend(action, state=state))

    def test_backend_record_descriptor_and_boundaries_reject_forgery(self):
        mutations = (
            lambda v: v.update(schema_version=True), lambda v: v.update(extra=None),
            lambda v: v.update(service_kind="AI_SERVICE"), lambda v: v.update(resource_actions="SUPPORTED"),
            lambda v: v.update(outcome="ERROR", error="error\naios>"), lambda v: v.update(descriptor=None),
            lambda v: v["service_record"].update(start_generation=True),
            lambda v: v["service_record"].update(start_generation=65),
            lambda v: v["service_record"].update(lifecycle_state="exited"),
            lambda v: v["service_record"].update(source_only=False),
            lambda v: v["service_record"]["child_identity"].update(uid=0),
            lambda v: v["service_record"]["child_identity"].update(process_id=201),
            lambda v: v["service_record"]["supervisor_identity"].update(process_start_ticks=0),
            lambda v: v["descriptor"].update(source_generation=2),
            lambda v: v["descriptor"].update(launcher_start_ticks=999),
            lambda v: v["descriptor"].update(source_instance="00000000-0000-4000-8000-000000000025"),
            lambda v: v["descriptor"].update(listener_inode=0),
            lambda v: v["descriptor"].update(endpoint="http://example.com:8089"),
            lambda v: v["descriptor"].update(model_sha256="x" * 64),
            lambda v: v["descriptor"].update(extra=None),
        )
        for mutation in mutations:
            value = backend()
            mutation(value)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                backend_contract.validate_backend_result(value)
        for state in ("STALE", "STOPPING", "STARTING"):
            value = backend(state=state, error="process-not-running")
            value["service_record"]["backend_ready"] = True
            with self.subTest(state=state), self.assertRaises(ValueError):
                backend_contract.validate_backend_result(value)


class AgentResourceContractTests(unittest.TestCase):
    @staticmethod
    def command(value):
        action = value["action"]
        if action.startswith("resources-"):
            name, args = "resources", [action.removeprefix("resources-")]
        elif action.startswith("cell-"):
            name, args = "cell", [action.removeprefix("cell-")]
        elif action == "ask":
            name, args = "ask", ["hello"]
        else:
            name, args = "agent", [action]
        return {"name": name, "args": args, "outcome": value["outcome"], "result": value}

    def test_protocol1_2_3_4_have_distinct_exact_schema_contracts(self):
        current = agent("status")
        for protocol in (1, 2, 3, 4):
            value = agent("status", protocol=protocol)
            agent_contract.agent_result(self.command(value), protocol + 2)
            for schema in (3, 4, 5, 6):
                if schema != protocol + 2:
                    with self.subTest(protocol=protocol, schema=schema), self.assertRaises(ValueError):
                        agent_contract.agent_result(self.command(value), schema)
        for mutation in (lambda v: v.pop("resource_result"), lambda v: v.update(schema_version=True),
                         lambda v: v.update(resource_result=resource_error()), lambda v: v.update(extra=None)):
            changed = copy.deepcopy(current)
            mutation(changed)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                agent_contract.agent_result(self.command(changed), 6)

    def test_receipt_schema1_history_and_schema2_fixture_live_boundary(self):
        for protocol in (1, 2, 3, 4):
            value = agent("ask", bound=True, prompt="hello", protocol=protocol)
            agent_contract.agent_result(self.command(value), protocol + 2)
            self.assertEqual(value["inference_receipt"]["schema_version"], 2 if protocol == 4 else 1)
        current = agent("ask", bound=True, prompt="hello")
        current["capture_kind"] = "live"
        with self.assertRaisesRegex(ValueError, "missing_execution"):
            agent_contract.agent_result(self.command(current), 6)
        current["capture_kind"] = "fixture"
        for change in ({"schema_version": 1}, {"backend_execution": {}}, {"extra": None}):
            value = copy.deepcopy(current)
            value["inference_receipt"].update(change)
            with self.subTest(change=change), self.assertRaises(ValueError):
                agent_contract.agent_result(self.command(value), 6)

    def test_backend_changed_is_pre_request_error_and_not_a_legacy_error(self):
        value = agent("ask", bound=True, error="backend-changed")
        agent_contract.agent_result(self.command(value), 6)
        for protocol in (1, 2, 3):
            old = agent("ask", bound=True, error="backend-changed", protocol=protocol)
            with self.subTest(protocol=protocol), self.assertRaisesRegex(ValueError, "legacy_backend_error"):
                agent_contract.agent_result(self.command(old), protocol + 2)

    def test_receipt_execution_proof_is_validated_and_matches_model_identity(self):
        from test_hosted_backend_execution import execution_fixture
        value = agent("ask", bound=True, prompt="hello")
        inference = value["inference_receipt"]
        execution = execution_fixture()
        execution["descriptor"].update({key: inference[key] for key in ("model_id", "model_sha256", "backend_sha256")})
        execution["descriptor"]["host_boot_id"] = value["source_record"]["host_boot_id"]
        for frame in ("before", "send", "after"):
            for role in ("backend", "launcher", "client") if frame == "send" else ("backend", "launcher"):
                execution[frame][role]["host_boot_id"] = value["source_record"]["host_boot_id"]
        worker = execution["send"]["client"]
        head, tail = worker["raw_stat"].rsplit(") ", 1)
        fields = tail.split()
        fields[1] = str(value["source_record"]["process_id"])
        worker["raw_stat"] = head + ") " + " ".join(fields) + "\n"
        inference["elapsed_ns"] = 5000
        inference["backend_execution"] = execution
        agent_contract.agent_result(self.command(value), 6)
        for mutate in (lambda e: e["descriptor"].update(model_id="other-model"),
                       lambda e: e["descriptor"].update(backend_sha256="e" * 64),
                       lambda e: e["send"].update(server_fd_target="socket:[999]"),
                       lambda e: e.update(after=None), lambda e: e.update(capture_kind="live")):
            changed = copy.deepcopy(value)
            mutate(changed["inference_receipt"]["backend_execution"])
            with self.subTest(mutation=mutate), self.assertRaises(ValueError):
                agent_contract.agent_result(self.command(changed), 6)
        changed = copy.deepcopy(value)
        changed["inference_receipt"]["elapsed_ns"] = 100
        with self.assertRaisesRegex(ValueError, "execution_window"):
            agent_contract.agent_result(self.command(changed), 6)
        changed = copy.deepcopy(value)
        changed["inference_receipt"]["backend_execution"]["send"]["client"]["raw_stat"] = worker["raw_stat"].replace(") S 123 ", ") S 999 ")
        with self.assertRaisesRegex(ValueError, "execution_owner"):
            agent_contract.agent_result(self.command(changed), 6)

    def test_failed_receipt_cannot_retain_successful_execution_evidence(self):
        value = agent("ask", bound=True, prompt="hello")["inference_receipt"]
        for key in agent_contract.REQUEST_CONTEXT:
            value.pop(key)
        value.update(outcome="ERROR", error="backend-failed", response_body=None, response_sha256=None,
                     content=None, tokens_predicted=0)
        agent_contract.receipt(value)
        value["backend_execution"] = {}
        with self.assertRaisesRegex(ValueError, "failed_execution"):
            agent_contract.receipt(value)

    def test_resource_without_response_requires_transport_or_process_failure(self):
        for state, error in (("ABSENT", "process-not-running"), ("STOPPED", "process-not-running"),
                             ("UNSUPPORTED", "unsupported-platform"), ("RUNNING", "rpc-timeout")):
            agent_contract.agent_result(self.command(agent("resources-status", state=state, error=error)), 6)
        for error in (None, "resource-unlinked", "already-running"):
            with self.subTest(error=error), self.assertRaises(ValueError):
                agent_contract.agent_result(self.command(agent("resources-status", error=error)), 6)

    def test_resource_error_reply_is_independent_from_inference_outcome(self):
        value = {**agent("ask", bound=True, prompt="hello"), "capture_kind": "fixture",
                 "resource_result": resource_error("request", "process-exited")}
        agent_contract.agent_result(self.command(value), 6)
        self.assertEqual(value["outcome"], "OK")
        self.assertTrue(value["source_record"]["model_ready"])
        changed = copy.deepcopy(value)
        changed["resource_result"]["action"] = "sample"
        with self.assertRaises(ValueError):
            agent_contract.agent_result(self.command(changed), 6)

    def test_resource_result_exact_boundary_and_parent_outcome_agree(self):
        valid = {**agent("resources-status", bound=True, error="resource-unlinked"), "capture_kind": "fixture",
                 "resource_result": resource_error()}
        agent_contract.agent_result(self.command(valid), 6)
        mutations = (lambda v: v.update(outcome="OK", error=None), lambda v: v.update(error="process-exited"),
                     lambda v: v.update(capture_kind="live"), lambda v: v.update(management_outcome="accepted"),
                     lambda v: v["resource_result"].update(relation_current=True),
                     lambda v: v["resource_result"].update(ownership_valid=True),
                     lambda v: v["resource_result"].update(observation_only=False),
                     lambda v: v["resource_result"].update(resource_actions="SUPPORTED"),
                     lambda v: v["resource_result"].update(action="sample"),
                     lambda v: v["resource_result"].update(schema_version=True),
                     lambda v: v["resource_result"].update(unexpected=0))
        for mutation in mutations:
            changed = copy.deepcopy(valid)
            mutation(changed)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                agent_contract.agent_result(self.command(changed), 6)
        with self.assertRaises(ValueError):
            agent_contract.agent_result(self.command(valid), 6, require_live=True)

    def test_resource_display_reconstruction_and_cached_labels_are_independent(self):
        for action in ("sample", "status", "request"):
            value = resource_display(action)
            self.assertEqual(agent_contract.render_resource(value), "".join(line + "\n" for line in shell._resource_lines(value)))
            text = agent_contract.render_resource(value)
            self.assertEqual("Last observation (cached)" in text, action == "status")
            self.assertIn("CPU: some avg10 1.23%; full avg10 undefined for system CPU.", text)
        stale = resource_display("sample")
        stale.update(outcome="ERROR", error="resource-relation-stale", relation_current=False)
        self.assertIn("Last observation (cached)", agent_contract.render_resource(stale))
        self.assertIn("AIOS resources: stale", agent_contract.render_resource(stale))

    def test_cell_success_status_accepts_active_and_inactive_but_actions_match(self):
        for value in (cell(), cell(active=False), cell("deactivate"), cell("activate", active=False),
                      cell("activate"), cell("deactivate", active=False), cell(bound=False)):
            agent_contract.agent_result(self.command(value), 6)
            for schema in (1, 2, 3, 4):
                with self.subTest(action=value["action"], schema=schema), self.assertRaises(ValueError):
                    agent_contract.agent_result(self.command(value), schema)
        for value in (cell("activate"), cell("deactivate")):
            value["action"] = "cell-deactivate" if value["action"] == "cell-activate" else "cell-activate"
            with self.assertRaisesRegex(ValueError, "cell_activity"):
                agent_contract.agent_result(self.command(value), 6)

    def test_cell_state_reports_do_not_require_model_ready_or_promote_binding(self):
        value = cell(active=False)
        self.assertEqual(value["source_record"], agent_source())
        self.assertFalse(value["management_snapshot"]["binding_current"])
        self.assertTrue(value["source_record"]["model_ready"])
        agent_contract.agent_result(self.command(value), 6)
        value = cell(bound=False)
        value["source_record"].update(model_ready=False, source_generation=2)
        agent_contract.agent_result(self.command(value), 6)

    def test_cell_error_outcomes_and_empty_resource_contract_remain_exact(self):
        for management in (None, "rejected"):
            value = cell("activate", error="overflow")
            value["management_outcome"] = management
            agent_contract.agent_result(self.command(value), 6)
        valid = cell()
        for change in ({"management_outcome": "rejected"}, {"management_outcome": None},
                       {"inference_receipt": {}}, {"resource_result": {}}, {"state": "STOPPED"},
                       {"schema_version": True}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                agent_contract.agent_result(self.command({**valid, **change}), 6)


if __name__ == "__main__":
    unittest.main()
