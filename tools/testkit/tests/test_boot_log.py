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
PROCESS_TRAP_SNAPSHOT_LINE = (
    "[PROC] trap evidence snapshot PASS schema=1 captures=2 pid_a=1 "
    "slot_a=0 seq_a=1 valid_a=1 owner_a=1 frame_a=1 cr3_a=1 rsp0_a=1 "
    "pid_b=2 slot_b=1 seq_b=2 valid_b=1 owner_b=1 frame_b=1 cr3_b=1 "
    "rsp0_b=1 distinct_storage=1 current_pid=0 stale_owner=0 resume_ready=0"
)
PROCESS_EVENT_JOURNAL_LINE = (
    "[PROC] process event journal PASS schema=1 events=6 lifecycle=4 "
    "captures=2 seqs=1,2,3,4,5,6 kinds=1,2,3,1,2,3 "
    "reasons=1,2,3,1,2,3 from_pids=0,1,1,0,2,2 "
    "to_pids=1,1,0,2,2,0 slots=0,0,0,1,1,1 "
    "generations=1,1,1,1,1,1 capture_seqs=0,1,1,0,2,2 "
    "owner_ok=1,1,1,1,1,1 cr3_ok=1,1,1,1,1,1 "
    "rsp0_ok=1,1,1,1,1,1 if0=1,1,1,1,1,1 "
    "snapshot_refs=0,1,1,0,1,1 outcomes=1,1,1,1,1,1 "
    "capture_seq_separate=1 current_pid=0 stale_owner=0 dropped=0 "
    "overflow=0 evidence_only=1 switch_events=0 resume_ready=0"
)
PRESSURE_LINE = (
    "[PRESSURE] tracker selftest PASS schema=1 planes=3 max_levels=4 "
    "active_levels=2 balanced=1 hotspot=1 overlap=1 gate_mask=1 "
    "observation_only=1"
)
RESOURCE_LINE = (
    "[RESOURCE] ledger selftest PASS schema=1 kinds=5 units=2 entries=5 "
    "capacity=8 source_flags=31 limit_kinds=5 used_kinds=5 "
    "high_water_kinds=1 denied_kinds=0 owners_unattributed=1 "
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


class BootLogProcessTrapSnapshotTests(unittest.TestCase):
    def test_process_trap_snapshot_record_is_structured(self) -> None:
        summary = parse_boot_log_text(
            PROCESS_TRAP_SNAPSHOT_LINE, "full", "synthetic.log"
        )
        snapshot = summary["process_trap_snapshot"]

        self.assertTrue(snapshot["ready"])
        self.assertEqual("PASS", snapshot["status"])
        self.assertEqual(1, snapshot["schema"])
        self.assertEqual(2, snapshot["captures"])
        self.assertEqual(1, snapshot["seq_a"])
        self.assertEqual(2, snapshot["seq_b"])
        self.assertEqual(0, snapshot["resume_ready"])
        self.assertEqual(1, snapshot["record_count"])
        self.assertEqual(1, snapshot["fullmatch_count"])

    def test_missing_or_invalid_process_trap_snapshot_is_not_ready(self) -> None:
        for line in (
            "[USER] bootstrap process pair PASS runs=2",
            "[PROC] trap evidence snapshot PASS schema=1 captures=2",
            PROCESS_TRAP_SNAPSHOT_LINE.replace("owner_b=1", "owner_b=0"),
            PROCESS_TRAP_SNAPSHOT_LINE.replace("seq_b=2", "seq_b=1"),
            PROCESS_TRAP_SNAPSHOT_LINE.replace(
                "current_pid=0", "current_pid=2"
            ),
            PROCESS_TRAP_SNAPSHOT_LINE.replace(
                "stale_owner=0", "stale_owner=1"
            ),
            PROCESS_TRAP_SNAPSHOT_LINE.replace(
                "resume_ready=0", "resume_ready=1"
            ),
            f"  {PROCESS_TRAP_SNAPSHOT_LINE}",
            f'"{PROCESS_TRAP_SNAPSHOT_LINE}"',
        ):
            with self.subTest(line=line):
                summary = parse_boot_log_text(line, "full", "synthetic.log")
                self.assertFalse(summary["process_trap_snapshot"]["ready"])

    def test_process_trap_snapshot_duplicates_are_not_ready(self) -> None:
        for duplicate in (
            PROCESS_TRAP_SNAPSHOT_LINE,
            "[PROC] trap evidence snapshot PASS schema=1 captures=2",
        ):
            with self.subTest(duplicate=duplicate):
                summary = parse_boot_log_text(
                    f"{PROCESS_TRAP_SNAPSHOT_LINE}\n{duplicate}",
                    "full",
                    "synthetic.log",
                )
                snapshot = summary["process_trap_snapshot"]
                self.assertFalse(snapshot["ready"])
                self.assertEqual(2, snapshot["record_count"])
                self.assertTrue(snapshot["duplicate"])


class BootLogProcessEventJournalTests(unittest.TestCase):
    def test_process_event_journal_is_structured(self) -> None:
        summary = parse_boot_log_text(
            PROCESS_EVENT_JOURNAL_LINE, "full", "synthetic.log"
        )
        journal = summary["process_event_journal"]

        self.assertTrue(journal["ready"])
        self.assertEqual("PASS", journal["status"])
        self.assertEqual(1, journal["schema"])
        self.assertEqual(6, journal["event_count"])
        self.assertEqual(4, journal["lifecycle"])
        self.assertEqual(2, journal["captures"])
        self.assertEqual([1, 2, 3, 4, 5, 6], journal["seqs"])
        self.assertEqual([0, 1, 1, 0, 2, 2], journal["capture_seqs"])
        self.assertTrue(journal["ordered"])
        self.assertTrue(journal["lengths_match"])
        self.assertEqual(6, len(journal["events"]))
        self.assertEqual(
            {
                "sequence": 2,
                "kind": 2,
                "reason": 2,
                "from_pid": 1,
                "to_pid": 1,
                "slot": 0,
                "generation": 1,
                "capture_sequence": 1,
                "owner_ok": 1,
                "cr3_ok": 1,
                "rsp0_ok": 1,
                "if0": 1,
                "snapshot_ref": 1,
                "outcome": 1,
            },
            journal["events"][1],
        )
        self.assertEqual(1, journal["record_count"])
        self.assertEqual(1, journal["fullmatch_count"])

    def test_process_event_journal_missing_or_aggregate_only_is_not_ready(self) -> None:
        aggregate_only = (
            "[PROC] process event journal PASS schema=1 events=6 lifecycle=4 "
            "captures=2 current_pid=0 stale_owner=0 dropped=0 overflow=0 "
            "evidence_only=1 switch_events=0 resume_ready=0"
        )
        for line in (
            "[PROC] trap evidence snapshot PASS schema=1 captures=2",
            "[PROC] process event journal PASS schema=1 events=6",
            aggregate_only,
            f"  {PROCESS_EVENT_JOURNAL_LINE}",
            f'"{PROCESS_EVENT_JOURNAL_LINE}"',
        ):
            with self.subTest(line=line):
                summary = parse_boot_log_text(line, "full", "synthetic.log")
                self.assertFalse(summary["process_event_journal"]["ready"])

    def test_process_event_journal_mutations_are_not_ready(self) -> None:
        mutations = (
            PROCESS_EVENT_JOURNAL_LINE.replace(
                "seqs=1,2,3,4,5,6", "seqs=1,3,2,4,5,6"
            ),
            PROCESS_EVENT_JOURNAL_LINE.replace(
                "kinds=1,2,3,1,2,3", "kinds=1,2,99,1,2,3"
            ),
            PROCESS_EVENT_JOURNAL_LINE.replace(
                "reasons=1,2,3,1,2,3", "reasons=1,2,99,1,2,3"
            ),
            PROCESS_EVENT_JOURNAL_LINE.replace(
                "outcomes=1,1,1,1,1,1", "outcomes=1,1,1,1,1,99"
            ),
            PROCESS_EVENT_JOURNAL_LINE.replace(
                "owner_ok=1,1,1,1,1,1", "owner_ok=1,1,1,1,1,0"
            ),
            PROCESS_EVENT_JOURNAL_LINE.replace(
                "capture_seqs=0,1,1,0,2,2", "capture_seqs=0,1,0,2,2"
            ),
            PROCESS_EVENT_JOURNAL_LINE.replace(
                "snapshot_refs=0,1,1,0,1,1", "snapshot_refs=0,1,0,0,1,1"
            ),
            PROCESS_EVENT_JOURNAL_LINE.replace(
                "stale_owner=0", "stale_owner=1"
            ),
            PROCESS_EVENT_JOURNAL_LINE.replace(
                "capture_seq_separate=1", "capture_seq_separate=0"
            ),
            PROCESS_EVENT_JOURNAL_LINE.replace(
                "events=6", "events=999999999999999999999999"
            ),
            PROCESS_EVENT_JOURNAL_LINE.replace(
                "kinds=1,2,3,1,2,3",
                "kinds=1,2,3,1,2,3 kinds=1,2,3,1,2,3",
            ),
        )
        for line in mutations:
            with self.subTest(line=line):
                summary = parse_boot_log_text(line, "full", "synthetic.log")
                self.assertFalse(summary["process_event_journal"]["ready"])

        unknown_kind = parse_boot_log_text(
            PROCESS_EVENT_JOURNAL_LINE.replace(
                "kinds=1,2,3,1,2,3", "kinds=1,2,99,1,2,3"
            ),
            "full",
            "synthetic.log",
        )["process_event_journal"]
        unknown_reason = parse_boot_log_text(
            PROCESS_EVENT_JOURNAL_LINE.replace(
                "reasons=1,2,3,1,2,3", "reasons=1,2,99,1,2,3"
            ),
            "full",
            "synthetic.log",
        )["process_event_journal"]
        unknown_outcome = parse_boot_log_text(
            PROCESS_EVENT_JOURNAL_LINE.replace(
                "outcomes=1,1,1,1,1,1", "outcomes=1,1,1,1,1,99"
            ),
            "full",
            "synthetic.log",
        )["process_event_journal"]
        self.assertEqual([99], unknown_kind["unknown_kind_ids"])
        self.assertEqual([99], unknown_reason["unknown_reason_ids"])
        self.assertEqual([99], unknown_outcome["unknown_outcome_ids"])

    def test_process_event_journal_duplicates_are_not_ready(self) -> None:
        for duplicate in (
            PROCESS_EVENT_JOURNAL_LINE,
            "[PROC] process event journal PASS schema=1 events=6",
        ):
            with self.subTest(duplicate=duplicate):
                summary = parse_boot_log_text(
                    f"{PROCESS_EVENT_JOURNAL_LINE}\n{duplicate}",
                    "full",
                    "synthetic.log",
                )
                journal = summary["process_event_journal"]
                self.assertFalse(journal["ready"])
                self.assertEqual(2, journal["record_count"])
                self.assertTrue(journal["duplicate"])

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
            f"  {PRESSURE_LINE}",
            f'"{PRESSURE_LINE}"',
        ):
            with self.subTest(line=line):
                summary = parse_boot_log_text(line, "full", "synthetic.log")
                self.assertTrue(summary["pressure"]["checkpoint_seen"])
                self.assertFalse(summary["pressure"]["ready"])


class BootLogResourceTests(unittest.TestCase):
    def test_resource_record_is_structured(self) -> None:
        summary = parse_boot_log_text(RESOURCE_LINE, "full", "synthetic.log")
        resource = summary["resource"]

        self.assertTrue(resource["ready"])
        self.assertEqual("PASS", resource["status"])
        self.assertEqual(1, resource["schema"])
        self.assertEqual(5, resource["kinds"])
        self.assertEqual(5, resource["entries"])
        self.assertEqual(1, resource["high_water_kinds"])
        self.assertEqual(0, resource["denied_kinds"])
        self.assertEqual(1, resource["observation_only"])

    def test_incomplete_or_mutating_resource_is_not_ready(self) -> None:
        for line in (
            "[RESOURCE] ledger selftest PASS schema=1 kinds=5",
            RESOURCE_LINE.replace("observation_only=1", "observation_only=0"),
            f"{RESOURCE_LINE} apply_enabled=1",
            f"  {RESOURCE_LINE}",
            f'"{RESOURCE_LINE}"',
        ):
            with self.subTest(line=line):
                summary = parse_boot_log_text(line, "full", "synthetic.log")
                self.assertTrue(summary["resource"]["checkpoint_seen"])
                self.assertFalse(summary["resource"]["ready"])


if __name__ == "__main__":
    unittest.main()
