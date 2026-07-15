from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from lib import shell_lane
from lib.shell_lane import (
    DEFAULT_EXCHANGES,
    SHELL_REBOOT_MARKER,
    SerialSession,
    expectation_matches,
    expectations_match,
    shell_result_passed,
)


class ShellExpectationTests(unittest.TestCase):
    def test_exact_values_require_token_boundaries(self) -> None:
        self.assertTrue(expectation_matches("address_space_ready=1 ", "address_space_ready=1"))
        self.assertFalse(expectation_matches("address_space_ready=10 ", "address_space_ready=1"))
        self.assertFalse(expectation_matches("failed=00 ", "failed=0"))
        self.assertFalse(expectation_matches("io_failed=0 ", "failed=0"))
        self.assertFalse(expectation_matches("stability=stable_bad ", "stability=stable"))

    def test_equals_suffix_requires_a_nonspace_value(self) -> None:
        self.assertTrue(expectation_matches("[STATE] pong ticks=123\n", "[STATE] pong ticks="))
        self.assertFalse(expectation_matches("[STATE] pong ticks=\n", "[STATE] pong ticks="))
        self.assertFalse(expectation_matches("[STATE] pong ticks= 123\n", "[STATE] pong ticks="))
        self.assertFalse(expectation_matches("not_ticks=123\n", "ticks="))

    def test_structured_markers_are_line_anchored(self) -> None:
        self.assertFalse(
            expectation_matches(
                "WARN expected [SHELL] Interactive shell started but absent\n",
                "[SHELL] Interactive shell started",
            )
        )
        self.assertFalse(
            expectation_matches(
                "ERROR missing [SHELL] reboot requested marker\n",
                SHELL_REBOOT_MARKER,
            )
        )
        self.assertFalse(
            expectation_matches(
                "DEBUG [STATE] health stability=stable\n",
                "[STATE] health stability=stable",
            )
        )

    def test_state_health_requires_stable_zero_counters(self) -> None:
        exchange = next(item for item in DEFAULT_EXCHANGES if item["command"] == "state health")
        expectations = list(exchange["expect"])
        healthy = (
            "[STATE] health stability=stable ok=18 degraded=0 failed=0 "
            "unknown=2 io_degraded=0 autonomy=1 risky_io=1\n"
        )
        self.assertTrue(expectations_match(healthy, expectations))

        for invalid in (
            healthy.replace("stability=stable", "stability=degraded"),
            healthy.replace("degraded=0", "degraded=1", 1),
            healthy.replace("failed=0", "failed=00", 1),
            healthy.replace("io_degraded=0", "io_degraded=1", 1),
            healthy.replace("degraded=0", "degraded=1 degraded=0", 1),
            healthy.replace("failed=0", "failed=1 failed=0", 1),
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(expectations_match(invalid, expectations))

        split_evidence = (
            "[STATE] health stability=stable ok=16 degraded=1 failed=1 autonomy=0\n"
            "[DEBUG] prior counters degraded=0 failed=0 autonomy=1\n"
        )
        self.assertFalse(expectations_match(split_evidence, expectations))


class ShellVerdictTests(unittest.TestCase):
    def test_clean_exit_is_required_for_pass(self) -> None:
        self.assertTrue(shell_result_passed(True, [], True, True, True))
        self.assertFalse(shell_result_passed(True, [], True, False, True))
        self.assertFalse(shell_result_passed(False, [], True, True, True))
        self.assertFalse(shell_result_passed(True, ["state health"], True, True, True))

    def test_reboot_ack_and_boot_verdict_are_required_for_pass(self) -> None:
        self.assertFalse(shell_result_passed(True, [], False, True, True))
        self.assertFalse(shell_result_passed(True, [], True, True, False))

    def test_reader_drain_precedes_final_marker_match(self) -> None:
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb", buffering=0)

        class FakeProcess:
            stdout = reader

            @staticmethod
            def poll() -> int:
                return 0

        session = SerialSession(FakeProcess())  # type: ignore[arg-type]
        try:
            os.write(write_fd, f"{SHELL_REBOOT_MARKER}\n".encode("ascii"))
        finally:
            os.close(write_fd)

        try:
            self.assertTrue(session.drain())
            self.assertTrue(expectation_matches(session.text(), SHELL_REBOOT_MARKER))
        finally:
            reader.close()

    def test_launch_error_overwrites_stale_pass_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            build_dir = Path(temp_dir)
            smoke_dir = build_dir / "shell-smoke"
            smoke_dir.mkdir()
            (build_dir / "aios-kernel.iso").write_bytes(b"iso")
            (smoke_dir / "transcript.log").write_text("stale", encoding="utf-8")
            (smoke_dir / "summary.json").write_text(
                json.dumps({"passed": True}),
                encoding="utf-8",
            )

            with (
                patch.object(shell_lane, "BUILD_DIR", build_dir),
                patch.object(shell_lane, "SHELL_SMOKE_DIR", smoke_dir),
                patch.object(shell_lane, "find_qemu", return_value="missing-qemu"),
                patch.object(shell_lane.subprocess, "Popen", side_effect=FileNotFoundError("launch failed")),
                patch.object(shell_lane, "print_step"),
            ):
                with self.assertRaises(FileNotFoundError):
                    shell_lane.run_shell_lane(timeout_sec=1, strict=True, skip_build=True)

            transcript = (smoke_dir / "transcript.log").read_text(encoding="utf-8")
            summary = json.loads((smoke_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual("", transcript)
            self.assertFalse(summary["passed"])
            self.assertFalse(summary["boot_verdict"]["passed"])
            self.assertEqual("qemu-launch-error", summary["termination"]["reason"])
            self.assertFalse(summary["termination"]["timed_out"])
            self.assertEqual("FileNotFoundError", summary["error"]["type"])


if __name__ == "__main__":
    unittest.main()
