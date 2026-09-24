"""Static readout replay with synthetic origin and mocked model I/O only.

Test-only origin pin patches do not qualify the actual frozen origin or model.
Production has no alternate pin CLI. The real six-query audit is a separate lane.
"""
import ast
import contextlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import self_reference_readout_replay as consumer

contract = consumer.contract


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.write_bytes(contract.encoded(value) + b"\n")


def hashes(root):
    return {path.relative_to(root).as_posix(): consumer.sha(path.read_bytes())
            for path in root.rglob("*") if path.is_file()}


class ReadoutPrimitiveTests(unittest.TestCase):
    def test_production_pins_are_fixed_literals(self):
        self.assertEqual(consumer.PLAN_ID, "frozen-readout-order-v1")
        self.assertEqual(consumer.ORIGIN_MANIFEST_SHA256,
            "6575e701107dd78a16d3a254a15a02abed8989a4295b3b53e5322965627e6ed8")
        self.assertEqual(consumer.DECISION_PINS, {
            "S0": "24c27fd755ca538dc4deadebee5f1d090018e5197e39448316e3c854bae6c4a8",
            "S1": "5d1c684ff91a0c9fc16f06ed17318be5fb9429cb8edeeb2daa0f63ce725462f5",
            "S11": "e15f63d9263d2f94bf56f182dc1626f21186ae130dfc81902f8048827dd309ef"})
        self.assertEqual(consumer.SLOT_ORDER, (("S0", "C0"), ("S0", "C1"), ("S1", "C1"),
            ("S1", "C0"), ("S11", "C0"), ("S11", "C1")))
        self.assertEqual(len(consumer.SOURCE_FILES), 6)

    def test_consumer_imports_no_producer_world_or_model(self):
        tree = ast.parse(Path(consumer.__file__).read_text(encoding="utf-8"))
        names = [alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names]
        names += [node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)]
        self.assertFalse(set(names) & {"self_reference_readout", "self_reference_model", "self_reference_lab",
                                     "self_reference_world", "subprocess", "importlib", "runpy"})

    def test_renderer_preserves_nested_utf8_and_only_moves_one_member(self):
        context = {key: None for key in consumer.ORIGINAL_ORDER}
        context.update(channel={"enabled": True}, goal={"value": 7},
            history=[{"proposal": {"text": '중첩,문자 {"x":2} \\\"', "revision": 0}}],
            observation={"owner": {"id": "가"}, "value": 0, "last_writer": None})
        original = contract.render_context(context, "relational")
        changed = consumer.render_variant(original, "C1")
        self.assertEqual(consumer.render_variant(original, "C0").encode(), original.encode())
        self.assertNotEqual(changed, original)
        self.assertEqual(contract.encoded(json.loads(changed)), contract.encoded(json.loads(original)))
        self.assertEqual(list(json.loads(changed)["related_facts"]),
            ["channel", "goal", "observation", "history", "identity", "last_result",
             "last_set_step", "schema_version", "step"])
        for name, value in context.items():
            fragment = json.dumps(name) + ":" + contract.encoded(value).decode()
            self.assertIn(fragment, original)
            self.assertIn(fragment, changed)

    def test_renderer_rejects_duplicate_and_noncanonical_input(self):
        context = {key: None for key in consumer.ORIGINAL_ORDER}
        original = contract.render_context(context, "relational")
        for user in (original.replace('"step":null', '"step":null,"step":null'), " " + original,
                     original.replace('"step":null', '"step":NaN')):
            with self.subTest(user=user):
                with self.assertRaises(ValueError):
                    consumer.render_variant(user, "C1")

    def test_exact_static_grammar_language_accepts_all_504_choices(self):
        count = 0
        for action in ("SET", "OBSERVE", "WAIT", "FINISH"):
            for revision in (range(25) if action == "SET" else (None,)):
                for prediction in sorted(contract.PREDICTIONS):
                    for attribution in sorted(contract.ATTRIBUTIONS):
                        proposal = {"action": action, "expected_revision": revision,
                                    "prediction": prediction, "attribution": attribution}
                        content = json.dumps(proposal, separators=(",", ":"))
                        self.assertEqual(consumer.grammar_proposal(content), proposal)
                        count += 1
        self.assertEqual(count, 504)

    def test_schema_valid_but_non_grammar_output_rejected(self):
        proposal = {"action": "OBSERVE", "expected_revision": None, "prediction": "OBSERVED", "attribution": "UNKNOWN"}
        valid = json.dumps(proposal, separators=(",", ":"))
        for content in (" " + valid, valid + "\n", json.dumps(proposal),
                        contract.encoded(proposal).decode(), valid.replace('"OBSERVE"', '"OBS\\u0045RVE"')):
            with self.subTest(content=content):
                with self.assertRaises(ValueError):
                    consumer.grammar_proposal(content)


class ReadoutReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from readout_fixture import produce, patched_origin
        cls.produce, cls.patched_origin = staticmethod(produce), staticmethod(patched_origin)
        cls.temp = tempfile.TemporaryDirectory(prefix="readout-consumer-")
        cls.fixture = Path(cls.temp.name) / "fixture"
        if produce(cls.fixture) != 0:
            raise AssertionError("mocked readout producer failed")
        cls.origin = cls.fixture.parent / (cls.fixture.name + "-origin")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.temp_copy = tempfile.TemporaryDirectory(prefix="readout-tamper-")
        self.addCleanup(self.temp_copy.cleanup)
        self.root = Path(self.temp_copy.name) / "relocated"
        shutil.copytree(self.fixture, self.root)

    def verify(self):
        with self.patched_origin(self.origin, consumer):
            return consumer.verify_artifacts(self.root)

    def produce_variant(self, root, failure):
        shutil.copytree(self.origin, root.parent / (root.name + "-origin"))
        return self.produce(root, failure=failure)

    def reject(self, reason=None):
        result = self.verify()
        self.assertEqual(result["outcome"], "FAIL", result)
        if reason:
            self.assertIn(reason, result["reason"], result)

    def document(self, name, change):
        path = self.root / name
        value = read(path)
        change(value)
        write(path, value)

    def rebind(self, number):
        folder = self.root / ("model/query-%04d" % number)
        result = read(folder / "result.json")
        response_raw = (folder / "completion.response.json").read_bytes()
        response = json.loads(response_raw)
        result.update(raw_response=response, response_sha256=consumer.sha(response_raw), content=response["content"],
                      request_sha256=consumer.sha((folder / "completion.request.json").read_bytes()))
        write(folder / "result.json", result)
        write(folder / "completion.http.json", {"status": 200, "response_bytes": len(response_raw)})
        start_path = self.root / ("ledger/query-%04d.start.json" % number)
        finish_path = self.root / ("ledger/query-%04d.finish.json" % number)
        finish = read(finish_path)
        finish.update(start_sha256=consumer.sha(start_path.read_bytes()),
                      result_sha256=consumer.sha((folder / "result.json").read_bytes()))
        write(finish_path, finish)
        path = self.root / ("readouts/readout-%04d.json" % number)
        record = read(path)
        record.update(start_sha256=consumer.sha(start_path.read_bytes()), finish_sha256=consumer.sha(finish_path.read_bytes()),
                      model_query={key: value for key, value in result.items() if key != "outcome"}, content=response["content"])
        write(path, record)

    def replace_prompt(self, number, change):
        folder = self.root / ("model/query-%04d" % number)
        request = read(folder / "completion.request.json")
        request["prompt"] = change(request["prompt"])
        write(folder / "completion.request.json", request)
        self.document(str((folder / "tokenize.request.json").relative_to(self.root)),
                      lambda value: value.__setitem__("content", request["prompt"]))
        self.document(str((folder / "completion.response.json").relative_to(self.root)),
                      lambda value: value.__setitem__("prompt", request["prompt"]))
        self.rebind(number)

    def test_complete_synthetic_six_slots_replay_and_no_writes(self):
        before = hashes(self.root)
        result = self.verify()
        self.assertEqual(result["outcome"], "PASS", result)
        self.assertEqual(result["model_queries_verified"], 6)
        self.assertEqual(result["source_files_verified"], 6)
        self.assertEqual(result["origin_files_verified"], 289)
        self.assertEqual(result["evaluation_scope"], "STATIC_ONLY")
        self.assertEqual(result["query_accounting"]["reserved"], 6)
        self.assertEqual([row["slot_id"] for row in result["readouts"]], ["S0C0", "S0C1", "S1C1", "S1C0", "S11C0", "S11C1"])
        self.assertEqual(before, hashes(self.root))

    def test_production_pins_reject_synthetic_origin_without_patch(self):
        result = consumer.verify_artifacts(self.root)
        self.assertEqual(result["outcome"], "FAIL", result)
        self.assertIn("origin-manifest-pin", result["reason"])

    def test_origin_unselected_raw_file_tamper_rejected(self):
        path = self.root / "origin/model/backend-stdout.log"
        path.write_bytes(path.read_bytes() + b"tamper")
        self.reject("origin-manifest-pin")

    def test_origin_selected_decision_tamper_and_rehash_manifest_rejected(self):
        name = "origin/00-normal-action-first-public-feedback-v1/decision-002.json"
        self.document(name, lambda value: value["context"]["observation"].__setitem__("last_writer", value["context"]["identity"]))
        files = hashes(self.root / "origin")
        write(self.root / "origin-manifest.json", {"schema_version": 1, "file_count": 289,
              "manifest_sha256": contract.digest(files), "files": files})
        self.reject("origin-manifest-pin")

    def test_wrong_selected_pin_rejected_even_with_valid_origin_manifest(self):
        with self.patched_origin(self.origin, consumer):
            with patch.dict(consumer.DECISION_PINS, {"S0": "0" * 64}):
                result = consumer.verify_artifacts(self.root)
        self.assertEqual(result["outcome"], "FAIL", result)
        self.assertIn("origin-decision-pin", result["reason"])

    def test_origin_stored_pass_verdict_cannot_replace_replay(self):
        self.document("origin-validation.json", lambda value: value.__setitem__("model_queries_verified", 23))
        self.reject("origin-retained-validation")

    def test_slot_order_swap_rejected(self):
        self.document("design.json", lambda value: value["slots"].reverse())
        self.reject("readout-slot-plan")

    def test_plan_and_seven_call_cap_cannot_change(self):
        self.document("design.json", lambda value: value["controls"].__setitem__("max_query_invocations", 7))
        self.reject("readout-controls")

    def test_c1_canonical_resort_coherently_rehashed_is_rejected(self):
        original = (self.root / "inputs/S0C0.user.txt").read_bytes()
        (self.root / "inputs/S0C1.user.txt").write_bytes(original)
        digest = consumer.sha(original)
        self.document("design.json", lambda value: value["slots"][1].__setitem__("user_sha256", digest))
        self.document("ledger/query-0002.start.json", lambda value: value.__setitem__("user_sha256", digest))
        self.document("readouts/readout-0002.json", lambda value: value.__setitem__("user_sha256", digest))
        self.replace_prompt(2, lambda prompt: prompt.split("<|im_start|>user\n", 1)[0] +
            "<|im_start|>user\n" + original.decode() + " /no_think<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n")
        self.reject("readout-renderer-bytes")

    def test_nested_value_type_change_rejected(self):
        path = self.root / "inputs/S1C1.user.txt"
        path.write_bytes(path.read_bytes().replace(b'"enabled":true', b'"enabled":1'))
        self.reject("readout-renderer-bytes")

    def test_current_derived_score_label_in_context_rejected(self):
        self.document("inputs/S1.context.json", lambda value: value["last_result"].__setitem__("attribution", "UNKNOWN"))
        self.reject("readout-context-bytes")

    def test_coherent_wrong_system_request_echo_rejected(self):
        self.replace_prompt(1, lambda prompt: prompt.replace("<|im_start|>system\n", "<|im_start|>system\nExtra instruction.\n", 1))
        self.reject("completion-request")

    def test_all_new_queries_sampling_drift_from_origin_rejected(self):
        for number in range(1, 7):
            self.document("model/query-%04d/completion.response.json" % number,
                          lambda value: value["generation_settings"].__setitem__("top_k", 41))
            self.rebind(number)
        self.reject("readout-origin-generation-settings")

    def test_generation_setting_type_drift_from_origin_rejected(self):
        for number in range(1, 7):
            self.document("model/query-%04d/completion.response.json" % number,
                          lambda value: value["generation_settings"].__setitem__("temperature", 0))
            self.rebind(number)
        self.reject("readout-origin-generation-settings")

    def test_grammar_language_whitespace_rejected_after_coherent_hashes(self):
        self.document("model/query-0001/completion.response.json", lambda value: value.__setitem__("content", value["content"] + "\n"))
        self.rebind(1)
        self.reject("readout-grammar-language")

    def test_raw_proposal_cannot_be_replaced_with_evaluator_answer(self):
        self.document("readouts/readout-0001.json", lambda value: value["proposal"].__setitem__("attribution", "OTHER"))
        self.reject("readout-raw-proposal")

    def test_static_score_tamper_rejected(self):
        self.document("readouts/readout-0001.json", lambda value: value["static_score"].__setitem__("attribution_correct", False))
        self.reject("readout-static-score")

    def test_report_aggregate_tamper_rejected(self):
        self.document("report.json", lambda value: value["by_condition"]["C0"].__setitem__("readouts", 2))
        self.reject("readout-condition-summary")

    def test_effect_and_goal_metrics_cannot_appear_in_report_or_readout(self):
        for filename, field in (("report.json", "observed_goal_completion"),
                                ("readouts/readout-0001.json", "prediction_matched_result")):
            with self.subTest(filename=filename):
                path = self.root / filename
                original = path.read_bytes()
                self.document(filename, lambda value: value.__setitem__(field, False))
                self.reject("fields")
                path.write_bytes(original)

    def test_missing_start_finish_or_readout_rejected(self):
        for filename in ("ledger/query-0001.start.json", "ledger/query-0001.finish.json", "readouts/readout-0001.json"):
            with self.subTest(filename=filename):
                path = self.root / filename
                raw = path.read_bytes()
                path.unlink()
                self.reject("missing-artifact")
                path.write_bytes(raw)

    def test_duplicate_query_seven_and_orphan_file_rejected(self):
        shutil.copytree(self.root / "model/query-0006", self.root / "model/query-0007")
        self.reject("readout-http-coverage")

    def test_unreferenced_python_is_never_executed(self):
        (self.root / "execute-me.py").write_text("raise RuntimeError('artifact-executed')\n")
        self.reject("unreferenced-artifacts")

    def test_source_bytes_coherently_rehashed_rejected(self):
        relative = "tools/research/self_reference_model.py"
        path = self.root / "verification-source" / relative
        path.write_bytes(path.read_bytes() + b"\n")
        digest = consumer.sha(path.read_bytes())
        self.document("design.json", lambda value: value["source"]["files"].__setitem__(relative, digest))
        self.document("verification-source/manifest.json", lambda value: value["files"].__setitem__(relative, digest))
        for key in ("source_before", "source_after"):
            self.document("report.json", lambda value: value[key]["files"].__setitem__(relative, digest))
        self.reject("readout-source-version")

    def test_cleanup_unreaped_rejected(self):
        self.document("model/cleanup.json", lambda value: value.__setitem__("reaped", False))
        self.reject("model-cleanup")

    def test_raw_cost_cannot_omit_response_tokens(self):
        self.document("report.json", lambda value: value["raw_cost"].__setitem__("generated_tokens_known", 0))
        self.reject("readout-raw-cost")

    def test_failed_runs_are_not_partial_pass_and_preserve_tail_costs(self):
        for failure in ("tokenizer", "transport", "limit", "settings_drift", "syntax", "missing_receipt", "cleanup"):
            with self.subTest(failure=failure):
                root = Path(self.temp_copy.name) / ("failed-" + failure)
                self.assertEqual(self.produce_variant(root, failure), 1)
                before = hashes(root)
                result = consumer.verify_artifacts(root)
                self.assertEqual(result["outcome"], "NOT_EVALUABLE", result)
                self.assertFalse(result["partial_evidence_replayed"])
                self.assertEqual(result["raw_diagnostics"]["status"], "UNVERIFIED_RAW_DIAGNOSTICS")
                if failure in ("limit", "settings_drift", "cleanup"):
                    self.assertGreater(result["raw_diagnostics"]["completion_http_200"], 0)
                    self.assertGreater(result["raw_diagnostics"]["http_200_costs"]["prompt_tokens"]["known_sum"], 0)
                if failure == "tokenizer":
                    self.assertEqual(result["raw_diagnostics"]["completion_http_200"], 0)
                    self.assertEqual(result["raw_diagnostics"]["query_directories"], 1)
                self.assertEqual(before, hashes(root))

    def test_semantically_wrong_six_outputs_are_complete_static_results(self):
        root = Path(self.temp_copy.name) / "wrong-semantics"
        self.assertEqual(self.produce_variant(root, "wrong_semantics"), 0)
        with self.patched_origin(root.parent / (root.name + "-origin"), consumer):
            result = consumer.verify_artifacts(root)
        self.assertEqual(result["outcome"], "PASS", result)
        self.assertEqual(result["model_queries_verified"], 6)
        for value in result["by_condition"].values():
            self.assertEqual(value["readouts"], 3)
            self.assertEqual(value["action_revision_correct"], 1)
            self.assertEqual(value["attribution_correct"], 0)
            self.assertEqual(value["joint_correct"], 0)
            self.assertEqual(value["prediction_correct"], 3)

    def test_whole_condition_aggregate_swap_rejected(self):
        root = Path(self.temp_copy.name) / "different-conditions"
        self.assertEqual(self.produce_variant(root, "condition_difference"), 0)
        report = read(root / "report.json")
        self.assertNotEqual(report["by_condition"]["C0"], report["by_condition"]["C1"])
        report["by_condition"]["C0"], report["by_condition"]["C1"] = (
            report["by_condition"]["C1"], report["by_condition"]["C0"])
        write(root / "report.json", report)
        with self.patched_origin(root.parent / (root.name + "-origin"), consumer):
            result = consumer.verify_artifacts(root)
        self.assertEqual(result["outcome"], "FAIL", result)
        self.assertIn("readout-condition-summary", result["reason"])

    def test_cli_has_no_pin_override(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                consumer.main(["--artifacts", str(self.root), "--origin-manifest-sha256", "0" * 64])
        self.assertEqual(caught.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
