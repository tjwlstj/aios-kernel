"""Host staging checks; synthetic files are not live model evidence."""
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
from qemu_console import stage_evidence_report


class EvidenceReportStageTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.share = self.root / "share"
        self.output = self.root / "output"
        self.share.mkdir()
        self.output.mkdir()
        sources = {}
        for index in range(10):
            relative = f"build/source-{index}.json"
            raw = json.dumps({"index": index}).encode("ascii")
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            sources[relative] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
        self.plan = {"schema_version": 1, "scenario": "evidence-report-feedback-v1",
                     "source_files": sources, "e0": {"run_id": "synthetic"}, "question1": "Q"}
        self.plan_path = self.root / "plan.json"
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")

    def stage(self):
        with patch("evidence_report_plan.build_plan", return_value=self.plan):
            stage_evidence_report(self.root, self.share, self.output, self.plan_path)

    def test_pinned_plan_and_sources_are_snapshotted(self):
        self.stage()
        self.assertEqual((self.output / "evidence-plan.json").read_bytes(), self.plan_path.read_bytes())
        self.assertEqual((self.share / "evidence-report/plan.json").read_bytes(), self.plan_path.read_bytes())
        self.assertEqual(len(list((self.output / "evidence-source").rglob("*.json"))), 10)

    def test_changed_question_rejected_before_copy(self):
        modified = dict(self.plan)
        modified["question1"] = "changed"
        self.plan_path.write_text(json.dumps(modified), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "differs from pinned evidence"):
            self.stage()
        self.assertFalse((self.share / "evidence-report").exists())

    def test_changed_source_rejected_before_vm(self):
        (self.root / "build/source-0.json").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "source changed"):
            self.stage()

    def test_duplicate_plan_key_rejected(self):
        raw = self.plan_path.read_text(encoding="utf-8")
        self.plan_path.write_text(raw[:-1] + ',"question1":"changed"}', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "duplicate JSON key"):
            self.stage()


if __name__ == "__main__":
    unittest.main()
