"""Hostile completed MAIN artifacts; these generated records remain fixtures."""
from __future__ import annotations

import ast
import copy
import json
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/hosted"))
sys.path.insert(0, str(ROOT / "hosted/linux"))
from aios_agent.inference import request_body
from aios_management.binding import Authority
from verify_agent import (BACKEND_SHA, FIXED_FILES, MODEL_BYTES, MODEL_SHA, LEGACY_SOURCES as SOURCES,
                          digest, verify_interactive, verify_model, verify_run, verify_shutdown)
from test_hosted_management import source


WHEN = "2026-09-07T00:00:00+00:00"


def encoded(value):
    return (json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def receipt(purpose="warmup"):
    request = request_body("Say hello.", warmup=purpose == "warmup").decode()
    response = json.dumps({"content": "Hello!", "tokens_predicted": 2, "model": "fixture-model"})
    return {"schema_version": 1, "request_id": str(uuid.uuid4()), "started_at": WHEN,
            "purpose": purpose, "model_id": "fixture-model", "model_sha256": "1" * 64,
            "backend_sha256": "2" * 64, "provenance_sha256": "3" * 64,
            "request_body": request, "request_sha256": digest(request.encode()),
            "response_body": response, "response_sha256": digest(response.encode()),
            "content": "Hello!", "tokens_predicted": 2, "elapsed_ns": 100,
            "outcome": "OK", "error": None}


class AgentVerifierTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.sources = self.root / "runtime-source"
        for name in SOURCES:
            path = self.sources / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(("# retained test fixture " + name + "\n").encode())
        self.warmup = receipt()
        self.source = source(warmup_request_sha256=self.warmup["request_sha256"],
                             warmup_response_sha256=self.warmup["response_sha256"])
        self.run = self.root / self.source["source_instance"]
        self.run.mkdir()
        self.authority = Authority()
        self.authority.initialize()
        self.events = []
        self.event("STARTING", None)
        self.event("RUNNING", self.source, file="warmup.json")
        self.authority.discover([self.source])
        self.event("COMMAND", self.source, action="room-discover")
        self.authority.bind(self.source)
        self.event("COMMAND", self.source, action="room-bind")
        self.user = receipt("user")
        self.user.update(source_before=copy.deepcopy(self.source), authority_instance=self.authority.authority_instance,
                         binding_generation=1)
        self.source["completed_requests"] += 1
        self.authority.observe(self.source)
        self.user["source_after"] = copy.deepcopy(self.source)
        self.user_path = "requests/" + self.user["request_id"] + ".json"
        self.event("COMMAND", self.source, action="ask", file=self.user_path)
        self.source.update(lifecycle_state="exited", model_ready=False, source_generation=2)
        self.authority.observe(self.source)
        self.event("STOPPING", self.source)
        self.event("STOPPED", self.source)
        self.config = {"schema_version": 1, "endpoint": "http://127.0.0.1:8000", "model_id": "fixture-model",
                       "model_path": "/fixture/model", "backend_path": "/fixture/backend", "model_sha256": "1" * 64,
                       "backend_sha256": "2" * 64, "provenance_sha256": "3" * 64}
        self.start = {"schema_version": 1, "source_id": self.source["source_id"],
                      "source_instance": self.source["source_instance"], "service_start_generation": 1,
                      "service_kind": "AI_SERVICE", "capture_kind": "fixture", "started_at": WHEN,
                      "config_sha256": digest(encoded(self.config)),
                      "source_hashes": {name: digest((self.sources / name).read_bytes()) for name in SOURCES}}
        self.result = {"schema_version": 1, "source_id": self.source["source_id"],
                       "source_instance": self.source["source_instance"], "service_start_generation": 1,
                       "state": "STOPPED", "exit_code": 0, "error": None, "completed_at": WHEN,
                       "capture_kind": "fixture", "source_record": self.source,
                       "management_snapshot": self.authority.snapshot(), "files": {}}
        self.save()

    def event(self, name, row, *, action=None, file=None):
        self.events.append({"schema_version": 1, "source_instance": self.source["source_instance"],
                            "sequence": len(self.events) + 1, "monotonic_ns": 100 + len(self.events),
                            "event": name, "action": action, "outcome": "OK", "error": None,
                            "source_record": copy.deepcopy(row), "management_snapshot": self.authority.snapshot(),
                            "receipt_file": file})

    def save(self):
        for name, value in (("start.json", self.start), ("config.json", self.config), ("warmup.json", self.warmup),
                            ("source.json", self.source), ("management.json", self.authority.snapshot()),
                            (self.user_path, self.user)):
            path = self.run / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(encoded(value))
        (self.run / "events.jsonl").write_bytes(b"".join(encoded(row) for row in self.events))
        self.result["files"] = {name: digest((self.run / name).read_bytes()) for name in FIXED_FILES | {self.user_path}}
        (self.run / "result.json").write_bytes(encoded(self.result))

    def verify(self, **options):
        return verify_run(self.run, self.sources, **options)

    def assertFails(self, *reason):
        self.save()
        result = self.verify()
        self.assertEqual(result["outcome"], "FAIL", result)
        if reason:
            self.assertTrue(any(text in result["reasons"][0] for text in reason), result)

    def test_consistent_fixture_passes_without_claiming_process_or_model_bytes(self):
        result = self.verify()
        self.assertEqual(result["outcome"], "PASS", result)
        self.assertFalse(result["process_exit_verified"])
        self.assertFalse(result["model_bytes_verified"])
        self.assertEqual(len(result["requests"]), 1)
        self.assertEqual(self.verify(require_live=True)["outcome"], "FAIL")

    def test_saved_source_hash_must_match_retained_runtime(self):
        self.start["source_hashes"][SOURCES[0]] = "0" * 64
        self.assertFails("source_hash")

    def test_config_hash_cannot_be_detached_from_saved_configuration(self):
        self.config["model_sha256"] = "4" * 64
        self.assertFails("config_hash")

    def test_matching_config_file_hash_does_not_hide_wrong_model(self):
        self.config["model_sha256"] = "4" * 64
        self.start["config_sha256"] = digest(encoded(self.config))
        self.assertFails("receipt_config")

    def test_rehashed_empty_or_wrong_model_warmup_is_rejected(self):
        self.warmup["response_body"] = json.dumps({"content": "", "tokens_predicted": 2, "model": "fixture-model"})
        self.warmup["response_sha256"] = digest(self.warmup["response_body"].encode())
        self.warmup["content"] = ""
        self.assertFails("receipt_completion")

    def test_ready_record_cannot_replace_warmup_receipt(self):
        self.events[1]["receipt_file"] = None
        self.assertFails("running_readiness")

    def test_foreign_source_or_authority_cannot_supply_request(self):
        self.user["authority_instance"] = str(uuid.uuid4())
        self.assertFails("request_authority")

    def test_request_before_source_must_be_preceding_observed_state(self):
        self.user["source_before"]["completed_requests"] = 0
        self.assertFails("source_warmup", "request_source")

    def test_receipt_cannot_reuse_warmup_request_identity(self):
        self.user["request_id"] = self.warmup["request_id"]
        self.assertFails("request_identity")

    def test_user_request_requires_current_bound_generation(self):
        self.user["binding_generation"] = 2
        self.assertFails("request_binding")

    def test_counter_regression_and_source_semantics_are_rejected(self):
        self.events[4]["source_record"]["completed_requests"] = 0
        self.assertFails("source_warmup", "source_snapshot", "source_requests")

    def test_duplicate_fields_truncation_and_extra_requests_are_rejected(self):
        raw = (self.run / "events.jsonl").read_bytes()
        (self.run / "events.jsonl").write_bytes(raw.rstrip(b"\n"))
        self.assertEqual(self.verify()["outcome"], "FAIL")
        self.save()
        (self.run / "requests/unknown.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self.verify()["outcome"], "FAIL")

    def test_missing_terminal_or_failed_result_never_passes(self):
        self.result["state"] = "FAILED"
        self.assertFails("agent_not_stopped")

    def test_altered_terminal_generation_cannot_pass_with_rehashed_files(self):
        self.events[-1]["source_record"]["source_generation"] = 3
        self.assertFails("terminal_generation", "source_snapshot")

    def test_command_with_unknown_error_is_not_valid_negative_evidence(self):
        self.events[2].update(outcome="ERROR", error="unknown-future-success")
        self.assertFails("event_error")

    def test_real_shutdown_requires_clean_process_and_anchored_final_record(self):
        vm = {"outcome": "PASS", "vm_exit_code": 0, "host_killed": False, "shutdown_observed": True}
        (self.root / "vm-verdict.json").write_bytes(encoded(vm))
        (self.root / "linux-serial.log").write_bytes(b"boot\r\n[  35.777] reboot: Power down\r\n")
        verify_shutdown(self.root)
        (self.root / "linux-serial.log").write_bytes(b"boot\r\nlocalhost:~# [ 1178.297624] reboot: Power down\r\n")
        verify_shutdown(self.root)
        for serial in (b"quoted reboot: Power down\n", b"reboot: Power down\nFATAL\n", b"boot\n",
                       b"reboot: Power down\nreboot: Power down\n",
                       b"quoted localhost:~# [ 1178.297624] reboot: Power down\r\n",
                       b"other:~# [ 1178.297624] reboot: Power down\r\n",
                       b" localhost:~# [ 1178.297624] reboot: Power down\r\n",
                       b"localhost:~# [ 1178.297624] reboot: Power down\r\nFATAL\r\n"):
            (self.root / "linux-serial.log").write_bytes(serial)
            with self.subTest(serial=serial), self.assertRaises(ValueError):
                verify_shutdown(self.root)
        (self.root / "linux-serial.log").write_bytes(b"reboot: Power down\n")
        for change in ({"host_killed": True}, {"vm_exit_code": True}, {"vm_exit_code": 1}, {"shutdown_observed": False}):
            (self.root / "vm-verdict.json").write_bytes(encoded({**vm, **change}))
            with self.subTest(change=change), self.assertRaises(ValueError):
                verify_shutdown(self.root)

    def test_runtime_module_imports_are_absent_from_verifier(self):
        tree = ast.parse((ROOT / "tools/hosted/verify_agent.py").read_text(encoding="utf-8"))
        modules = [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(any(name and name.startswith(("aios_agent", "aios_management", "aios_service")) for name in modules))


class AgentModelVerifierTests(unittest.TestCase):
    """Consistency fixtures do not execute a model or establish live support."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "provenance").mkdir()
        (self.root / "model-integrity").mkdir()
        (self.root / "model-backend").mkdir()
        self.provenance = {"schema_version": 1, "prepared_at": WHEN, "purpose": "explicit verification fixture",
                           "repository_import": False, "repository_manifest_changed": False,
                           "host_global_install": False, "redistribution_approved": False,
                           "dependency_license_review": "fixture", "artifacts": [], "source_receipts": [],
                           "windows_probe": "not-executed", "linux_probe": "not-executed"}
        for name, sha, size, revision, url in (
                ("llamafile-0.10.5-thin.exe", BACKEND_SHA, 42328074, "486e6c5f9356eae50b851b07517bfae1f2420193",
                 "https://github.com/mozilla-ai/llamafile/releases/download/0.10.5/llamafile-0.10.5-thin"),
                ("Qwen3-0.6B-Q8_0.gguf", MODEL_SHA, MODEL_BYTES, "23749fefcc72300e3a2ad315e1317431b06b590a",
                 "https://huggingface.co/Qwen/Qwen3-0.6B-GGUF/resolve/23749fefcc72300e3a2ad315e1317431b06b590a/Qwen3-0.6B-Q8_0.gguf")):
            item = {"name": name, "upstream_name": name, "project": "fixture", "version": "fixture",
                    "revision": revision, "url": url, "license": "fixture", "size": size, "sha256": sha,
                    "hash_source": "fixture", "local_integrity_verified": True}
            if name.startswith("llamafile"):
                item["llama_cpp_revision"] = "c588c4f47683e73ad2d69f50480bec6cc85fd0f7"
            self.provenance["artifacts"].append(item)
        for index in range(11):
            name = "provenance/fixture-" + str(index) + ".txt"
            raw = ("explicit fixture " + str(index)).encode()
            (self.root / name).write_bytes(raw)
            self.provenance["source_receipts"].append({"path": name, "size": len(raw), "sha256": digest(raw),
                "url": "https://raw.githubusercontent.com/mozilla-ai/llamafile/fixture/LICENSE"})
        self.config = {"schema_version": 1, "endpoint": "http://127.0.0.1:18081",
                       "model_id": "aios-qwen3-0.6b-q8_0", "model_path": "/tmp/model.gguf",
                       "backend_path": "/fixture/backend.exe", "model_sha256": MODEL_SHA, "backend_sha256": BACKEND_SHA,
                       "provenance_sha256": ""}
        self.host = {"schema_version": 1, "model_bytes": MODEL_BYTES, "model_sha256": MODEL_SHA,
                     "backend_sha256": BACKEND_SHA, "verification": "host-read-complete"}
        self.guest = {"schema_version": 1, "model_bytes": MODEL_BYTES, "model_sha256": MODEL_SHA,
                      "verification": "guest-read-complete", "read_only_source": True}
        self.backend = {"schema_version": 1, "outcome": "PASS", "uid": 1000, "model_id": self.config["model_id"],
                        "model_sha256": MODEL_SHA, "backend_sha256": BACKEND_SHA, "ready": True, "error": None,
                        "host_killed": False, "process_exit_code": 0, "startup_seconds": 1.0, "pid": 123,
                        "elapsed_seconds": 5.0}
        self.command = {"source_only": True, "command": ["/bin/sh", self.config["backend_path"], "--server",
            "-m", self.config["model_path"], "--gpu", "disable", "--host", "127.0.0.1", "--port", "18081", "--no-webui",
            "-c", "1024", "-b", "64", "-ub", "64", "-t", "2", "-np", "1", "--alias", self.config["model_id"], "--nologo"]}
        (self.root / "model-backend/health.json").write_bytes(encoded({"status": "ok"}))
        (self.root / "model-backend/stdout.log").write_bytes(b"explicit fixture\n")
        (self.root / "model-backend/stderr.log").write_bytes(b"")
        self.save()

    def save(self):
        self.config["provenance_sha256"] = digest(encoded(self.provenance))
        for name, value in (("inference-provenance.json", self.provenance), ("inference-integrity.json", self.host),
                            ("model-integrity/integrity.json", self.guest), ("model-backend/result.json", self.backend),
                            ("model-backend/command.json", self.command)):
            (self.root / name).write_bytes(encoded(value))

    def assertRejected(self, reason):
        self.save()
        with self.assertRaisesRegex(ValueError, reason):
            verify_model(self.root, self.config)

    def test_complete_model_execution_record_consistency(self):
        self.assertEqual(verify_model(self.root, self.config), self.backend)
        self.backend["process_exit_code"] = -15
        self.save()
        self.assertEqual(verify_model(self.root, self.config), self.backend)

    def test_changed_model_hash_and_rehashed_provenance_are_rejected(self):
        self.provenance["artifacts"][1]["sha256"] = "4" * 64
        self.assertRejected("provenance_artifact_pin")

    def test_model_source_revision_and_url_are_pinned(self):
        self.provenance["artifacts"][1]["url"] = self.provenance["artifacts"][1]["url"].replace("23749fefcc72300e3a2ad315e1317431b06b590a", "main")
        self.assertRejected("provenance_artifact_pin")

    def test_changed_saved_upstream_receipt_is_detected(self):
        (self.root / "provenance/fixture-0.txt").write_bytes(b"replacement")
        self.assertRejected("provenance_source_hash")

    def test_guest_partial_model_read_is_not_full_integrity(self):
        self.guest["model_bytes"] -= 1
        self.assertRejected("guest_integrity_value")

    def test_backend_kill_or_nonzero_exit_never_passes(self):
        for changes in ({"host_killed": True}, {"process_exit_code": 1}, {"process_exit_code": False},
                        {"ready": False}, {"uid": 0}, {"elapsed_seconds": float("nan")}):
            original = dict(self.backend)
            self.backend.update(changes)
            with self.subTest(changes=changes):
                self.assertRejected("backend_execution|non_finite")
            self.backend = original

    def test_backend_launch_cannot_select_a_different_model(self):
        self.command["command"][4] = "/wrong/model.gguf"
        self.assertRejected("backend_launch")


class AgentInteractiveVerifierTests(unittest.TestCase):
    """Exercise aggregation over explicit model records and a mocked console gate.

    The real console parser has its own execution tests; these tests require its
    successful live verdict and cover the distinct no-MAIN control flow.
    """
    def setUp(self):
        self.model = AgentModelVerifierTests("test_complete_model_execution_record_consistency")
        self.model.setUp()
        self.addCleanup(self.model.doCleanups)
        self.root = self.model.root
        (self.root / "agent").mkdir()
        (self.root / "agent-stop").mkdir()
        (self.root / "session").mkdir()
        (self.root / "agent-config.json").write_bytes(encoded(self.model.config))
        (self.root / "execution.json").write_bytes(encoded({"mode": "interactive", "requested_commands": None}))
        (self.root / "session/session.events.jsonl").write_bytes(encoded({"event": "COMMAND", "data": {
            "name": "help", "args": [], "outcome": "OK", "result": {}}}))
        (self.root / "vm-verdict.json").write_bytes(encoded({"vm_exit_code": 0, "host_killed": False, "shutdown_observed": True}))
        (self.root / "linux-serial.log").write_bytes(b"[ 1.0] reboot: Power down\n")
        self.stop = {"schema_version": 1, "outcome": "OK", "error": None, "action": "stop", "state": "ABSENT",
                     "service_kind": "AI_SERVICE", "source_record": None, "management_snapshot": None,
                     "management_outcome": None, "inference_receipt": None, "resource_actions": "UNSUPPORTED", "capture_kind": "live"}
        self.save_stop()
        gate = patch("verify_console.verify_execution", return_value={"outcome": "PASS", "reasons": []})
        self.console = gate.start()
        self.addCleanup(gate.stop)

    def save_stop(self, *, exit_code=0, stderr=b""):
        raw = encoded(self.stop)
        (self.root / "agent-stop/stdout.log").write_bytes(raw)
        (self.root / "agent-stop/stderr.log").write_bytes(stderr)
        (self.root / "agent-stop/execution.json").write_bytes(encoded({"schema_version": 1, "action": "stop",
            "process_exit_code": exit_code, "stdout_sha256": digest(raw), "stderr_sha256": digest(stderr)}))

    def test_help_exit_without_main_preserves_absent_and_no_process_claim(self):
        result = verify_interactive(self.root)
        self.assertEqual(result["outcome"], "PASS", result)
        self.assertEqual(result["state"], "ABSENT")
        self.assertEqual(result["service_runs"], 0)
        self.assertFalse(result["main_started"])
        self.assertFalse(result["process_exit_verified"])
        self.assertTrue(result["backend_process_exit_verified"])
        self.assertTrue(result["vm_shutdown_verified"])
        self.assertTrue(self.console.call_args.kwargs["require_live"])

    def test_absent_requires_empty_state_and_no_past_source_claim(self):
        (self.root / "agent/registry.json").write_bytes(b"{}")
        self.assertEqual(verify_interactive(self.root)["outcome"], "FAIL")
        (self.root / "agent/registry.json").unlink()
        (self.root / "session/session.events.jsonl").write_bytes(encoded({"event": "COMMAND", "data": {
            "name": "agent", "args": ["start"], "outcome": "OK", "result": {"source_record": source()}}}))
        self.assertEqual(verify_interactive(self.root)["outcome"], "FAIL")

    def test_stop_exit_stderr_hash_or_fixture_failure_cannot_be_absent_pass(self):
        for exit_code, stderr in ((1, b""), (False, b""), (0, b"error\n")):
            self.save_stop(exit_code=exit_code, stderr=stderr)
            with self.subTest(exit_code=exit_code, stderr=stderr):
                self.assertEqual(verify_interactive(self.root)["outcome"], "FAIL")
        self.save_stop()
        (self.root / "agent-stop/stdout.log").write_bytes(b"{}")
        self.assertEqual(verify_interactive(self.root)["outcome"], "FAIL")
        self.stop["capture_kind"] = "fixture"
        self.save_stop()
        self.assertEqual(verify_interactive(self.root)["outcome"], "FAIL")

    def test_backend_and_console_failures_survive_absent_main(self):
        self.console.return_value = {"outcome": "FAIL", "reasons": ["console_process_failure"]}
        self.assertEqual(verify_interactive(self.root)["outcome"], "FAIL")
        self.console.return_value = {"outcome": "PASS", "reasons": []}
        self.model.backend["host_killed"] = True
        self.model.save()
        self.assertEqual(verify_interactive(self.root)["outcome"], "FAIL")

    def test_shutdown_default_is_required_and_pre_shutdown_mode_is_explicit(self):
        (self.root / "vm-verdict.json").unlink()
        self.assertEqual(verify_interactive(self.root)["outcome"], "FAIL")
        result = verify_interactive(self.root, require_shutdown=False)
        self.assertEqual(result["outcome"], "PASS", result)
        self.assertFalse(result["vm_shutdown_verified"])

    def test_stopped_reply_requires_service_run_acceptance_and_matching_source(self):
        # This aggregation seam uses the retained MAIN1 / CLI3 family.
        session = self.root / 'session/session.events.jsonl'
        session.write_bytes(encoded({'schema_version': 3, 'event': 'START', 'data': {}}) + session.read_bytes())
        active = source(model_sha256=MODEL_SHA)
        authority = Authority()
        authority.initialize()
        authority.discover([active])
        authority.bind(active)
        terminal = {**active, "source_generation": 2, "model_ready": False, "lifecycle_state": "exited"}
        authority.observe(terminal)
        self.stop.update(state="STOPPED", source_record=terminal, management_snapshot=authority.snapshot())
        self.save_stop()
        run = {"source_record": terminal, "management_snapshot": authority.snapshot(), "config": self.model.config,
               "events": [{"event": "RUNNING", "source_record": active},
                          {"event": "STOPPED", "source_record": terminal}], "requests": [], 'resource_results': []}
        with patch("verify_agent.verify_agent_runs", return_value={"outcome": "FAIL", "reasons": ["incomplete_run"]}):
            self.assertEqual(verify_interactive(self.root)["outcome"], "FAIL")
        with patch("verify_agent.verify_agent_runs", return_value={"outcome": "PASS", "runs": [run]}) as gate:
            result = verify_interactive(self.root)
            self.assertEqual(result["outcome"], "PASS", result)
            self.assertTrue(result["main_started"])
            self.assertTrue(result["process_exit_verified"])
            self.assertTrue(gate.call_args.kwargs["require_live"])
            self.stop["source_record"] = {**terminal, "source_instance": str(uuid.uuid4())}
            self.save_stop()
            self.assertEqual(verify_interactive(self.root)["outcome"], "FAIL")


if __name__ == "__main__":
    unittest.main()
