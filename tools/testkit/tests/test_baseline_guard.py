from __future__ import annotations

from copy import deepcopy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from lib.baseline_guard import (
    require_exact_profile_request,
    require_strict_baseline_write,
    require_trusted_matrix_source,
)
from lib.boot_inventory import (
    compare_inventory_records,
    require_complete_inventory_record,
    run_boot_inventory,
)
from lib.boot_perf import (
    PERF_RULES,
    compare_perf_records,
    require_complete_perf_record,
    run_boot_perf,
)
from lib.common import ToolError


def passing_result(profile: str) -> dict[str, object]:
    return {
        "profile": profile,
        "summary_present": True,
        "skipped": False,
        "unsupported": False,
        "outcome": "PASS",
        "passed": True,
        "missing_patterns": [],
        "verdict": {
            "schema_version": 1,
            "outcome": "PASS",
            "passed": True,
            "reasons": [],
            "missing_patterns": [],
            "health": {"passed": True},
            "checkpoints": {"passed": True},
        },
    }


def passing_matrix(profiles: list[str]) -> dict[str, object]:
    return {
        "passed": True,
        "profiles_requested": profiles,
        "profile_count": len(profiles),
        "results": [passing_result(profile) for profile in profiles],
    }


def complete_perf_record(profile: str = "full") -> dict[str, object]:
    return {
        "profile": profile,
        "selftest_status": "PASS",
        "size_kib": 256,
        "iterations": 64,
        "tier": "balanced",
        "metrics": {
            **{metric_name: 100 for metric_name in PERF_RULES},
            "tsc_khz": 3_000_000,
        },
    }


class BaselineGuardTests(unittest.TestCase):
    def test_complete_ordered_matrix_is_trusted(self) -> None:
        require_strict_baseline_write(True)
        require_exact_profile_request(["full", "minimal"], ["full", "minimal"])
        require_trusted_matrix_source(
            passing_matrix(["full", "minimal"]),
            ["full", "minimal"],
        )

    def test_non_strict_write_is_rejected(self) -> None:
        with self.assertRaises(ToolError):
            require_strict_baseline_write(False)

    def test_incomplete_domain_records_are_rejected(self) -> None:
        with self.assertRaises(ToolError):
            require_complete_inventory_record("full", {"profile": "full"})
        with self.assertRaises(ToolError):
            require_complete_perf_record("full", {"profile": "full"})

    def test_complete_domain_records_are_accepted(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        baseline_dir = repo_root / "tools" / "testkit" / "fixtures" / "boot-baseline"
        for profile in ("full", "minimal", "storage-only"):
            with self.subTest(profile=profile):
                inventory_path = baseline_dir / f"{profile}.json"
                inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
                require_complete_inventory_record(profile, inventory)

        require_complete_perf_record("full", complete_perf_record())

    def test_inventory_controller_contract_is_profile_aware(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        baseline_dir = repo_root / "tools" / "testkit" / "fixtures" / "boot-baseline"
        cases = (
            ("full", "network", "unknown"),
            ("minimal", "usb", "ready"),
            ("storage-only", "storage", "absent"),
        )
        for profile, controller, state in cases:
            with self.subTest(profile=profile, controller=controller, state=state):
                inventory = json.loads(
                    (baseline_dir / f"{profile}.json").read_text(encoding="utf-8")
                )
                inventory["controller_states"][controller] = state
                with self.assertRaises(ToolError):
                    require_complete_inventory_record(profile, inventory)

        inventory = json.loads(
            (baseline_dir / "full.json").read_text(encoding="utf-8")
        )
        inventory["controller_states"]["future-controller"] = "unknown"
        with self.assertRaises(ToolError):
            require_complete_inventory_record("full", inventory)

        inventory = json.loads(
            (baseline_dir / "full.json").read_text(encoding="utf-8")
        )
        inventory["controller_states"].pop("usb")
        with self.assertRaises(ToolError):
            require_complete_inventory_record("full", inventory)

        inventory = json.loads(
            (baseline_dir / "full.json").read_text(encoding="utf-8")
        )
        inventory["profile"] = "future"
        with self.assertRaises(ToolError):
            require_complete_inventory_record("future", inventory)

    def test_inventory_numeric_semantics_are_fail_closed(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        baseline_path = (
            repo_root / "tools" / "testkit" / "fixtures" / "boot-baseline" / "full.json"
        )
        inventory = json.loads(baseline_path.read_text(encoding="utf-8"))

        numeric_sections = {
            "device_summary": ("pci", "matched", "eth", "wifi", "bt", "usb", "storage"),
            "health_summary": ("ok", "degraded", "failed", "unknown", "io_degraded"),
        }
        for section_name, keys in numeric_sections.items():
            for key in keys:
                with self.subTest(section=section_name, key=key, value=-1):
                    invalid = deepcopy(inventory)
                    invalid[section_name][key] = -1
                    with self.assertRaises(ToolError):
                        require_complete_inventory_record("full", invalid)

        for key in ("degraded", "failed", "io_degraded"):
            with self.subTest(section="health_summary", key=key, value=1):
                invalid = deepcopy(inventory)
                invalid["health_summary"][key] = 1
                with self.assertRaises(ToolError):
                    require_complete_inventory_record("full", invalid)

        for value in (0, -1):
            with self.subTest(field="slm_seeded_plan_count", value=value):
                invalid = deepcopy(inventory)
                invalid["slm_seeded_plan_count"] = value
                with self.assertRaises(ToolError):
                    require_complete_inventory_record("full", invalid)

    def test_inventory_process_proof_values_are_exact(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        baseline_path = (
            repo_root / "tools" / "testkit" / "fixtures" / "boot-baseline" / "full.json"
        )
        inventory = json.loads(baseline_path.read_text(encoding="utf-8"))
        expected_values = {
            "slots": 2,
            "owned_processes": 2,
            "stack_bytes": 16384,
            "unique_cr3": 1,
            "unique_backing": 1,
            "unique_stack": 1,
            "pid": 1,
            "slot": 0,
            "process_bound": 1,
            "kstack_bytes": 16384,
            "rsp0_changed": 1,
            "rsp0_published": 1,
            "int80_entries": 3,
            "all_int80_entries_in_stack": 1,
            "rsp0_restored": 1,
            "kstack_floor_canary": 1,
        }
        for key, expected in expected_values.items():
            with self.subTest(key=key):
                invalid = deepcopy(inventory)
                invalid["process_stack"][key] = expected + 1
                with self.assertRaises(ToolError):
                    require_complete_inventory_record("full", invalid)

    def test_inventory_comparison_rejects_invalid_record_evidence(self) -> None:
        repo_root = Path(__file__).resolve().parents[3]
        baseline_path = (
            repo_root / "tools" / "testkit" / "fixtures" / "boot-baseline" / "full.json"
        )
        current = json.loads(baseline_path.read_text(encoding="utf-8"))
        poisoned_baseline = deepcopy(current)
        poisoned_baseline["health_summary"]["degraded"] = -1

        mismatches = compare_inventory_records(poisoned_baseline, current)

        self.assertIn("record_validation", mismatches)
        self.assertIn("baseline", mismatches["record_validation"])

    def test_perf_comparability_fields_are_exact_gates(self) -> None:
        baseline = complete_perf_record()
        cases = {
            "profile": "minimal",
            "size_kib": 512,
            "iterations": 32,
            "tier": "throughput",
        }
        for field, value in cases.items():
            with self.subTest(field=field):
                current = deepcopy(baseline)
                current[field] = value
                current["metrics"]["memcpy_mib_s"] = 10
                mismatches = compare_perf_records(baseline, current)
                self.assertEqual(
                    {"baseline": baseline[field], "current": value},
                    mismatches[field],
                )
                self.assertNotIn("metrics", mismatches)

        for field in cases:
            with self.subTest(field=field, malformed="missing-from-both"):
                incomplete_baseline = deepcopy(baseline)
                incomplete_current = deepcopy(baseline)
                incomplete_baseline.pop(field)
                incomplete_current.pop(field)
                incomplete_current["metrics"]["memcpy_mib_s"] = 10
                mismatches = compare_perf_records(
                    incomplete_baseline,
                    incomplete_current,
                )
                self.assertEqual(
                    {"baseline": None, "current": None},
                    mismatches[field],
                )
                self.assertNotIn("metrics", mismatches)

    def test_perf_thresholds_run_for_comparable_records(self) -> None:
        baseline = complete_perf_record()
        current = deepcopy(baseline)
        current["metrics"]["memcpy_mib_s"] = 10

        mismatches = compare_perf_records(baseline, current)

        metric_mismatch = mismatches["metrics"]["memcpy_mib_s"]
        self.assertIn("threshold", metric_mismatch)
        self.assertNotIn("reason", metric_mismatch)

    def test_perf_comparison_requires_pass_selftests(self) -> None:
        cases = (
            ("FAIL", "FAIL"),
            ("FAIL", "PASS"),
            ("PASS", "FAIL"),
            (None, None),
        )
        for baseline_status, current_status in cases:
            with self.subTest(
                baseline_status=baseline_status,
                current_status=current_status,
            ):
                baseline = complete_perf_record()
                current = complete_perf_record()
                baseline["selftest_status"] = baseline_status
                current["selftest_status"] = current_status
                current["metrics"]["memcpy_mib_s"] = 10

                mismatches = compare_perf_records(baseline, current)

                self.assertEqual(
                    "selftest-not-pass",
                    mismatches["selftest_status"]["reason"],
                )
                self.assertNotIn("metrics", mismatches)

    def test_perf_comparison_rejects_invalid_metric_evidence(self) -> None:
        invalid_values = (
            ("zero", 0),
            ("negative", -1),
            ("nan", float("nan")),
            ("positive-infinity", float("inf")),
            ("negative-infinity", float("-inf")),
            ("bool", True),
        )
        for source in ("baseline", "current"):
            for metric_name in PERF_RULES:
                for case_name, value in invalid_values:
                    with self.subTest(
                        source=source,
                        metric=metric_name,
                        case=case_name,
                    ):
                        baseline = complete_perf_record()
                        current = complete_perf_record()
                        record = baseline if source == "baseline" else current
                        record["metrics"][metric_name] = value

                        mismatches = compare_perf_records(baseline, current)

                        metric_mismatch = mismatches["metrics"][metric_name]
                        self.assertEqual("invalid-metric", metric_mismatch["reason"])
                        self.assertEqual(
                            [source],
                            metric_mismatch["invalid_sources"],
                        )

    def test_complete_perf_record_rejects_invalid_metrics(self) -> None:
        invalid_values = (
            0,
            -1,
            float("nan"),
            float("inf"),
            float("-inf"),
            True,
        )
        for metric_name in PERF_RULES:
            for value in invalid_values:
                with self.subTest(metric=metric_name, value=value):
                    record = complete_perf_record()
                    record["metrics"][metric_name] = value
                    with self.assertRaises(ToolError):
                        require_complete_perf_record("full", record)

    def test_perf_tsc_frequency_is_context_not_an_exact_gate(self) -> None:
        baseline = complete_perf_record()
        current = deepcopy(baseline)
        current["metrics"]["tsc_khz"] = 2_400_000

        self.assertEqual({}, compare_perf_records(baseline, current))

    def test_duplicate_profile_request_is_rejected(self) -> None:
        with self.assertRaises(ToolError):
            require_exact_profile_request(["full", "full"], ["full"])

    def test_failed_or_incomplete_matrix_is_rejected(self) -> None:
        cases = []

        aggregate_failed = passing_matrix(["full"])
        aggregate_failed["passed"] = False
        cases.append(aggregate_failed)

        missing_result = passing_matrix(["full", "minimal"])
        missing_result["results"] = missing_result["results"][:-1]
        cases.append(missing_result)

        reordered = passing_matrix(["full", "minimal"])
        reordered["results"] = list(reversed(reordered["results"]))
        cases.append(reordered)

        malformed = passing_matrix(["full"])
        malformed["results"] = [None]
        cases.append(malformed)

        for matrix in cases:
            with self.subTest(matrix=matrix):
                with self.assertRaises(ToolError):
                    require_trusted_matrix_source(matrix, ["full", "minimal"] if matrix.get("profile_count") == 2 else ["full"])

    def test_skip_unsupported_missing_summary_and_non_pass_are_rejected(self) -> None:
        mutations = (
            {"skipped": True, "outcome": "SKIP", "passed": False},
            {"unsupported": True, "outcome": "UNSUPPORTED", "passed": False},
            {"summary_present": False},
            {"outcome": "FAIL", "passed": False},
            {"missing_patterns": ["required"]},
            {"verdict": {}},
        )
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                matrix = passing_matrix(["full"])
                matrix["results"][0].update(mutation)
                with self.assertRaises(ToolError):
                    require_trusted_matrix_source(matrix, ["full"])

    @patch("lib.boot_inventory.run_boot_matrix")
    def test_inventory_non_strict_write_fails_before_matrix(self, run_matrix) -> None:
        with self.assertRaises(ToolError):
            run_boot_inventory(["full"], 1, strict=False, write_baseline=True)
        run_matrix.assert_not_called()

    @patch("lib.boot_perf.run_boot_matrix")
    def test_perf_non_strict_write_fails_before_matrix(self, run_matrix) -> None:
        with self.assertRaises(ToolError):
            run_boot_perf(["full"], 1, strict=False, write_baseline=True)
        run_matrix.assert_not_called()

    @patch("lib.boot_perf._write_json")
    @patch("lib.boot_perf.ensure_dir")
    @patch("lib.boot_perf._load_boot_summary")
    @patch("lib.boot_perf.run_boot_matrix")
    def test_perf_missing_late_summary_writes_no_baseline(
        self,
        run_matrix,
        load_summary,
        ensure_dir,
        write_json,
    ) -> None:
        run_matrix.return_value = passing_matrix(["full", "minimal"])
        load_summary.side_effect = [{}, ToolError("missing summary")]

        with self.assertRaises(ToolError):
            run_boot_perf(
                ["full", "minimal"],
                1,
                strict=True,
                write_baseline=True,
            )

        write_json.assert_not_called()
        ensure_dir.assert_not_called()


if __name__ == "__main__":
    unittest.main()
