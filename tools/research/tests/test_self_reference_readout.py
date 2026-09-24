"""Producer boundary regressions using synthetic origin and MOCK backend replies."""
import argparse
import copy
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

import readout_fixture as fixture
import self_reference_contract as contract
import self_reference_readout as readout
import self_reference_replay as replay
import self_reference_world as world


class ReadoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temp.name)
        cls.good = cls.base / "good"
        if fixture.produce(cls.good) != 0:
            raise AssertionError((cls.good / "report.json").read_text())
        cls.origin = fixture.origin_path(cls.good)
        cls.design = json.loads((cls.good / "design.json").read_bytes())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def fresh(self, name):
        root = self.base / name
        shutil.copytree(self.origin, fixture.origin_path(root))
        return root

    def test_real_production_pins_are_literal_and_no_override(self):
        self.assertEqual(readout.ORIGIN_MANIFEST_SHA256,
            "6575e701107dd78a16d3a254a15a02abed8989a4295b3b53e5322965627e6ed8")
        self.assertEqual(readout.DECISION_PINS, {
            "S0": "24c27fd755ca538dc4deadebee5f1d090018e5197e39448316e3c854bae6c4a8",
            "S1": "5d1c684ff91a0c9fc16f06ed17318be5fb9429cb8edeeb2daa0f63ce725462f5",
            "S11": "e15f63d9263d2f94bf56f182dc1626f21186ae130dfc81902f8048827dd309ef"})
        self.assertEqual(readout.SYSTEM_SHA256,
            "cefb9168c4aa5ed20319a9d0b6e25f17db80870a03cb7b8df53e79558784390c")
        self.assertEqual(len(readout.TRUSTED_PINS), 4)
        readout.trusted_modules()
        with self.assertRaisesRegex(ValueError, "origin-manifest-pin"):
            readout.check_origin(self.origin, replay)

    def test_only_observation_moves_and_nested_bytes_and_facts_are_identical(self):
        for sample in self.design["samples"]:
            name = sample["sample_id"]
            original = (self.good / f"inputs/{name}C0.user.txt").read_text(encoding="utf-8")
            variant = (self.good / f"inputs/{name}C1.user.txt").read_text(encoding="utf-8")
            raw = json.loads((self.good / sample["origin_decision_path"]).read_bytes())
            self.assertEqual(original, raw["user"])
            self.assertEqual(contract.encoded(json.loads(original)), contract.encoded(json.loads(variant)))
            self.assertEqual(tuple(json.loads(variant)["related_facts"]), readout.VARIANT_KEYS)
            for key, value in raw["context"].items():
                member = contract.encoded(key).decode() + ":" + contract.encoded(value).decode()
                self.assertIn(member, original)
                self.assertIn(member, variant)
            self.assertEqual(variant, readout.render_variant(original, raw["context"], contract))
        context = json.loads((self.good / "inputs/S1.context.json").read_bytes())
        with self.assertRaisesRegex(ValueError, "not-canonical"):
            readout.render_variant(" " + original, context, contract)

    def test_six_fresh_queries_exact_order_and_no_effect_metrics(self):
        report = json.loads((self.good / "report.json").read_bytes())
        self.assertEqual(report["experiment_integrity"], "PASS")
        self.assertEqual([x["slot_id"] for x in self.design["slots"]], list(readout.ORDER))
        self.assertEqual(report["query_accounting"]["reserved"], 6)
        self.assertEqual(report["raw_cost"]["token_records_known"], 6)
        self.assertEqual(report["evaluation_scope"], "STATIC_ONLY")
        self.assertFalse(any("goal" in key or "actual_outcome" in key for key in report))
        self.assertEqual(readout.inventory(self.origin, replay)["files"],
                         readout.inventory(self.good / "origin", replay)["files"])
        for slot in self.design["slots"]:
            row = json.loads((self.good / slot["readout_path"]).read_bytes())
            context = json.loads((self.good / f"inputs/{slot['sample_id']}.context.json").read_bytes())
            self.assertEqual(row["static_score"], contract.score_decision(context, row["proposal"]))
            self.assertEqual(row["content"], row["model_query"]["raw_response"]["content"])

    def test_readout_never_constructs_world_and_wrong_valid_semantics_continue(self):
        root = self.fresh("wrong-semantics")
        before = readout.inventory(fixture.origin_path(root), replay)
        with patch.object(world.World, "__init__", side_effect=AssertionError("World construction forbidden")):
            self.assertEqual(fixture.produce(root, failure="wrong_semantics"), 0)
        report = json.loads((root / "report.json").read_bytes())
        self.assertEqual(report["completed_slots"], 6)
        for counts in report["by_condition"].values():
            self.assertEqual(counts["action_revision_correct"], 1)
            self.assertEqual(counts["attribution_correct"], 0)
        self.assertEqual(before, readout.inventory(fixture.origin_path(root), replay))

    def test_output_inside_origin_rejected_before_any_write_or_backend_start(self):
        before = readout.inventory(self.origin, replay)
        target = self.origin / "deep" / "new" / "output"
        with patch.object(readout, "trusted_modules") as loader:
            with self.assertRaisesRegex(ValueError, "artifact-root-overlap"):
                readout.main(["--origin", str(self.origin), "--artifacts", str(target),
                              "--cache", str(self.base / "cache")])
            loader.assert_not_called()
        self.assertFalse(target.parent.parent.exists())
        self.assertEqual(before, readout.inventory(self.origin, replay))

    def test_protected_ancestors_cache_and_traversal_are_rejected_before_creation(self):
        origin, cache = self.base / "some-origin", self.base / "some-cache"
        for output in (self.base, cache, cache / "new" / "nested", origin,
                       origin / "a" / "b", self.base / "parent" / ".." / "out"):
            with self.subTest(output=output), self.assertRaisesRegex(ValueError, "overlap|traversal"):
                readout.paths(argparse.Namespace(artifacts=output, origin=origin, cache=cache))
        ordinary = self.base / "ordinary-file"
        ordinary.write_bytes(b"unchanged")
        with self.assertRaisesRegex(ValueError, "unsafe-directory"):
            readout.safe_directory(ordinary / "a" / "b" / "c")
        self.assertEqual(ordinary.read_bytes(), b"unchanged")

    def test_symlink_ancestor_is_rejected_even_through_missing_children(self):
        target, link = self.base / "link-target", self.base / "directory-link"
        target.mkdir()
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            self.skipTest("directory symlink privilege unavailable: " + str(exc))
        with self.assertRaisesRegex(ValueError, "unsafe-directory"):
            readout.safe_directory(link / "a" / "b" / "c")
        self.assertEqual(list(target.iterdir()), [])

    def test_reservation_rejects_seventh_and_duplicate_slot_before_model(self):
        root = self.base / "ledger-boundary"
        (root / "ledger").mkdir(parents=True)
        slot = copy.deepcopy(self.design["slots"][0])
        sample = self.design["samples"][0]
        model = Mock()
        system = (self.good / "system.txt").read_text(encoding="utf-8")
        user = (self.good / slot["user_path"]).read_text(encoding="utf-8")
        seventh = {**slot, "ordinal": 7}
        with self.assertRaisesRegex(ValueError, "query-slot-limit"):
            readout.query_slot(root, self.design, seventh, sample, system, user, model)
        model.query.assert_not_called()
        model.query.return_value = {}
        readout.query_slot(root, self.design, slot, sample, system, user, model)
        with self.assertRaises(FileExistsError):
            readout.query_slot(root, self.design, slot, sample, system, user, model)
        self.assertEqual(model.query.call_count, 1)

    def test_changed_actual_input_hash_rejected_before_reservation_and_call(self):
        root = self.base / "input-boundary"
        (root / "ledger").mkdir(parents=True)
        slot, sample = self.design["slots"][0], self.design["samples"][0]
        system = (self.good / "system.txt").read_text(encoding="utf-8")
        user = (self.good / slot["user_path"]).read_text(encoding="utf-8")
        model = Mock()
        for s, u in ((system + " ", user), (system, user + " ")):
            with self.assertRaisesRegex(ValueError, "query-input-hash"):
                readout.query_slot(root, self.design, slot, sample, s, u, model)
        self.assertEqual(list((root / "ledger").iterdir()), [])
        model.query.assert_not_called()

    def test_interrupt_records_raised_finish_and_never_returned(self):
        root = self.base / "interrupted"
        (root / "ledger").mkdir(parents=True)
        slot, sample = self.design["slots"][0], self.design["samples"][0]
        model = Mock()
        model.query.side_effect = KeyboardInterrupt("mock query interrupted")
        with self.assertRaises(KeyboardInterrupt):
            readout.query_slot(root, self.design, slot, sample,
                (self.good / "system.txt").read_text(encoding="utf-8"),
                (self.good / slot["user_path"]).read_text(encoding="utf-8"), model)
        finish = json.loads((root / "ledger/query-0001.finish.json").read_bytes())
        self.assertEqual(finish["status"], "RAISED")
        self.assertEqual(finish["error"]["type"], "KeyboardInterrupt")
        self.assertIsNone(finish["result_sha256"])

    def test_preflight_transport_limit_and_settings_failures_keep_honest_tail_cost(self):
        for failure, http, known, code in (
                ("tokenizer", 0, 0, "prompt_token_overflow"),
                ("transport", 1, 0, "transport_error"),
                ("limit", 1, 1, "incomplete_generation"),
                ("settings_drift", 1, 1, None),
                ("syntax", 1, 1, None)):
            with self.subTest(failure=failure):
                root = self.fresh(failure)
                self.assertEqual(fixture.produce(root, failure=failure), 1)
                report = json.loads((root / "report.json").read_bytes())
                self.assertEqual(report["experiment_integrity"], "FAIL")
                self.assertEqual(report["completed_slots"], 0)
                self.assertEqual(report["failure"]["code"], code)
                self.assertEqual(report["query_accounting"]["reserved"], 1)
                self.assertEqual(report["query_accounting"]["completion_http_records"], http)
                self.assertEqual(report["raw_cost"]["token_records_known"], known)
                self.assertEqual(report["raw_cost"]["query_directories"], 1)
                self.assertEqual(report["raw_cost"]["elapsed_records_known"], 1)
                self.assertEqual(len(report["raw_cost"]["unknown_token_queries"]), 1 - known)
                self.assertTrue((root / "model/query-0001/completion.request.json").is_file())
                if failure == "limit":
                    self.assertEqual(report["raw_cost"]["generated_tokens_known"], 192)

    def test_cleanup_or_missing_receipt_cannot_claim_integrity_pass(self):
        for failure in ("cleanup", "missing_receipt"):
            with self.subTest(failure=failure):
                root = self.fresh(failure)
                self.assertEqual(fixture.produce(root, failure=failure), 1)
                report = json.loads((root / "report.json").read_bytes())
                self.assertEqual(report["completed_slots"], 6)
                self.assertEqual(report["experiment_integrity"], "FAIL")
                self.assertIsNotNone(report["failure"])
                self.assertEqual(report["raw_cost"]["query_directories"], 6)


if __name__ == "__main__":
    unittest.main()
