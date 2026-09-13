"""Fixed two-profile producer and invocation-budget regressions; no real model."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prompt_comparison_fixture import produce
import self_reference_lab as lab
from self_reference_world import World


def read(path):
    return json.loads(path.read_bytes())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PromptComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = tempfile.TemporaryDirectory()
        cls.complete = Path(cls.fixture.name) / "complete"
        if produce(cls.complete) != 0:
            raise AssertionError("mocked two-profile producer failed")

    @classmethod
    def tearDownClass(cls):
        cls.fixture.cleanup()

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "run"

    def failure(self, kind):
        self.assertEqual(produce(self.root, failure=kind), 1)
        report = read(self.root / "report.json")
        self.assertEqual(report["experiment_integrity"], "FAIL")
        self.assertEqual(report["hypothesis_verdict"], "NOT_EVALUABLE")
        self.assertEqual(report["query_accounting"]["unfinished"], 0)
        return report

    def test_exact_two_prompt_bytes_and_separate_profile_results(self):
        design, report = read(self.complete / "design.json"), read(self.complete / "report.json")
        profiles = design["profiles"]
        self.assertEqual([p["prompt_id"] for p in profiles], ["baseline", "action-first-public-feedback-v1"])
        self.assertEqual([p["system_sha256"] for p in profiles], [
            "283fe9863649ed0b95d07981db9ecf27204ac389efa0866c912ac67cad7e3884",
            "cefb9168c4aa5ed20319a9d0b6e25f17db80870a03cb7b8df53e79558784390c"])
        for profile, system in zip(profiles, (lab.SYSTEM, lab.ACTION_PROGRESS_SYSTEM_V1)):
            self.assertEqual((self.complete / profile["system_path"]).read_bytes(), system.encode("utf-8"))
            self.assertEqual(sha(self.complete / profile["system_path"]), profile["system_sha256"])
        self.assertFalse(design["controls"]["system_prompt_equal"])
        self.assertEqual(design["controls"]["representation"], "relational")
        self.assertEqual(design["controls"]["max_query_invocations"], 24)
        self.assertEqual(report["completed_episodes"], report["planned_episodes"])
        self.assertEqual(report["planned_episodes"], 2)
        self.assertEqual(report["episode_paths"], [p["episode_path"] for p in profiles])
        self.assertNotIn("arms", report)
        self.assertNotIn("model_completion_requests_attempted", report)
        self.assertEqual(report["query_accounting"], {"reserved": 8, "returned": 8, "raised": 0, "unfinished": 0,
            "completion_http_records": 8, "completion_http_responses": 8, "completion_http_200": 8,
            "completion_transport_errors": 0, "without_decision": 0})
        for result in report["profiles"].values():
            self.assertEqual((result["episodes"], result["counts"]["model_calls"], result["observed_goal_completion"]), (1, 4, 1))

    def test_initial_facts_match_but_ledger_owns_each_profile_and_raw_query(self):
        design = read(self.complete / "design.json")
        initial = []
        number = 0
        for profile in design["profiles"]:
            first = read(self.complete / profile["episode_id"] / "decision-001.json")
            initial.append((first["context"], first["user"], (self.complete / profile["episode_id"] / "world/manifest.json").read_bytes()))
            self.assertEqual(first["context"]["step"], 0)
            self.assertEqual(first["context"]["history"], [])
            for step in range(1, 5):
                number += 1
                start_path = self.complete / ("ledger/query-%04d.start.json" % number)
                start = read(start_path)
                finish = read(self.complete / ("ledger/query-%04d.finish.json" % number))
                decision = read(self.complete / start["decision_path"])
                self.assertEqual((start["run_id"], start["plan_id"], start["prompt_id"], start["episode_id"], start["episode_step"]),
                    (design["run_id"], design["plan_id"], profile["prompt_id"], profile["episode_id"], step))
                self.assertEqual(start["context"], decision["context"])
                self.assertEqual(start["system_sha256"], decision["system_sha256"])
                self.assertEqual(finish["start_sha256"], sha(start_path))
                self.assertEqual(finish["status"], "RETURNED")
                query_dir = "model/query-%04d" % number
                self.assertEqual(start["expected_query_artifact_dir"], query_dir)
                self.assertEqual(finish["query_artifact_dir"], query_dir)
                self.assertEqual(finish["result_sha256"], sha(self.complete / query_dir / "result.json"))
                request = read(self.complete / query_dir / "completion.request.json")
                system = (self.complete / profile["system_path"]).read_bytes().decode("utf-8")
                self.assertTrue(request["prompt"].startswith("<|im_start|>system\n" + system + "<|im_end|>"))
        self.assertEqual(initial[0], initial[1])

    def test_tokenizer_failure_reserves_call_without_claiming_completion_http(self):
        report = self.failure("tokenizer")
        counts = report["query_accounting"]
        self.assertEqual((counts["reserved"], counts["raised"], counts["without_decision"]), (1, 1, 1))
        self.assertEqual(counts["completion_http_records"], 0)
        self.assertFalse(report["actual_model_executed"])
        folder = self.root / "model/query-0001"
        self.assertTrue((folder / "completion.request.json").is_file())
        self.assertFalse((folder / "completion.http.json").exists())
        self.assertEqual(read(folder / "result.json")["error"], "prompt_token_overflow")
        self.assertEqual(read(self.root / "ledger/query-0001.start.json")["prompt_id"], "baseline")

    def test_transport_failure_keeps_partial_exchange_separate_from_http_response(self):
        counts = self.failure("transport")["query_accounting"]
        self.assertEqual((counts["reserved"], counts["completion_http_records"], counts["completion_transport_errors"]), (1, 1, 1))
        self.assertEqual((counts["completion_http_responses"], counts["completion_http_200"]), (0, 0))
        self.assertEqual((self.root / "model/query-0001/completion.response.json").read_bytes(), b'{"partial":')

    def test_limit_http_200_is_a_failed_assigned_tail_and_is_not_retried(self):
        report = self.failure("limit")
        self.assertTrue(report["actual_model_executed"])
        self.assertEqual(report["query_accounting"]["completion_http_200"], 1)
        self.assertEqual(report["query_accounting"]["returned"], 0)
        self.assertEqual(read(self.root / "ledger/query-0001.finish.json")["error"]["code"], "incomplete_generation")
        self.assertFalse((self.root / "model/query-0002").exists())

    def test_second_profile_failure_preserves_owner_and_completed_first_profile_only(self):
        report = self.failure("second_transport")
        self.assertEqual(report["completed_episodes"], 1)
        self.assertEqual(set(report["profiles"]), {"baseline"})
        self.assertEqual(report["query_accounting"]["reserved"], 5)
        tail = read(self.root / "ledger/query-0005.start.json")
        self.assertEqual((tail["prompt_id"], tail["episode_step"]), ("action-first-public-feedback-v1", 1))
        self.assertFalse((self.root / tail["decision_path"]).exists())
        self.assertTrue(read(self.root / "model/cleanup.json")["reaped"])

    def test_observe_loops_stop_at_two_twelve_step_episodes_without_goal_claim(self):
        self.assertEqual(produce(self.root, failure="observe_loop"), 0)
        report = read(self.root / "report.json")
        self.assertEqual(report["query_accounting"]["reserved"], 24)
        self.assertFalse((self.root / "model/query-0025").exists())
        for result in report["profiles"].values():
            self.assertEqual(result["counts"]["decisions"], 12)
            self.assertEqual(result["step_limit"], 1)
            self.assertEqual(result["observed_goal_completion"], 0)

    def test_unreaped_owned_backend_makes_producer_fail(self):
        report = self.failure("cleanup")
        self.assertEqual(report["completed_episodes"], 2)
        cleanup = read(self.root / "model/cleanup.json")
        self.assertFalse(cleanup["reaped"])
        self.assertTrue(cleanup["errors"])

    def test_different_profile_outcomes_remain_separate(self):
        self.assertEqual(produce(self.root, failure="baseline_observe_loop"), 0)
        report = read(self.root / "report.json")
        self.assertEqual(report["query_accounting"]["reserved"], 16)
        baseline = report["profiles"]["baseline"]
        candidate = report["profiles"]["action-first-public-feedback-v1"]
        self.assertEqual((baseline["counts"]["decisions"], baseline["observed_goal_completion"]), (12, 0))
        self.assertEqual((candidate["counts"]["decisions"], candidate["observed_goal_completion"]), (4, 1))
        tail = read(self.root / "ledger/query-0013.start.json")
        self.assertEqual((tail["prompt_id"], tail["episode_step"]), ("action-first-public-feedback-v1", 1))

    def test_missing_completion_receipt_after_query_is_terminal_coverage_failure(self):
        original = lab.query_accounting
        def remove_then_count(root):
            (root / "model/query-0001/completion.http.json").unlink()
            return original(root)
        with patch.object(lab, "query_accounting", side_effect=remove_then_count):
            self.assertEqual(produce(self.root), 1)
        report = read(self.root / "report.json")
        self.assertEqual(report["failure"]["type"], "EpisodeCoverage")
        self.assertEqual((report["completed_episodes"], report["query_accounting"]["completion_http_records"]), (2, 7))

    def test_missing_cleanup_even_after_successful_close_is_terminal_failure(self):
        original = lab.query_accounting
        def remove_then_count(root):
            (root / "model/cleanup.json").unlink()
            return original(root)
        with patch.object(lab, "query_accounting", side_effect=remove_then_count):
            self.assertEqual(produce(self.root), 1)
        self.assertEqual(read(self.root / "report.json")["failure"]["type"], "BackendCleanup")

    def test_fixed_plan_rejects_other_selectors_before_artifact_creation(self):
        for options in (["--grammar"], ["--rules-only"], ["--calibration"], ["--repetitions", "2"]):
            with self.subTest(options=options), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit):
                    lab.main(["--artifacts", str(self.root), "--cache", "unused",
                              "--prompt-comparison", "action-progress-v1", *options])
            self.assertFalse(self.root.exists())


class InvocationBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "ledger").mkdir()
        self.profile = lab.prompt_comparison_profiles()[0]
        self.design = {"run_id": "test-run", "plan_id": lab.PROMPT_COMPARISON_PLAN, "profiles": [self.profile]}
        self.model = Mock()
        self.world = World(self.root / "world", "normal", "budget-test")
        self.context = lab.model_context({**self.world.context(), "history": []})
        self.user = lab.render_context(self.context, "relational")

    def test_twenty_fifth_invocation_is_rejected_before_model_entry(self):
        ledger = lab.ComparisonQueries(self.root, self.design, self.model)
        for _ in range(24):
            ledger.query(self.profile, lab.SYSTEM, self.user, context=self.context, episode_step=1)
        before = {path.name: sha(path) for path in (self.root / "ledger").iterdir()}
        with self.assertRaisesRegex(ValueError, "query-invocation-limit"):
            ledger.query(self.profile, lab.SYSTEM, self.user, context=self.context, episode_step=1)
        self.assertEqual(self.model.query.call_count, 24)
        self.assertEqual(before, {path.name: sha(path) for path in (self.root / "ledger").iterdir()})

    def test_reserved_start_exists_before_query_and_is_never_overwritten(self):
        start_path = self.root / "ledger/query-0001.start.json"
        def observe_entry(*args, **kwargs):
            self.assertEqual(read(start_path)["prompt_id"], "baseline")
            self.assertFalse((self.root / "ledger/query-0001.finish.json").exists())
            raise RuntimeError("entry failed")
        self.model.query.side_effect = observe_entry
        ledger = lab.ComparisonQueries(self.root, self.design, self.model)
        with self.assertRaisesRegex(RuntimeError, "entry failed"):
            ledger.query(self.profile, lab.SYSTEM, self.user, context=self.context, episode_step=1)
        before = start_path.read_bytes()
        second = lab.ComparisonQueries(self.root, self.design, self.model)
        with self.assertRaises(FileExistsError):
            second.query(self.profile, lab.SYSTEM, self.user, context=self.context, episode_step=1)
        self.assertEqual(self.model.query.call_count, 1)
        self.assertEqual(start_path.read_bytes(), before)
        finish = read(self.root / "ledger/query-0001.finish.json")
        self.assertEqual((finish["status"], finish["query_artifact_dir"], finish["result_sha256"]), ("RAISED", None, None))


if __name__ == "__main__":
    unittest.main()
