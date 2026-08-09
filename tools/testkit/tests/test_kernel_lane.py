from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import boot_log, kernel_lane
from lib.common import ToolError


class KernelBootSummaryArtifactTests(unittest.TestCase):
    def _assert_stale_pass_is_replaced(self, cpu_profile: str) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            build_dir = Path(temp_dir)
            with (
                patch.object(boot_log, "BUILD_DIR", build_dir),
                patch.object(kernel_lane, "BUILD_DIR", build_dir),
            ):
                summary_path = boot_log.boot_summary_path(
                    "test", "minimal", cpu_profile
                )
                summary_path.parent.mkdir(parents=True, exist_ok=True)
                summary_path.write_text(
                    json.dumps(
                        {"stale": True, "verdict": {"passed": True}}
                    ),
                    encoding="utf-8",
                )

                with (
                    patch.object(
                        kernel_lane, "host_name", return_value="windows"
                    ),
                    patch.object(
                        kernel_lane,
                        "run_windows_kernel",
                        side_effect=ToolError("synthetic boot failure"),
                    ),
                    self.assertRaisesRegex(
                        ToolError, "synthetic boot failure"
                    ),
                ):
                    kernel_lane.run_kernel_suite(
                        "test",
                        timeout_sec=1,
                        strict=True,
                        smoke_profile="minimal",
                        export_boot_summary=True,
                        cpu_profile=cpu_profile,
                    )

                summary = json.loads(
                    summary_path.read_text(encoding="utf-8")
                )
                self.assertNotIn("stale", summary)
                self.assertEqual(
                    "initialized-before-run", summary["artifact_state"]
                )
                self.assertEqual(cpu_profile, summary["cpu_profile"])
                self.assertFalse(summary["verdict"]["passed"])
                self.assertEqual("FAIL", summary["verdict"]["outcome"])
                self.assertEqual(
                    ["-cpu", "max"] if cpu_profile == "max-smap" else [],
                    summary["qemu_cpu_args"],
                )

    def test_default_export_replaces_stale_pass_before_failure(self) -> None:
        self._assert_stale_pass_is_replaced("default")

    def test_max_smap_export_replaces_stale_pass_before_failure(self) -> None:
        self._assert_stale_pass_is_replaced("max-smap")

    def test_collect_rejects_not_ready_security_even_if_verdict_passes(
        self,
    ) -> None:
        feature = kernel_lane.CPU_SECURITY_PATTERNS["default"]
        entry = kernel_lane.RING3_ENTRY_AC_HARDENING_PATTERNS["default"]
        log = "\n".join(
            (
                feature,
                entry,
                "[ROOM] snapshot stability=degraded "
                "ok=17 degraded=1 failed=0 unknown=2 "
                "topology=segmented domains=4 windows=0 drivers=1/1 "
                "plans=5 nodes=10 rings=0 active=0 user=1 "
                "nodebit_active=1 nodebit_risky=0",
                "[ROOM] snapshot stability=stable "
                "ok=18 degraded=0 failed=0 unknown=2 "
                "topology=segmented domains=4 windows=0 drivers=1/1 "
                "plans=5 nodes=10 rings=0 active=0 user=1 "
                "nodebit_active=1 nodebit_risky=0",
            )
        )
        passing_verdict: dict[str, object] = {
            "schema_version": 1,
            "outcome": "PASS",
            "passed": True,
            "reasons": [],
            "first_failure": None,
            "missing_patterns": [],
        }

        with tempfile.TemporaryDirectory() as temp_dir:
            serial_log = Path(temp_dir) / "serial.log"
            serial_log.write_text(log, encoding="utf-8")
            with (
                patch.object(kernel_lane, "SERIAL_LOG", serial_log),
                patch.object(
                    kernel_lane,
                    "required_smoke_patterns",
                    return_value=[feature, entry],
                ),
                patch.object(
                    kernel_lane,
                    "evaluate_normal_boot",
                    return_value=passing_verdict,
                ),
                self.assertRaisesRegex(
                    ToolError, "SECURITY_SUMMARY_INVALID"
                ),
            ):
                kernel_lane.collect_smoke_summary(
                    "minimal", cpu_profile="default"
                )

        self.assertFalse(passing_verdict["passed"])
        self.assertEqual("FAIL", passing_verdict["outcome"])
        self.assertEqual(
            "SECURITY_SUMMARY_INVALID",
            passing_verdict["reasons"][-1]["code"],  # type: ignore[index]
        )


if __name__ == "__main__":
    unittest.main()
