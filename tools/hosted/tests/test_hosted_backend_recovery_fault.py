"""Expected-fault joins reject a different owner, child or recovery timeline.

The workflow checker consumes already-normalized backend/MAIN evidence. These
synthetic inputs exercise that join only; they are not live-model acceptance.
"""
from __future__ import annotations

import copy
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/hosted"))
sys.path.insert(0, str(ROOT / "hosted/linux"))

from aios_management.binding import Authority
from backend_recovery_contract import RECOVERY_COMMANDS, verify_recovery_workflow
from test_hosted_agent_verifier import receipt as model_receipt
from test_hosted_backend_recovery_verifier import recovery_fixture, sample
from test_hosted_backend_verifier import raw, save
from test_hosted_console import agent_source, backend as backend_reply
from verify_backend import verify_backend_runs


def fault_fixture(root):
    state, source, _paths, _receipt_path, owner = recovery_fixture(root, count=2)
    checked = verify_backend_runs(state, source, allow_recovered=True, recovery_owner=owner)
    if checked["outcome"] != "PASS":
        raise AssertionError(checked)
    # The expected-fault layer requires live-labelled normalized evidence.
    # Relabel only this disposable in-memory fixture, never the source artifacts.
    def live(value):
        if isinstance(value, list):
            return [live(item) for item in value]
        if isinstance(value, dict):
            return {key: "live" if key == "capture_kind" and item == "fixture" else
                    "llamafile-pinned" if key == "profile" and item == "python-fixture" else live(item)
                    for key, item in value.items()}
        return value
    managed = live(checked["runs"])
    old, new = managed
    lease = old["recovery"]["lease"]
    parent_id, child_id = lease["service_record"]["supervisor_identity"], lease["service_record"]["child_identity"]
    running = {"schema_version": 1, "action": "status", "outcome": "OK", "error": None,
               "state": "RUNNING", "service_kind": "MODEL_BACKEND", "capture_kind": "live",
               "service_record": copy.deepcopy(lease["service_record"]),
               "descriptor": copy.deepcopy(old["descriptor"]), "resource_actions": "UNSUPPORTED"}
    fresh = backend_reply("recover", state="FAILED", error="recovery-owner-required")
    fresh["capture_kind"] = "live"
    injector = root / "verification-source/backend_recovery_guest.py"
    injector.parent.mkdir()
    injector.write_bytes(b"# Explicit expected-fault unit fixture, not an injector execution.\n")
    proof = {"schema_version": 1, "scenario": "running-supervisor-loss", "capture_kind": "live",
             "source_only": True, "injector_source_sha256": hashlib.sha256(injector.read_bytes()).hexdigest(),
             "backend_status": running, "cli": sample(owner, 1, 1452),
             "supervisor_before": sample(parent_id, owner["process_id"], 1452),
             "child_before": sample(child_id, parent_id["process_id"], 1452),
             "child_after": sample(child_id, 1, 1465), "child_after_fresh": sample(child_id, 1, 1476),
             "signal": {"name": "SIGKILL", "via": "authenticated-pidfd", "pidfd_acquired_monotonic_ns": 1451,
                        "sent_monotonic_ns": 1463, "supervisor_exit_observed": True,
                        "supervisor_exit_monotonic_ns": 1464},
             "fresh_recover": {"process_exit_code": 1, "stdout": raw(fresh).decode(), "stderr": "", "response": fresh}}
    (root / "session").mkdir()
    save(root / "session/session.events.jsonl",
         {"event": "START", "data": {"source_process": sample(owner, 1, 900)}})

    # Build a real management transition using the existing MAIN source fixture.
    authority = Authority("00000000-0000-4000-8000-000000000014")
    authority.initialize()
    old_main = agent_source()
    authority.discover([old_main])
    authority.bind(old_main)
    invalid_main = {**old_main, "model_ready": False, "source_generation": 2}
    authority.observe(invalid_main)
    invalid = {"event": "BACKEND_INVALIDATED", "monotonic_ns": 1490,
               "source_record": invalid_main, "management_snapshot": authority.snapshot()}
    new_main = agent_source(source_instance="00000000-0000-4000-8000-000000000099", service_start_generation=2)
    authority.discover([new_main])
    authority.reconcile(new_main)
    answer = model_receipt("user")
    runs = [{"events": [invalid], "requests": [], "execution_binding": {"descriptor": old["descriptor"]}},
            {"events": [], "requests": [answer], "execution_binding": {"descriptor": new["descriptor"]}}]
    commands = []
    for index, line in enumerate(RECOVERY_COMMANDS):
        name, *args = line.split()
        commands.append({"name": name, "args": args, "outcome": "ERROR" if index in (5, 7, 8, 9) else "OK",
                         "result": {}})
    commands[0]["result"] = {**running, "action": "start"}
    commands[5]["result"] = {"error": "process-not-running"}
    commands[6]["result"] = {key: invalid[key] for key in ("source_record", "management_snapshot")}
    commands[7]["result"] = {"error": "model-not-ready", "inference_receipt": None}
    relation = {"fixture_relation": "unchanged"}
    commands[4]["result"] = {"resource_result": {"relation": relation}}
    commands[8]["result"] = {"error": "backend-unavailable", "resource_result": {
        "error": "backend-unavailable", "outcome": "ERROR", "relation_current": False,
        "observation": None, "relation": relation}}
    commands[9]["result"] = {"error": "stop-failed"}
    commands[10]["result"] = commands[11]["result"] = {"state": "RECOVERED"}
    commands[12]["result"] = {"service_record": new["events"][2]["service_record"]}
    commands[15]["result"] = {"management_snapshot": authority.snapshot()}
    commands[17]["result"] = {"inference_receipt": answer}
    commands[18]["result"] = commands[19]["result"] = {"state": "STOPPED"}
    save(root / "fault.json", proof)
    return proof, commands, runs, {"managed_runs": managed}


class BackendRecoveryFaultTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.proof, self.commands, self.runs, self.backend = fault_fixture(self.root)
        verify_recovery_workflow(self.root, self.commands, self.runs, self.backend)

    def reject(self, change, reason):
        proof, commands, runs, backend = copy.deepcopy((self.proof, self.commands, self.runs, self.backend))
        change(proof, commands, runs, backend)
        save(self.root / "fault.json", proof)
        with self.assertRaisesRegex(ValueError, reason):
            verify_recovery_workflow(self.root, commands, runs, backend)

    def test_coherent_owner_crash_orphan_recovery_and_new_generation_pass(self):
        self.assertIsNone(verify_recovery_workflow(self.root, self.commands, self.runs, self.backend))
        self.assertIsNone(self.backend["managed_runs"][0]["result"])
        self.assertEqual(self.commands[15]["result"]["management_snapshot"]["binding"]["generation"], 2)

    def test_wrong_cli_and_raw_parent_chain_are_rejected(self):
        self.reject(lambda p, *_: p.update(cli=sample({**{k: p["cli"][k] for k in
                    ("host_boot_id", "process_id", "process_start_ticks", "uid")}, "process_id": 999}, 1, 1452)),
                    "backend_recovery:fault_cli_owner")
        for role in ("supervisor_before", "child_before"):
            with self.subTest(role=role):
                def change(proof, *_):
                    current = proof[role]
                    proof[role] = sample({k: current[k] for k in ("host_boot_id", "process_id", "process_start_ticks", "uid")}, 999, 1452)
                self.reject(change, "backend_recovery:fault_parent_chain")

    def test_bool_injector_times_and_fresh_exit_are_not_integers(self):
        for name in ("pidfd_acquired_monotonic_ns", "sent_monotonic_ns", "supervisor_exit_monotonic_ns"):
            with self.subTest(name=name):
                self.reject(lambda p, *_: p["signal"].update({name: True}), "backend_recovery:fault_time_type")
        self.reject(lambda p, *_: p["fresh_recover"].update(process_exit_code=True), "backend_recovery:fresh_execution")

    def test_orphan_must_remain_the_same_live_raw_child(self):
        for role in ("child_after", "child_after_fresh"):
            with self.subTest(role=role):
                self.reject(lambda p, *_: p[role].update(raw_stat=p[role]["raw_stat"].replace(") S ", ") Z ")),
                            "resource_contract:stat_state")
                self.reject(lambda p, *_: p[role].update(raw_stat="malformed\n"), "resource_contract:stat_format")
                def replacement(proof, *_):
                    current = proof[role]
                    proof[role] = sample({**{k: current[k] for k in ("host_boot_id", "process_id", "process_start_ticks", "uid")},
                                          "process_id": 999}, 1, current["read_start_ns"])
                self.reject(replacement, "backend_recovery:orphan_child_identity")

    def test_fresh_client_success_and_stdout_response_mismatch_are_rejected(self):
        def success(proof, *_):
            value = copy.deepcopy(proof["backend_status"])
            value.update(action="recover", state="RECOVERED", descriptor=None)
            value["service_record"].update(lifecycle_state="exited", backend_ready=False)
            proof["fresh_recover"].update(response=value, stdout=raw(value).decode())
        self.reject(success, "backend_recovery:fresh_owner_rejected")
        self.reject(lambda p, *_: p["fresh_recover"].update(process_exit_code=0), "backend_recovery:fresh_execution")
        self.reject(lambda p, *_: p["fresh_recover"]["response"].update(error="recovery-unavailable"),
                    "backend_recovery:fresh_execution")

    def test_missing_wrong_or_unobserved_injector_signal_is_rejected(self):
        self.reject(lambda p, *_: p["signal"].pop("name"), "backend_recovery:fault_signal_keys")
        for field, value in (("name", "SIGTERM"), ("via", "stored-pid"), ("supervisor_exit_observed", False),
                             ("supervisor_exit_observed", 1)):
            with self.subTest(field=field, value=value):
                self.reject(lambda p, *_: p["signal"].update({field: value}), "backend_recovery:fault_signal")
        self.reject(lambda _p, _c, _r, b: b["managed_runs"][0]["recovery"]["recovery"].update(signal=None),
                    "backend_recovery:fault_recovery_signal")

    def test_premature_recovery_or_new_generation_and_duplicate_invalidation_fail(self):
        self.reject(lambda _p, _c, _r, b: b["managed_runs"][0]["recovery"]["recovery"].update(started_monotonic_ns=1480),
                    "backend_recovery:fault_time_order")
        self.reject(lambda _p, _c, r, _b: r[0]["events"][0].update(monotonic_ns=1463), "backend_recovery:recovery_order")
        self.reject(lambda _p, _c, r, _b: r[0]["events"].append(copy.deepcopy(r[0]["events"][0])),
                    "backend_recovery:no_old_model_request")
        self.reject(lambda _p, _c, _r, b: b["managed_runs"][1]["events"][0].update(monotonic_ns=1599),
                    "backend_recovery:recovery_order")

    def test_changed_injector_bytes_cannot_keep_the_old_proof_hash(self):
        (self.root / "verification-source/backend_recovery_guest.py").write_bytes(b"# different injector\n")
        with self.assertRaisesRegex(ValueError, "backend_recovery:injector_source"):
            verify_recovery_workflow(self.root, self.commands, self.runs, self.backend)

    def test_unavailable_attester_has_no_new_observation_or_relinked_relation(self):
        self.reject(lambda _p, c, *_: c[8]["result"].update(error="resource-relation-stale"),
                    "backend_recovery:stale_rejection")
        for field, value in (("error", "resource-relation-stale"), ("outcome", "OK"),
                             ("relation_current", True), ("observation", {}), ("relation", None)):
            with self.subTest(field=field):
                self.reject(lambda _p, c, *_: c[8]["result"]["resource_result"].update({field: value}),
                            "backend_recovery:unavailable_resource_observation")


if __name__ == "__main__":
    unittest.main()
