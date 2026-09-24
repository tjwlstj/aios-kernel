"""Synthetic fail-closed checks; these records are not live model evidence."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools/hosted"))
import evidence_report_contract as contract


RUN_ID = "51ad65ad-7081-4c90-8bb6-419e47f307f2"
FIRST_ID = "12345678-1234-4234-8234-123456789012"
SECOND_ID = "12345678-1234-4234-8234-123456789013"
AUDIT_TIME = "2026-09-24T03:45:39.777390+00:00"
PROPOSAL = {"action": "OBSERVE", "expected_revision": None,
            "prediction": "OBSERVED", "attribution": "SELF"}


def encode(value: dict) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()


def put(directory: Path, relative: str, value: dict) -> dict:
    raw = encode(value)
    path = directory / "evidence-source" / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"bytes": len(raw), "sha256": contract.digest(raw)}


def synthetic_source(directory: Path) -> tuple[dict, dict]:
    """Ten connected records with substitute test pins and no producer import."""
    by_condition = {condition: {"readouts": 3, "action_revision_correct": 1,
                    "attribution_correct": 0, "prediction_correct": 3,
                    "schema_valid": 3} for condition in ("C0", "C1")}
    slots = []
    rows = []
    source_hashes = {}
    readout_paths = [f"readouts/readout-{index:04d}.json" for index in range(1, 7)]
    for index, slot_id in enumerate(contract.SLOT_IDS, 1):
        sample = slot_id[:-2]
        condition = slot_id[-2:]
        score = {"action_correct_from_visible_facts": sample == "S0",
                 "revision_correct": True, "attribution_correct": False,
                 "prediction_matches_visible_expectation": True,
                 "prediction_evaluable": True, "schema_valid": True}
        slots.append({"slot_id": slot_id, "readout_path": readout_paths[index - 1],
                      "sample_id": sample, "condition": condition})
        content = "synthetic-" + slot_id
        row = {"ordinal": index, "slot_id": slot_id, "sample_id": sample,
               "condition": condition, "proposal": PROPOSAL, "static_score": score,
               "content": content,
               "model_query": {"content": content,
                               "raw_response": {"content": content, "truncated": False}}}
        rows.append({"slot_id": slot_id, "proposal": PROPOSAL, "static_score": score})
        relative = contract.SOURCE_PATHS[index + 3]
        source_hashes[relative] = put(directory, relative, row)
    design = {"run_id": RUN_ID, "plan_id": "frozen-readout-order-v1",
              "controls": {"world_effects_permitted": False,
                           "history_updates_from_new_outputs": False}, "slots": slots}
    report = {"run_id": RUN_ID, "plan_id": "frozen-readout-order-v1",
              "evaluation_scope": "STATIC_ONLY", "relationship": "RESEARCH",
              "experiment_integrity": "PASS", "kind": "static-public-context-readout",
              "completed_slots": 6, "by_condition": by_condition,
              "readout_paths": readout_paths}
    source_hashes[contract.SOURCE_PATHS[2]] = put(directory, contract.SOURCE_PATHS[2], design)
    source_hashes[contract.SOURCE_PATHS[3]] = put(directory, contract.SOURCE_PATHS[3], report)
    evidence = {name.removeprefix("build/self-reference-readout-01/"): source_hashes[name]
                for name in contract.SOURCE_PATHS[2:]}
    strict = {"outcome": "PASS", "evaluation_scope": "STATIC_ONLY",
              "relationship": "RESEARCH", "run_id": RUN_ID,
              "model_queries_verified": 6, "plan_id": "frozen-readout-order-v1",
              "files_verified": 379, "producer_experiment_integrity": "PASS",
              "scope": "Six decisions, not executed effects", "readouts": rows,
              "by_condition": by_condition, "file_evidence": evidence}
    source_hashes[contract.SOURCE_PATHS[1]] = put(directory, contract.SOURCE_PATHS[1], strict)
    audit = {"outcome": "PASS", "evaluation_scope": "STATIC_ONLY",
             "relationship": "RESEARCH", "run_id": RUN_ID,
             "errors": [], "actual_queries_observed": 6,
             "strict_replay": {key: value for key, value in strict.items()
                               if key != "file_evidence"},
             "producer_report": report,
             "before_after_unchanged": {"caller": True},
             "raw_costs_vs_strict_replay": {"matched": True},
             "scope": "Six fixed contexts; no World effects",
             "audit_finished_at": AUDIT_TIME}
    source_hashes[contract.SOURCE_PATHS[0]] = put(directory, contract.SOURCE_PATHS[0], audit)
    return source_hashes, {"audit": source_hashes[contract.SOURCE_PATHS[0]]["sha256"],
                           "strict": source_hashes[contract.SOURCE_PATHS[1]]["sha256"]}


def command(line: str, result: dict) -> dict:
    words = line.split(" ")
    return {"name": words[0], "args": words[1:], "outcome": "OK", "result": result}


def task_commands(question1: str, question2: str,
                  first: str = FIRST_ID, second: str = SECOND_ID) -> list:
    def cycle(question: str, request_id: str) -> list:
        admitted = {"request_id": request_id, "phase": "ACCEPTED", "revision": 1}
        finished = {"request_id": request_id, "phase": "FINISHED", "model_outcome": "ANSWERED"}
        return [command("ask " + question, {"request_id": request_id, "task": admitted}),
                command("task status " + request_id, {"request_id": request_id, "task": finished}),
                command("task result " + request_id, {"request_id": request_id, "task": finished})]

    return ([command(value, {}) for value in ("backend start", "agent start", "room discover",
                                               "room bind", "space")]
            + cycle(question1, first) + [command("space", {})] + cycle(question2, second)
            + [command(value, {"state": "STOPPED"}) for value in ("agent stop", "backend stop")]
            + [command("exit", {})])


class EvidenceReportContractTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)

    def test_rederives_ten_records_and_rejects_source_or_question_drift(self):
        hashes, pins = synthetic_source(self.directory)
        with patch.object(contract, "AUDIT_SHA256", pins["audit"]), \
             patch.object(contract, "STRICT_SHA256", pins["strict"]):
            e0, actual_hashes, readouts = contract.derive_e0(self.directory)
            self.assertEqual(actual_hashes, hashes)
            self.assertEqual(len(readouts), 6)
            self.assertEqual(e0["per_condition"]["C0"]["action_revision_correct"], 1)
            self.assertTrue(e0["paired_decisions_and_scores_equal"])
            self.assertEqual(e0["world_effects_executed"], 0)
            plan = {"schema_version": 1, "scenario": contract.SCENARIO,
                    "e0": e0, "question1": contract.question1(e0, readouts),
                    "source_files": hashes}
            (self.directory / "evidence-plan.json").write_bytes(encode(plan))
            self.assertEqual(contract.verify_e0_plan(self.directory)[0], plan)
            plan["question1"] += " altered"
            (self.directory / "evidence-plan.json").write_bytes(encode(plan))
            with self.assertRaisesRegex(ValueError, "question1_exact"):
                contract.verify_e0_plan(self.directory)
            source = self.directory / "evidence-source" / contract.SOURCE_PATHS[-1]
            source.write_bytes(source.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "strict_source_hash"):
                contract.derive_e0(self.directory)

    def test_frozen_question_and_fixed_report_bytes(self):
        e0 = {"run_id": RUN_ID, "audit_sha256": contract.AUDIT_SHA256,
              "observed_at": AUDIT_TIME, "world_effects_executed": 0,
              "per_condition": {condition: {"readouts": 3,
                  "action_revision_correct": 1, "attribution_correct": 0,
                  "prediction_correct": 3, "schema_valid": 3}
                  for condition in ("C0", "C1")}}
        rows = {}
        for sample in contract.SAMPLES:
            for condition in ("C0", "C1"):
                rows[sample + condition] = {"proposal": PROPOSAL, "static_score": {
                    "action_correct_from_visible_facts": sample == "S0",
                    "revision_correct": True, "attribution_correct": False,
                    "prediction_matches_visible_expectation": True,
                    "prediction_evaluable": True, "schema_valid": True}}
        question = contract.question1(e0, rows)
        self.assertEqual(len(question), 510)
        self.assertEqual(contract.digest(question.encode()),
                         "a555cc2bc906e7ee711cb0ee0b05428f7db7b1d2ec769e1d9736e5a76d9fa4ef")
        answer = "comparison: SAME\neffect: NOT_EXECUTED\nnext: SAVE\nevidence: E0\n"
        saved = contract.saved_report_bytes(RUN_ID, FIRST_ID, contract.digest(answer.encode()))
        self.assertEqual(saved.splitlines(), [b"AIOS evidence report v1",
                         ("run_id=" + RUN_ID).encode(), ("request_id=" + FIRST_ID).encode(),
                         ("response_sha256=" + contract.digest(answer.encode())).encode(),
                         b"comparison=SAME", b"effect=NOT_EXECUTED", b"next=SAVE"])
        self.assertTrue(saved.endswith(b"\n"))
        q2 = contract.question2(FIRST_ID, SECOND_ID, {"uid": 1000, "pid": 211},
                                contract.digest(saved), contract.digest(saved), len(saved))
        self.assertIn("first_task_uuid=" + FIRST_ID + " action_id=" + SECOND_ID, q2)
        self.assertIn("writer=driver(uid=1000,pid=211)", q2)

    def test_response_fields_and_two_uuid_command_plan_fail_closed(self):
        self.assertEqual(contract.first_answer(
            "comparison: SAME\neffect: NOT_EXECUTED\nnext: SAVE\nevidence: E0"),
            ("SAME", "NOT_EXECUTED", "SAVE"))
        self.assertEqual(contract.second_answer(
            "stored: CONFIRMED\nnext: FINISH\nevidence: E1\n"),
            ("CONFIRMED", "FINISH"))
        for bad in ("comparison: SAME\neffect: NOT_EXECUTED\nnext: SAVE\nevidence: E0\nextra",
                    "comparison: SAME\r\neffect: NOT_EXECUTED\r\nnext: SAVE\r\nevidence: E0",
                    "comparison: SAME\neffect: NOT_EXECUTED\nnext: SAVE\nevidence: E1"):
            with self.assertRaisesRegex(ValueError, "first_answer_format"):
                contract.first_answer(bad)
        with self.assertRaisesRegex(ValueError, "second_answer_format"):
            contract.second_answer("stored: CONFIRMED\nnext: FINISH\nevidence: E0")
        rows = task_commands("Q1", "Q2")
        self.assertEqual(contract.verify_plan(rows, "Q1", "Q2"), (FIRST_ID, SECOND_ID))
        with self.assertRaisesRegex(ValueError, "suffix_or_duplicate_task"):
            contract.verify_plan(task_commands("Q1", "Q2", second=FIRST_ID), "Q1", "Q2")
        rows.insert(-3, command("task cancel " + FIRST_ID, {}))
        with self.assertRaisesRegex(ValueError, "suffix_or_duplicate_task"):
            contract.verify_plan(rows, "Q1", "Q2")

    def test_raw_receipt_hash_and_retention_join(self):
        request = {"prompt": "P", "n_predict": 192, "temperature": 0.0,
                   "seed": 1, "cache_prompt": False, "stream": False}
        response = {"prompt": "P", "truncated": False, "content": "answer"}
        request_raw = json.dumps(request, sort_keys=True, separators=(",", ":"))
        response_raw = encode(response).decode()
        receipt = {"request_id": FIRST_ID, "outcome": "OK", "user_prompt": "Q1",
                   "content": "answer", "request_body": request_raw,
                   "response_body": response_raw,
                   "request_sha256": contract.digest(request_raw.encode()),
                   "response_sha256": contract.digest(response_raw.encode())}
        state = {"request_id": FIRST_ID, "phase": "FINISHED", "model_outcome": "ANSWERED",
                 "cancel_requested_ns": None, "worker_exit_code": 0, "user_prompt": "Q1",
                 "request_body": request_raw, "request_sha256": receipt["request_sha256"],
                 "inference_receipt": receipt}
        runs = [{"tasks": {FIRST_ID: {"revisions": [{"request_state": state}]}},
                 "requests": [dict(receipt)]}]
        self.assertEqual(contract._receipt(runs, FIRST_ID, "Q1"), (state, receipt))
        runs[0]["requests"][0]["content"] = "altered"
        with self.assertRaisesRegex(ValueError, "retained_receipt"):
            contract._receipt(runs, FIRST_ID, "Q1")
        receipt["request_sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "raw_receipt"):
            contract._receipt(runs, FIRST_ID, "Q1")

    def test_preflight_margin_and_actual_request_can_have_later_context(self):
        import space_output_contract as space

        question = "ASCII QUESTION"
        observed = {"validity": "CURRENT", "observation": {"id": 1},
                    "consumer": {"id": 2}, "checked_monotonic_ns": 100}
        admitted = {**observed, "checked_monotonic_ns": 101}

        def prompt(packet):
            context = {"checked": packet["checked_monotonic_ns"]}
            envelope = json.dumps({"space_data": context, "question": question},
                                  sort_keys=True, separators=(",", ":"))
            return (space.SPACE_PREFIX + envelope
                    + " /no_think<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n")

        def request_body(text):
            return json.dumps({"prompt": text, "n_predict": 192, "temperature": 0.0,
                               "seed": 1, "cache_prompt": False, "stream": False},
                              sort_keys=True, separators=(",", ":")).encode()

        preflight_prompt = prompt(observed)
        actual_body = request_body(prompt(admitted))
        token_request = json.dumps({"content": preflight_prompt, "add_special": True,
                                    "parse_special": True}, separators=(",", ":")).encode()
        token_dir = self.directory / "tokenize-1"
        token_dir.mkdir()
        (token_dir / "request.json").write_bytes(token_request)
        response = {"tokens_evaluated": 831}
        receipt = {"request_body": actual_body.decode(),
                   "request_sha256": contract.digest(actual_body),
                   "response_body": json.dumps(response), "space_context": admitted}
        state = {"space_context": admitted, "request_body": actual_body.decode(),
                 "request_sha256": contract.digest(actual_body)}
        actual = {"request_sha256": contract.digest(actual_body), "actual_prompt_tokens": 831}
        self.assertNotEqual(contract.digest(request_body(preflight_prompt)), actual["request_sha256"])

        def record_tokens(count, margin=24):
            token_response = json.dumps({"tokens": [1] * count}).encode()
            (token_dir / "response.json").write_bytes(token_response)
            record = {"ordinal": 1, "question_sha256": contract.digest(question.encode()),
                      "request_sha256": contract.digest(request_body(preflight_prompt)),
                      "request_bytes": len(request_body(preflight_prompt)),
                      "prompt_sha256": contract.digest(preflight_prompt.encode()),
                      "token_count": count, "response_budget": 192, "context_size": 1024,
                      "token_drift_margin": margin, "tokenize_status": 200,
                      "token_request_sha256": contract.digest(token_request),
                      "token_response_sha256": contract.digest(token_response)}
            (self.directory / "preflight-1.json").write_bytes(encode(record))

        with patch.object(space, "validate_packet", return_value=None), \
             patch.object(space, "model_context", side_effect=lambda packet:
                          {"checked": packet["checked_monotonic_ns"]}):
            record_tokens(808)
            contract._preflight(self.directory, 1, question, receipt, state, observed, actual)
            record_tokens(809)
            with self.assertRaisesRegex(ValueError, "token_preflight"):
                contract._preflight(self.directory, 1, question, receipt, state, observed, actual)
            record_tokens(808, margin=0)
            with self.assertRaisesRegex(ValueError, "preflight_record"):
                contract._preflight(self.directory, 1, question, receipt, state, observed, actual)
            record_tokens(808)
            actual["actual_prompt_tokens"] = 833
            with self.assertRaisesRegex(ValueError, "actual_input"):
                contract._preflight(self.directory, 1, question, receipt, state, observed, actual)


if __name__ == "__main__":
    unittest.main()
