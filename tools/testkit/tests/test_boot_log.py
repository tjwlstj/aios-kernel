from __future__ import annotations

import unittest

from lib.boot_log import parse_boot_log_text


PROCESS_PAIR_LINE = (
    "[USER] bootstrap process pair PASS runs=2 order=1,2 "
    "pid_a=1 slot_a=0 pid_b=2 slot_b=1 distinct_pid=1 distinct_slot=1 "
    "distinct_cr3=1 distinct_backing=1 distinct_stack=1 "
    "int80_a=3 int80_b=3 between_clean=1 current_pid=0 last_pid=2 "
    "rsp0_publishes=2 rsp0_restores=2 tss_rsp0_baseline=1 both_restored=1"
)
PRESSURE_LINE = (
    "[PRESSURE] tracker selftest PASS schema=1 planes=3 max_levels=4 "
    "active_levels=2 balanced=1 hotspot=1 overlap=1 gate_mask=1 "
    "observation_only=1"
)


class BootLogProcessPairTests(unittest.TestCase):
    def test_process_pair_record_is_structured(self) -> None:
        summary = parse_boot_log_text(PROCESS_PAIR_LINE, "full", "synthetic.log")
        pair = summary["process_pair"]

        self.assertTrue(pair["ready"])
        self.assertEqual("PASS", pair["status"])
        self.assertEqual("1,2", pair["order"])
        self.assertEqual(2, pair["runs"])
        self.assertEqual(2, pair["pid_b"])
        self.assertEqual(1, pair["distinct_cr3"])
        self.assertEqual(1, pair["between_clean"])
        self.assertEqual(1, pair["both_restored"])

    def test_missing_process_pair_record_is_not_ready(self) -> None:
        summary = parse_boot_log_text(
            "[USER] bootstrap process stack PASS pid=1",
            "full",
            "synthetic.log",
        )

        self.assertFalse(summary["process_pair"]["ready"])

    def test_incomplete_process_pair_record_is_not_ready(self) -> None:
        summary = parse_boot_log_text(
            "[USER] bootstrap process pair PASS runs=2",
            "full",
            "synthetic.log",
        )

        self.assertTrue(summary["process_pair"]["checkpoint_seen"])
        self.assertFalse(summary["process_pair"]["ready"])


class BootLogPressureTests(unittest.TestCase):
    def test_pressure_record_is_structured(self) -> None:
        summary = parse_boot_log_text(PRESSURE_LINE, "full", "synthetic.log")
        pressure = summary["pressure"]

        self.assertTrue(pressure["ready"])
        self.assertEqual("PASS", pressure["status"])
        self.assertEqual(1, pressure["schema"])
        self.assertEqual(3, pressure["planes"])
        self.assertEqual(4, pressure["max_levels"])
        self.assertEqual(2, pressure["active_levels"])
        self.assertEqual(1, pressure["observation_only"])

    def test_incomplete_or_apply_capable_pressure_is_not_ready(self) -> None:
        for line in (
            "[PRESSURE] tracker selftest PASS schema=1 planes=3",
            PRESSURE_LINE.replace("observation_only=1", "observation_only=0"),
            PRESSURE_LINE.replace("gate_mask=1", "gate_mask=0"),
        ):
            with self.subTest(line=line):
                summary = parse_boot_log_text(line, "full", "synthetic.log")
                self.assertTrue(summary["pressure"]["checkpoint_seen"])
                self.assertFalse(summary["pressure"]["ready"])


if __name__ == "__main__":
    unittest.main()
