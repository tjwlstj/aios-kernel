"""Synthetic boundary tests; these fixtures are not actual-model evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/hosted"))
import evidence_report_plan as plan


def encoded(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def put(root: Path, relative: str, raw: bytes) -> dict:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"bytes": len(raw), "sha256": sha(raw)}


def fixture(root: Path, *, audit_outcome="PASS", changed_proposal=False):
    readout_dir = root / plan.READOUT_REL
    audit_dir = root / plan.AUDIT_REL
    source = {}
    for index in range(19):
        relative = f"tools/research/source-{index:02d}.py"
        source[relative] = put(root, relative, f"source-{index}".encode())["sha256"]
    caller = {"exit_code": 0, "sources_unchanged": True, "driver_unchanged": True,
              "source_before": source, "source_after": source}
    caller_raw = encoded(caller)
    put(root, plan.CALLER_REL, caller_raw)

    counts = {condition: {"readouts": 3, "action_revision_correct": 1,
                          "attribution_correct": 0, "prediction_correct": 3,
                          "schema_valid": 3} for condition in plan.CONDITIONS}
    report = {"run_id": plan.RUN_ID, "experiment_integrity": "PASS",
              "evaluation_scope": "STATIC_ONLY", "planned_slots": 6, "completed_slots": 6,
              "failure": None, "readout_paths": list(plan.READOUT_FILES),
              "by_condition": counts,
              "query_accounting": {"reserved": 6, "returned": 6, "raised": 0,
                  "unfinished": 0, "completion_http_records": 6,
                  "completion_http_responses": 6, "completion_http_200": 6,
                  "completion_transport_errors": 0, "without_readout": 0}}
    design = {"run_id": plan.RUN_ID, "plan_id": "frozen-readout-order-v1",
              "controls": {"world_effects_permitted": False,
                           "history_updates_from_new_outputs": False}}
    evidence = {"design.json": put(readout_dir, "design.json", encoded(design)),
                "report.json": put(readout_dir, "report.json", encoded(report))}
    replay_rows = []
    for index, (relative, slot) in enumerate(zip(plan.READOUT_FILES, plan.SLOT_ORDER), 1):
        sample, condition = slot[:-2], slot[-2:]
        proposal = {"action": "OBSERVE", "expected_revision": None,
                    "prediction": "OBSERVED", "attribution": "SELF"}
        if changed_proposal and slot == "S11C1":
            proposal["action"] = "SET"
        score = {"action_correct_from_visible_facts": sample == "S0",
                 "revision_correct": True, "attribution_correct": False,
                 "prediction_evaluable": True,
                 "prediction_matches_visible_expectation": True, "schema_valid": True}
        tokens = {"prompt": 100, "predicted": 26, "tokenized_prompt": 100}
        row = {"ordinal": index, "slot_id": slot, "sample_id": sample,
               "condition": condition, "proposal": proposal, "static_score": score,
               "model_query": {"tokens": tokens}}
        evidence[relative] = put(readout_dir, relative, encoded(row))
        replay_rows.append({"slot_id": slot, "proposal": proposal,
                            "static_score": score, "tokens": tokens})
    strict = {"outcome": "PASS", "evaluation_scope": "STATIC_ONLY",
              "run_id": plan.RUN_ID, "files_verified": 379,
              "model_queries_verified": 6, "producer_experiment_integrity": "PASS",
              "scope": "Six decisions, not executed effects", "readouts": replay_rows,
              "by_condition": counts, "query_accounting": report["query_accounting"],
              "file_evidence": evidence}
    strict_raw = encoded(strict)
    put(audit_dir, "strict-replay.json", strict_raw)
    audit = {"outcome": audit_outcome, "evaluation_scope": "STATIC_ONLY",
             "run_id": plan.RUN_ID, "replay_exit_code": 0,
             "actual_queries_observed": 6, "errors": [],
             "scope": "Six fixed contexts; no World effects",
             "strict_replay": {key: value for key, value in strict.items()
                               if key != "file_evidence"},
             "producer_report": report,
             "raw_costs_vs_strict_replay": {"matched": True},
             "before_after_unchanged": {"artifact": True, "caller": True},
             "audit_finished_at": "2026-09-24T03:45:39.777390+00:00"}
    audit_raw = encoded(audit)
    put(audit_dir, "verification.json", audit_raw)
    pins = {"audit": sha(audit_raw), "strict": sha(strict_raw), "caller": sha(caller_raw)}
    return readout_dir, audit_dir, root / plan.CALLER_REL, pins


class EvidenceReportPlanTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_binds_source_and_builds_unlabelled_bounded_question(self):
        readout, audit, caller, pins = fixture(self.root)
        value = plan._build_plan(readout, audit, caller, self.root, pins, plan.RUN_ID)
        self.assertEqual(set(value), {"schema_version", "scenario", "e0",
                                      "question1", "source_files"})
        self.assertEqual(len(value["source_files"]), 10)
        self.assertEqual(value["e0"]["run_id"], plan.RUN_ID)
        self.assertTrue(value["e0"]["paired_decisions_and_scores_equal"])
        self.assertEqual(value["e0"]["world_effects_executed"], 0)
        self.assertIn("C0 n=3 ar=1 attr=0 pred=3 schema=3", value["question1"])
        self.assertIn("C1 n=3 ar=1 attr=0 pred=3 schema=3", value["question1"])
        self.assertNotIn("comparison: SAME;", value["question1"])
        self.assertNotIn("effect: NOT_EXECUTED;", value["question1"])
        self.assertLessEqual(len("ask " + value["question1"]), 2048)
        self.assertLessEqual(len(value["question1"].encode()), 4096)
        output = self.root / "plan.json"
        written = plan.write_plan(output, value)
        self.assertEqual(written["sha256"], sha(output.read_bytes()))
        with self.assertRaises(FileExistsError):
            plan.write_plan(output, value)

    def test_mutated_readout_rejected_by_file_evidence(self):
        readout, audit, caller, pins = fixture(self.root)
        with (readout / plan.READOUT_FILES[0]).open("ab") as stream:
            stream.write(b" ")
        with self.assertRaisesRegex(ValueError, "file-evidence"):
            plan._build_plan(readout, audit, caller, self.root, pins, plan.RUN_ID)

    def test_source_drift_rejected_even_with_preserved_audit(self):
        readout, audit, caller, pins = fixture(self.root)
        (self.root / "tools/research/source-00.py").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "live-source"):
            plan._build_plan(readout, audit, caller, self.root, pins, plan.RUN_ID)

    def test_failed_audit_rejected_even_when_hash_is_known(self):
        readout, audit, caller, pins = fixture(self.root, audit_outcome="FAIL")
        with self.assertRaisesRegex(ValueError, "audit-verdict"):
            plan._build_plan(readout, audit, caller, self.root, pins, plan.RUN_ID)

    def test_compact_question_rejects_nonuniform_proposals(self):
        readout, audit, caller, pins = fixture(self.root, changed_proposal=True)
        with self.assertRaisesRegex(ValueError, "compact-question-proposal-coverage"):
            plan._build_plan(readout, audit, caller, self.root, pins, plan.RUN_ID)


if __name__ == "__main__":
    unittest.main()
