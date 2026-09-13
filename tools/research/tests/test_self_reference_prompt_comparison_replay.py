"""Independent v2 raw-file replay and tampering; mocked model, never a real backend."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import self_reference_contract as contract
import self_reference_replay as replay
from prompt_comparison_fixture import produce


def read(path):
    return json.loads(path.read_bytes())


def write(path, value):
    path.write_bytes(contract.encoded(value) + b"\n")


def hashes(root):
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file()}


class PromptComparisonReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="prompt-comparison-replay-")
        cls.fixture = Path(cls.temp.name) / "fixture"
        with contextlib.redirect_stdout(io.StringIO()):
            if produce(cls.fixture) != 0:
                raise AssertionError("mocked prompt comparison producer failed")

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.temp_copy = tempfile.TemporaryDirectory(prefix="prompt-replay-tamper-")
        self.addCleanup(self.temp_copy.cleanup)
        self.root = Path(self.temp_copy.name) / "relocated"
        shutil.copytree(self.fixture, self.root)

    def reject(self, reason=None):
        result = replay.verify_artifacts(self.root)
        self.assertEqual(result["outcome"], "FAIL", result)
        if reason is not None:
            self.assertIn(reason, result["reason"], result)

    def document(self, name, change):
        path = self.root / name
        value = read(path)
        change(value)
        write(path, value)

    def rehash_finish(self, number):
        prefix = self.root / ("ledger/query-%04d" % number)
        path = Path(str(prefix) + ".finish.json")
        value = read(path)
        value["start_sha256"] = replay.sha(Path(str(prefix) + ".start.json").read_bytes())
        result = self.root / ("model/query-%04d/result.json" % number)
        value["result_sha256"] = replay.sha(result.read_bytes())
        write(path, value)

    def change_response(self, number, change):
        relative = "model/query-%04d" % number
        folder = self.root / relative
        response = read(folder / "completion.response.json")
        change(response)
        write(folder / "completion.response.json", response)
        raw = (folder / "completion.response.json").read_bytes()
        write(folder / "completion.http.json", {"status": 200, "response_bytes": len(raw)})
        result = read(folder / "result.json")
        result["raw_response"] = response
        result["response_sha256"] = replay.sha(raw)
        write(folder / "result.json", result)
        start = read(self.root / ("ledger/query-%04d.start.json" % number))
        path = self.root / start["decision_path"]
        decision = read(path)
        decision["model_query"] = {key: value for key, value in result.items() if key != "outcome"}
        write(path, decision)
        episode_path = path.parent / "episode.json"
        episode = read(episode_path)
        episode["decisions"][start["episode_step"] - 1]["decision_sha256"] = replay.sha(path.read_bytes())
        write(episode_path, episode)
        self.rehash_finish(number)

    def test_complete_two_profiles_replay_with_exact_source_and_accounting(self):
        before = hashes(self.root)
        result = replay.verify_artifacts(self.root)
        self.assertEqual(result["outcome"], "PASS", result)
        self.assertEqual(result["schema_version"], 2)
        self.assertEqual(result["episodes_verified"], 2)
        self.assertEqual(result["model_queries_verified"], 8)
        self.assertEqual(result["decisions_verified"], 8)
        self.assertEqual(result["source_files_verified"], 5)
        self.assertEqual(set(result["profiles"]), {"baseline", "action-first-public-feedback-v1"})
        self.assertTrue(result["initial_contexts_paired"])
        self.assertEqual(result["query_accounting"]["reserved"], 8)
        self.assertEqual(result["query_accounting"]["without_decision"], 0)
        self.assertEqual(result["hypothesis_verdict"], "NOT_EVALUATED_CALIBRATION")
        self.assertEqual(before, hashes(self.root))

    def test_profile_order_cannot_change(self):
        self.document("design.json", lambda v: v["profiles"].reverse())
        self.reject("prompt-profile-plan")

    def test_candidate_cannot_be_replaced_by_baseline_metadata(self):
        self.document("design.json", lambda v: v["profiles"].__setitem__(1, dict(v["profiles"][0])))
        self.reject("prompt-profile-plan")

    def test_wrong_run_id_is_rejected(self):
        self.document("report.json", lambda v: v.__setitem__("run_id", "00000000-0000-4000-8000-000000000000"))
        self.reject("prompt-plan-identity")

    def test_non_normal_scenario_is_rejected(self):
        self.document("design.json", lambda v: v.__setitem__("scenarios", ["stale_after_observe"]))
        self.reject("prompt-experiment-plan")

    def test_repetitions_cannot_expand_fixed_pair(self):
        self.document("design.json", lambda v: v.__setitem__("repetitions", 2))
        self.reject("prompt-experiment-plan")

    def test_grammar_cannot_be_disabled(self):
        self.document("design.json", lambda v: v["controls"].__setitem__("output_constraint", "off"))
        self.reject("prompt-controls")

    def test_cap_cannot_be_raised(self):
        self.document("design.json", lambda v: v["controls"].__setitem__("max_query_invocations", 25))
        self.reject("prompt-controls")

    def test_candidate_source_and_file_rehashed_together_still_fail_fixed_pin(self):
        relative = "tools/research/self_reference_lab.py"
        source = self.root / "verification-source" / relative
        original = source.read_bytes()
        altered = original.replace(b"Control the experimental object.", b"Control an experimental object.")
        self.assertNotEqual(original, altered)
        source.write_bytes(altered)
        prompt = self.root / replay.PROMPT_PROFILES[1]["system_path"]
        prompt.write_bytes(prompt.read_bytes().replace(b"Control the experimental object.", b"Control an experimental object."))
        digest = replay.sha(altered)
        self.document("design.json", lambda v: v["source"]["files"].__setitem__(relative, digest))
        self.document("report.json", lambda v: [v[key]["files"].__setitem__(relative, digest)
                                               for key in ("source_before", "source_after")])
        self.document("verification-source/manifest.json", lambda v: v["files"].__setitem__(relative, digest))
        self.reject("prompt-source-bytes-and-pin")

    def test_retained_contract_pin_is_not_weakened(self):
        relative = "tools/research/self_reference_contract.py"
        path = self.root / "verification-source" / relative
        path.write_bytes(path.read_bytes() + b"\n")
        digest = replay.sha(path.read_bytes())
        self.document("design.json", lambda v: v["source"]["files"].__setitem__(relative, digest))
        self.document("report.json", lambda v: [v[key]["files"].__setitem__(relative, digest)
                                               for key in ("source_before", "source_after")])
        self.document("verification-source/manifest.json", lambda v: v["files"].__setitem__(relative, digest))
        self.reject("replay-source-version")

    def test_ledger_profile_swap_cannot_pass_with_matching_finish_hash(self):
        self.document("ledger/query-0001.start.json", lambda v: v.__setitem__("prompt_id", "action-first-public-feedback-v1"))
        self.rehash_finish(1)
        self.reject("prompt-ledger-start")

    def test_ledger_step_cannot_be_reused_with_matching_finish_hash(self):
        self.document("ledger/query-0002.start.json", lambda v: v.__setitem__("episode_step", 1))
        self.rehash_finish(2)
        self.reject("prompt-ledger-start")

    def test_ledger_context_and_hash_cannot_change(self):
        def change(value):
            value["context"]["history"] = [{"forged": True}]
            value["context_sha256"] = contract.digest(value["context"])
        self.document("ledger/query-0001.start.json", change)
        self.rehash_finish(1)
        self.reject("prompt-ledger-start")

    def test_missing_start_is_rejected(self):
        (self.root / "ledger/query-0001.start.json").unlink()
        self.reject("missing-artifact")

    def test_missing_finish_is_rejected(self):
        (self.root / "ledger/query-0001.finish.json").unlink()
        self.reject("missing-artifact")

    def test_unassigned_extra_ledger_cannot_hide_outside_report_counts(self):
        shutil.copyfile(self.root / "ledger/query-0001.start.json", self.root / "ledger/query-0025.start.json")
        self.reject("prompt-ledger-coverage")

    def test_extra_http_query_is_rejected(self):
        path = self.root / "model/query-0025"
        path.mkdir()
        write(path / "completion.http.json", {"status": 200, "response_bytes": 1})
        self.reject("prompt-http-coverage")

    def test_raised_finish_cannot_pass_completed_report(self):
        self.document("ledger/query-0001.finish.json", lambda v: v.__setitem__("status", "RAISED"))
        self.reject("prompt-ledger-finish")

    def test_finish_result_hash_must_match_actual_query_receipt(self):
        self.document("ledger/query-0001.finish.json", lambda v: v.__setitem__("result_sha256", "0" * 64))
        self.reject("prompt-ledger-finish")

    def test_http_receipt_is_not_inferred_from_request_presence(self):
        (self.root / "model/query-0001/completion.http.json").unlink()
        self.reject("missing-artifact")

    def test_accounting_cannot_call_reserved_slots_http_attempts(self):
        self.document("report.json", lambda v: v.__setitem__("model_completion_requests_attempted", 8))
        self.reject("prompt-document-fields")

    def test_accounting_must_equal_decisions_not_reported_claim(self):
        self.document("report.json", lambda v: v["query_accounting"].__setitem__("reserved", 9))
        self.reject("prompt-query-accounting")

    def test_boolean_accounting_is_rejected(self):
        self.document("report.json", lambda v: v["query_accounting"].__setitem__("raised", False))
        self.reject("prompt-accounting-shape")

    def test_sampling_drift_rehashed_through_decision_and_ledger_is_rejected(self):
        self.change_response(5, lambda v: v["generation_settings"].__setitem__("top_k", 41))
        self.reject("generation-settings-drift")

    def test_wrong_http_prompt_rehashed_through_decision_and_ledger_is_rejected(self):
        self.change_response(5, lambda v: v.__setitem__("prompt", "different SYSTEM"))
        self.reject("completion-contract")

    def test_candidate_http_request_cannot_use_baseline_even_after_coherent_rehash(self):
        number = 5
        folder = self.root / "model/query-0005"
        baseline = (self.root / replay.PROMPT_PROFILES[0]["system_path"]).read_text(encoding="utf-8")
        candidate = (self.root / replay.PROMPT_PROFILES[1]["system_path"]).read_text(encoding="utf-8")
        request = read(folder / "completion.request.json")
        old_prompt = request["prompt"]
        self.assertIn(candidate, old_prompt)
        request["prompt"] = old_prompt.replace(candidate, baseline, 1)
        write(folder / "completion.request.json", request)
        self.document("model/query-0005/tokenize.request.json",
                      lambda value: value.__setitem__("content", request["prompt"]))
        self.change_response(number, lambda value: value.__setitem__("prompt", request["prompt"]))
        result = read(folder / "result.json")
        result["request_sha256"] = replay.sha((folder / "completion.request.json").read_bytes())
        write(folder / "result.json", result)
        start = read(self.root / "ledger/query-0005.start.json")
        path = self.root / start["decision_path"]
        decision = read(path)
        decision["model_query"] = {key: value for key, value in result.items() if key != "outcome"}
        write(path, decision)
        episode_path = path.parent / "episode.json"
        episode = read(episode_path)
        episode["decisions"][start["episode_step"] - 1]["decision_sha256"] = replay.sha(path.read_bytes())
        write(episode_path, episode)
        self.rehash_finish(number)
        self.assertEqual(read(folder / "completion.response.json")["prompt"], request["prompt"])
        self.assertEqual(read(folder / "tokenize.request.json")["content"], request["prompt"])
        self.reject("completion-request")

    def test_limit_stop_rehashed_through_decision_and_ledger_is_rejected(self):
        self.change_response(5, lambda v: v.__setitem__("stop_type", "limit"))
        self.reject("completion-contract")

    def test_cleanup_reaped_requirement_is_unchanged(self):
        self.document("model/cleanup.json", lambda v: v.__setitem__("reaped", False))
        self.reject("model-cleanup")

    def test_completion_cannot_claim_efficacy_hypothesis(self):
        self.document("report.json", lambda v: v.__setitem__("hypothesis_verdict", "PROVEN"))
        self.reject("report-hypothesis")

    def test_report_cannot_inflate_profile_action_count(self):
        def change(value):
            value["profiles"]["baseline"]["counts"]["correct_action"] += 1
        self.document("report.json", change)
        self.reject("prompt-report-profiles")

    def test_complete_unequal_profile_outcomes_cannot_be_swapped(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "mixed"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(produce(target, failure="baseline_observe_loop"), 0)
            result = replay.verify_artifacts(target)
            self.assertEqual(result["outcome"], "PASS", result)
            self.assertEqual(result["model_queries_verified"], 16)
            path = target / "report.json"
            report = read(path)
            left, right = (profile["prompt_id"] for profile in replay.PROMPT_PROFILES)
            self.assertEqual(report["profiles"][left]["observed_goal_completion"], 0)
            self.assertEqual(report["profiles"][right]["observed_goal_completion"], 1)
            report["profiles"][left], report["profiles"][right] = report["profiles"][right], report["profiles"][left]
            write(path, report)
            rejected = replay.verify_artifacts(target)
            self.assertEqual(rejected["outcome"], "FAIL", rejected)
            self.assertIn("prompt-report-profiles", rejected["reason"])

    def test_extra_unreferenced_file_is_rejected(self):
        (self.root / "extra.txt").write_bytes(b"unreported")
        self.reject("unreferenced-artifacts")

    def test_real_writer_failure_receipts_never_become_partial_pass(self):
        for failure in ("tokenizer", "transport", "limit", "second_transport", "cleanup"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / "failed"
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(produce(target, failure=failure), 1)
                before = hashes(target)
                result = replay.verify_artifacts(target)
                self.assertEqual(result["outcome"], "NOT_EVALUABLE", result)
                self.assertFalse(result["partial_evidence_replayed"])
                self.assertEqual(result["hypothesis_verdict"], "NOT_EVALUABLE")
                if failure == "tokenizer":
                    self.assertEqual(result["unverified_inventory"]["query_start_files"], 1)
                    self.assertEqual(result["unverified_inventory"]["completion_http_files"], 0)
                    self.assertTrue((target / "model/query-0001/completion.request.json").is_file())
                self.assertEqual(before, hashes(target))

    def test_twenty_four_complete_observations_are_integrity_not_efficacy(self):
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / "loop"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(produce(target, failure="observe_loop"), 0)
            result = replay.verify_artifacts(target)
            self.assertEqual(result["outcome"], "PASS", result)
            self.assertEqual(result["query_accounting"]["reserved"], 24)
            self.assertEqual(result["model_queries_verified"], 24)
            for profile in result["profiles"].values():
                self.assertEqual(profile["step_limit"], 1)
                self.assertEqual(profile["observed_goal_completion"], 0)
            self.assertEqual(result["hypothesis_verdict"], "NOT_EVALUATED_CALIBRATION")

    def test_failed_report_stays_not_evaluable_without_partial_integrity_claim(self):
        self.document("report.json", lambda v: v.update(experiment_integrity="FAIL",
            failure={"type": "ModelError", "reason": "incomplete_generation"},
            hypothesis_verdict="NOT_EVALUABLE", completed_episodes=1))
        (self.root / "ledger/query-0008.finish.json").unlink()
        before = hashes(self.root)
        result = replay.verify_artifacts(self.root)
        self.assertEqual(result["outcome"], "NOT_EVALUABLE", result)
        self.assertFalse(result["partial_evidence_replayed"])
        self.assertEqual(result["hypothesis_verdict"], "NOT_EVALUABLE")
        self.assertEqual(result["unverified_inventory"]["query_start_files"], 8)
        self.assertEqual(result["unverified_inventory"]["query_finish_files"], 7)
        self.assertNotIn("actual_model_evidence_verified", result)
        self.assertEqual(before, hashes(self.root))


if __name__ == "__main__":
    unittest.main()
