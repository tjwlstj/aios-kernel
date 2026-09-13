"""Producer-generated disk fixtures and tampering; no real model or HTTP calls."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import self_reference_contract as contract
import self_reference_lab as lab
import self_reference_model as model
import self_reference_replay as replay


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.write_bytes(contract.encoded(value) + b"\n")


def hashes(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


class ReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        cls.base = Path(cls.fixture.name)
        cls.rules, cls.actual = cls.base / "rules", cls.base / "actual"
        cls.calibration = cls.base / "calibration"
        cls.grammar = cls.base / "grammar"
        checkout = Path(lab.__file__).resolve().parents[2]
        source = {"head_sha": "494de92a5551225600c2da99648ecf377ed695e4", "dirty": True,
                  "files": {"tools/research/self_reference_" + name + ".py":
                    replay.sha((checkout / ("tools/research/self_reference_" + name + ".py")).read_bytes())
                    for name in ("world", "contract", "model", "lab", "grammar")}}
        with patch.object(lab, "source_manifest", return_value=source), contextlib.redirect_stdout(io.StringIO()):
            if lab.main(["--artifacts", str(cls.rules), "--rules-only"]) != 0:
                raise AssertionError("rules producer failed")
            process = Mock(pid=1234, returncode=None)
            process.poll.side_effect = lambda: process.returncode

            def wait(timeout):
                process.returncode = 1
                return 1

            process.wait.side_effect = wait
            original_record = model._file_record

            def record(path):
                if path.name in replay.PINS:
                    size, digest = replay.PINS[path.name]
                    return {"path": str(path), "bytes": size, "sha256": digest}
                return original_record(path)

            completion_count = 0

            def exchange(endpoint, payload, deadline, limit):
                nonlocal completion_count
                if endpoint == "/health":
                    return 200, b'{"status":"ok"}'
                if endpoint == "/tokenize":
                    return 200, contract.encoded({"tokens": list(range(100))})
                request = json.loads(payload)
                user = request["prompt"].split("<|im_start|>user\n", 1)[1].split(" /no_think<|im_end|>", 1)[0]
                facts = json.loads(user)
                context = facts.get("related_facts")
                if context is None:
                    context = contract.restore_facts(facts["path_value_facts"])
                completion_count += 1
                response = {"content": contract.encoded(lab.rules_proposal(context)).decode("utf-8"),
                    "model": model.MODEL_ID, "prompt": request["prompt"], "tokens_predicted": 30,
                    "tokens_evaluated": 100, "truncated": False, "stop": True, "stop_type": "eos"}
                response["generation_settings"] = {"seed": 1, "temperature": 0.0,
                    "n_predict": 192, "max_tokens": 192, "stream": False, "grammar": request.get("grammar", ""),
                    "grammar_lazy": False, "lora": [],
                    "top_k": 40, "top_p": 0.949999988079071, "min_p": 0.05000000074505806,
                    "repeat_penalty": 1.0, "samplers": ["top_k", "top_p", "min_p", "temperature"]}
                if "grammar" in request:
                    response["content"] = model._encoded(lab.rules_proposal(context)).decode("utf-8")
                elif completion_count == 1:
                    response["content"] = 'JSON is not available: {"action":"SET"}'
                return 200, contract.encoded(response)

            with patch.object(model, "_file_record", side_effect=record), \
                    patch.object(model.subprocess, "Popen", return_value=process), \
                    patch.object(model.LocalModel, "_exchange", side_effect=exchange), \
                    patch.object(model.socket, "socket") as reservation:
                reservation.return_value.__enter__.return_value.getsockname.return_value = ("127.0.0.1", 56789)
                if lab.main(["--artifacts", str(cls.actual), "--cache", str(cls.base / "cache")]) != 0:
                    raise AssertionError("mocked actual producer failed")
                process.returncode = None
                completion_count = 0
                if lab.main(["--artifacts", str(cls.calibration), "--cache", str(cls.base / "cache"), "--calibration"]) != 0:
                    raise AssertionError("mocked calibration producer failed")
                process.returncode = None
                completion_count = 0
                if lab.main(["--artifacts", str(cls.grammar), "--cache", str(cls.base / "cache"), "--calibration", "--grammar"]) != 0:
                    raise AssertionError("mocked grammar producer failed")

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "relocated"

    def fixture_copy(self, actual=False, calibration=False, grammar=False):
        shutil.copytree(self.grammar if grammar else self.calibration if calibration else self.actual if actual else self.rules, self.root)
        return self.root

    def reject(self, reason):
        result = replay.verify_artifacts(self.root)
        self.assertEqual(result["outcome"], "FAIL", result)
        self.assertIn(reason, result["reason"], result)

    def decision(self, actual=False, number=1):
        return self.root / ("00-normal-flat" if actual else "00-normal-rules") / ("decision-%03d.json" % number)

    def repair_decision_hash(self, path):
        episode_path = path.parent / "episode.json"
        episode = read(episode_path)
        row = next(row for row in episode["decisions"] if row["decision_file"] == path.name)
        row["decision_sha256"] = replay.sha(path.read_bytes())
        write(episode_path, episode)

    def change_response(self, transform, number=1):
        path = self.root / ("model/query-%04d/completion.response.json" % number)
        response = read(path)
        transform(response)
        write(path, response)
        write(path.with_name("completion.http.json"), {"status": 200, "response_bytes": path.stat().st_size})
        decision_path = self.decision(actual=True, number=number)
        decision = read(decision_path)
        query = decision["model_query"]
        query.update(raw_response=response, response_sha256=replay.sha(path.read_bytes()))
        write(decision_path, decision)
        self.repair_decision_hash(decision_path)
        write(path.with_name("result.json"), {"outcome": "VALID", **query})

    def test_rules_replays_readonly_and_relocates(self):
        self.fixture_copy()
        before = hashes(self.root)
        result = replay.verify_artifacts(self.root)
        self.assertEqual(result["outcome"], "PASS", result)
        self.assertEqual(result["episodes_verified"], 6)
        self.assertEqual(result["decisions_verified"], 27)
        self.assertFalse(result["actual_model_evidence_verified"])
        self.assertEqual(result["source_files_verified"], 5)
        self.assertEqual(result["replay_consumer_sha256"], replay.sha(Path(replay.__file__).read_bytes()))
        self.assertEqual(before, hashes(self.root))

    def test_mocked_actual_replays_all_raw_queries_and_exit_one_cleanup(self):
        self.fixture_copy(actual=True)
        before = hashes(self.root)
        result = replay.verify_artifacts(self.root)
        self.assertEqual(result["outcome"], "PASS", result)
        self.assertEqual(result["episodes_verified"], 18)
        self.assertEqual(result["model_queries_verified"], 55)
        self.assertEqual(result["arms"]["flat"]["counts"]["schema_valid"], 27)
        self.assertEqual(result["arms"]["flat"]["counts"]["decisions"], 28)
        self.assertEqual(result["model_lifecycle"]["returncode"], 1)
        self.assertEqual(result["model_lifecycle"]["termination_kind"], "host_termination")
        self.assertEqual(before, hashes(self.root))

    def test_calibration_has_exact_normal_only_three_episode_plan(self):
        self.fixture_copy(calibration=True)
        result = replay.verify_artifacts(self.root)
        self.assertEqual(result["outcome"], "PASS", result)
        self.assertEqual(result["stage"], "calibration")
        self.assertEqual(result["episodes_verified"], 3)
        self.assertEqual(result["model_queries_verified"], 9)
        self.assertEqual(result["hypothesis_verdict"], "NOT_EVALUATED_CALIBRATION")
        self.assertEqual({key: arm["episodes"] for key, arm in result["arms"].items()},
                         {"flat": 1, "relational": 1, "rules": 1})

    def test_stage_cannot_relabel_the_six_scenario_plan(self):
        self.fixture_copy()
        path = self.root / "design.json"
        value = read(path)
        value["stage"] = "calibration"
        write(path, value)
        self.reject("experiment-scenarios")

    def test_calibration_cannot_claim_hypothesis_evaluation(self):
        self.fixture_copy(calibration=True)
        path = self.root / "report.json"
        value = read(path)
        value["hypothesis_verdict"] = "PILOT_RESULTS_REQUIRE_REVIEW"
        write(path, value)
        self.reject("report-hypothesis")

    def test_static_grammar_run_replays_exact_request_response_and_source(self):
        self.fixture_copy(grammar=True)
        result = replay.verify_artifacts(self.root)
        self.assertEqual(result["outcome"], "PASS", result)
        self.assertEqual(result["model_queries_verified"], 8)
        self.assertEqual(result["generation_settings"]["grammar"], model.DECISION_GRAMMAR)
        self.assertIs(result["generation_settings"]["grammar_lazy"], False)

    def test_static_grammar_hash_is_required(self):
        self.fixture_copy(grammar=True)
        path = self.root / "design.json"
        value = read(path)
        value["controls"]["grammar_sha256"] = "0" * 64
        write(path, value)
        self.reject("experiment-controls")

    def test_rules_only_cannot_declare_static_grammar_with_correct_hash(self):
        self.fixture_copy()
        path = self.root / "design.json"
        value = read(path)
        value["controls"].update(output_constraint="static-decision-v1",
                                grammar_sha256=replay.sha(model.DECISION_GRAMMAR.encode("utf-8")))
        write(path, value)
        self.reject("rules-only-output-constraint")

    def test_grammar_mode_cannot_be_silently_disabled(self):
        self.fixture_copy(grammar=True)
        self.change_response(lambda response: response["generation_settings"].update(grammar=""))
        self.reject("generation-settings-fixed")

    def test_lazy_grammar_cannot_pass(self):
        self.fixture_copy(grammar=True)
        self.change_response(lambda response: response["generation_settings"].update(grammar_lazy=True))
        self.reject("generation-settings-fixed")

    def test_grammar_word_stop_cannot_pass(self):
        self.fixture_copy(grammar=True)
        self.change_response(lambda response: response.update(stop_type="word"))
        self.reject("completion-contract")

    def test_grammar_echo_cannot_hide_nonconforming_output(self):
        self.fixture_copy(grammar=True)
        self.change_response(lambda response: response.update(content='JSON: {"action":"OBSERVE"}'))
        path = self.decision(actual=True)
        decision = read(path)
        decision["content"] = decision["model_query"]["raw_response"]["content"]
        decision["model_query"]["content"] = decision["content"]
        write(path, decision)
        self.repair_decision_hash(path)
        write(self.root / "model/query-0001/result.json", {"outcome": "VALID", **decision["model_query"]})
        self.reject("grammar-output-contract")

    def test_grammar_source_copy_tamper_rejected(self):
        self.fixture_copy(grammar=True)
        path = self.root / "verification-source/tools/research/self_reference_grammar.py"
        path.write_bytes(path.read_bytes().replace(b"static-decision-v1", b"static-decision-v2"))
        self.reject("retained-source-hash")

    def test_public_prediction_aggregate_is_recomputed(self):
        self.fixture_copy()
        path = self.root / "report.json"
        value = read(path)
        value["arms"]["rules"]["counts"]["prediction_evaluable"] += 1
        write(path, value)
        self.reject("report-aggregate")

    def test_content_is_parsed_not_replaced_by_saved_proposal(self):
        self.fixture_copy()
        path = self.decision()
        value = read(path)
        value["content"] = '{"action":"FINISH","expected_revision":null,"prediction":"NOOP","attribution":"UNKNOWN"}'
        write(path, value)
        self.repair_decision_hash(path)
        self.reject("decision-content-proposal")

    def test_duplicate_decision_keys_rejected(self):
        self.fixture_copy()
        path = self.decision()
        path.write_bytes(path.read_bytes().rstrip()[:-1] + b',"step":1}')
        self.reject("json-duplicate-key")

    def test_flat_context_roundtrip_not_just_saved_hash(self):
        self.fixture_copy(actual=True)
        path = self.decision(actual=True)
        value = read(path)
        value["user"] = '{"path_value_facts":[{"path":["identity"],"value":{}}]}'
        write(path, value)
        self.reject("decision-user-roundtrip")

    def test_fabricated_history_with_recomputed_context_hash_rejected(self):
        self.fixture_copy()
        path = self.decision(number=2)
        value = read(path)
        value["context"]["history"] = []
        value["context_sha256"] = contract.digest(value["context"])
        value["user"] = contract.render_context(value["context"], "relational")
        write(path, value)
        self.reject("decision-context-history")

    def change_context(self, transform, number=2):
        path = self.decision(number=number)
        value = read(path)
        transform(value["context"])
        value["context_sha256"] = contract.digest(value["context"])
        value["user"] = contract.render_context(value["context"], "relational")
        write(path, value)
        self.repair_decision_hash(path)

    def test_projected_feedback_keeps_raw_evidence_and_own_previous_prediction(self):
        self.fixture_copy()
        event = read(self.root / "00-normal-rules/world/event-001.json")
        context = read(self.decision(number=2))["context"]
        self.assertEqual(event["result"]["attribution"], "UNKNOWN")
        self.assertNotIn("attribution", context["last_result"])
        self.assertNotIn("attribution", context["history"][0]["result"])
        self.assertEqual(context["history"][0]["proposal"]["attribution"], "UNKNOWN")
        later = read(self.decision(number=4))["context"]
        self.assertEqual(later["observation"]["last_writer"], later["identity"])
        self.assertEqual(replay.verify_artifacts(self.root)["outcome"], "PASS")

    def test_derived_last_result_label_leak_rejected_after_rehash(self):
        self.fixture_copy()
        self.change_context(lambda context: context["last_result"].update(attribution="UNKNOWN"))
        self.reject("decision-context-history")

    def test_derived_history_result_label_leak_rejected_after_rehash(self):
        self.fixture_copy()
        self.change_context(lambda context: context["history"][0]["result"].update(attribution="UNKNOWN"))
        self.reject("decision-context-history")

    def test_projection_cannot_drop_raw_writer_identity(self):
        self.fixture_copy()
        self.change_context(lambda context: context["observation"].pop("last_writer"), number=4)
        self.reject("decision-context-history")

    def test_flat_user_cannot_reintroduce_derived_label(self):
        self.fixture_copy(actual=True)
        path = self.decision(actual=True, number=2)
        value = read(path)
        user = json.loads(value["user"])
        user["path_value_facts"].append({"path": ["last_result", "attribution"], "value": "UNKNOWN"})
        value["user"] = contract.encoded(user).decode("utf-8")
        write(path, value)
        self.repair_decision_hash(path)
        self.reject("decision-user-roundtrip")

    def test_feedback_projection_control_is_required(self):
        self.fixture_copy()
        path = self.root / "design.json"
        value = read(path)
        value["controls"].pop("feedback_projection")
        write(path, value)
        self.reject("experiment-controls")

    def test_fabricated_score_rejected(self):
        self.fixture_copy()
        path = self.decision()
        value = read(path)
        value["score_from_visible_facts"]["attribution_correct"] = False
        write(path, value)
        self.reject("decision-score")

    def test_episode_totals_recomputed(self):
        self.fixture_copy()
        path = self.root / "00-normal-rules/episode.json"
        value = read(path)
        value["counts"]["correct_action"] += 1
        write(path, value)
        self.reject("episode-summary")

    def test_report_totals_recomputed(self):
        self.fixture_copy()
        path = self.root / "report.json"
        value = read(path)
        value["arms"]["rules"]["counts"]["correct_action"] += 1
        write(path, value)
        self.reject("report-aggregate")

    def test_event_chain_is_replayed(self):
        self.fixture_copy()
        path = self.root / "00-normal-rules/world/event-001.json"
        value = read(path)
        value["result"]["outcome"] = "APPLIED"
        value["event_sha256"] = replay.sha(contract.encoded({key: item for key, item in value.items() if key != "event_sha256"}) + b"\n")
        write(path, value)
        self.reject("action-result")

    def test_raw_completion_hash_rejected(self):
        self.fixture_copy(actual=True)
        path = self.root / "model/query-0001/completion.response.json"
        raw = path.read_bytes().replace(b'"truncated":false', b'"truncated": true')
        self.assertEqual(len(raw), path.stat().st_size)
        path.write_bytes(raw)
        self.reject("completion-response-hash")

    def test_consistently_rehashed_truncated_completion_rejected(self):
        self.fixture_copy(actual=True)
        self.change_response(lambda response: response.update(truncated=True))
        self.reject("completion-contract")

    def test_consistently_rehashed_wrong_prompt_rejected(self):
        self.fixture_copy(actual=True)
        self.change_response(lambda response: response.update(prompt="different prompt"))
        self.reject("completion-contract")

    def test_context_overflow_rejected_even_with_valid_receipt(self):
        self.fixture_copy(actual=True)
        self.change_response(lambda response: response.update(tokens_evaluated=2048))
        self.reject("completion-token-budget")

    def test_missing_generation_settings_rejected(self):
        self.fixture_copy(actual=True)
        self.change_response(lambda response: response.pop("generation_settings"))
        self.reject("generation-settings-required")

    def test_changed_applied_temperature_rejected(self):
        self.fixture_copy(actual=True)
        self.change_response(lambda response: response["generation_settings"].update(temperature=1.0))
        self.reject("generation-settings-temperature")

    def test_changed_applied_seed_rejected(self):
        self.fixture_copy(actual=True)
        self.change_response(lambda response: response["generation_settings"].update(seed=2))
        self.reject("generation-settings-fixed")

    def test_hidden_lora_rejected(self):
        self.fixture_copy(actual=True)
        self.change_response(lambda response: response["generation_settings"].update(lora=[{"id": 1, "scale": 1.0}]))
        self.reject("generation-settings-fixed")

    def test_hidden_grammar_rejected(self):
        self.fixture_copy(actual=True)
        self.change_response(lambda response: response["generation_settings"].update(grammar="root ::= []"))
        self.reject("generation-settings-fixed")

    def test_sampling_drift_between_calls_rejected(self):
        self.fixture_copy(actual=True)
        self.change_response(lambda response: response["generation_settings"].update(top_k=10), number=2)
        self.reject("generation-settings-drift")

    def test_original_paths_are_checked_not_opened(self):
        self.fixture_copy(actual=True)
        path = self.decision(actual=True)
        value = read(path)
        value["model_query"]["raw_response_path"] = str(self.root.parent / "outside.json")
        write(path, value)
        self.reject("model-query-path")

    def test_rules_cannot_claim_actual_execution(self):
        self.fixture_copy()
        path = self.root / "report.json"
        value = read(path)
        value["actual_model_executed"] = True
        write(path, value)
        self.reject("report-actual-model-executed")

    def test_unreaped_backend_rejected(self):
        self.fixture_copy(actual=True)
        path = self.root / "model/cleanup.json"
        value = read(path)
        value["reaped"] = False
        write(path, value)
        self.reject("model-cleanup")

    def test_log_hash_rejected(self):
        self.fixture_copy(actual=True)
        (self.root / "model/backend-stderr.log").write_bytes(b"unrecorded output")
        self.reject("model-log-hash")

    def test_source_copy_tamper_rejected(self):
        self.fixture_copy()
        path = self.root / "verification-source/tools/research/self_reference_contract.py"
        path.write_bytes(path.read_bytes() + b"\n# changed\n")
        self.reject("retained-source-hash")

    def test_orphan_query_rejected(self):
        self.fixture_copy()
        folder = self.root / "model/query-9999"
        folder.mkdir(parents=True)
        write(folder / "completion.http.json", {"status": 200, "response_bytes": 1})
        self.reject("unreferenced-artifacts")

    def test_missing_episode_and_unsafe_report_path_rejected(self):
        self.fixture_copy()
        path = self.root / "report.json"
        value = read(path)
        value["episode_paths"][0] = "../outside/episode.json"
        write(path, value)
        self.reject("report-episode-paths")

    def test_incomplete_run_remains_not_evaluable(self):
        self.fixture_copy()
        path = self.root / "report.json"
        value = read(path)
        value.update(experiment_integrity="FAIL", failure={"type": "ModelError", "reason": "incomplete_generation"})
        write(path, value)
        result = replay.verify_artifacts(self.root)
        self.assertEqual(result["outcome"], "NOT_EVALUABLE")
        self.assertEqual(result["producer_experiment_integrity"], "FAIL")
        self.assertFalse(result["partial_evidence_replayed"])

    def test_cli_stdout_and_exit_status(self):
        self.fixture_copy()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = replay.main(["--artifacts", str(self.root)])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(output.getvalue())["outcome"], "PASS")


if __name__ == "__main__":
    unittest.main()
