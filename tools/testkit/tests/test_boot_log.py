from __future__ import annotations

import unittest

from lib.boot_log import parse_boot_log_text
from lib.kernel_lane import (
    CPU_SECURITY_PATTERNS,
    KERNEL_ROOM_BINDING_PATTERN,
    KERNEL_ROOM_MANAGEMENT_PATTERN,
    RING3_ENTRY_AC_HARDENING_PATTERNS,
)


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
ROOM_SNAPSHOT_LINE = (
    "[ROOM] snapshot stability=stable ok=18 degraded=0 failed=0 "
    "unknown=2 topology=segmented domains=4 windows=0 drivers=1/1 "
    "plans=5 nodes=10 rings=0 active=0 user=1 "
    "nodebit_active=1 nodebit_risky=0"
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


class BootLogKernelRoomManagementTests(unittest.TestCase):
    def _summary(self, management: str) -> dict[str, object]:
        return parse_boot_log_text(
            "\n".join(
                (
                    RING3_ENTRY_AC_HARDENING_PATTERNS["default"],
                    management,
                    KERNEL_ROOM_BINDING_PATTERN,
                    ROOM_SNAPSHOT_LINE,
                )
            ),
            "minimal",
            "synthetic.log",
        )

    def test_management_hierarchy_record_is_structured_and_ordered(
        self,
    ) -> None:
        management = self._summary(KERNEL_ROOM_MANAGEMENT_PATTERN)[
            "kernel_room_management"
        ]

        self.assertTrue(management["ready"])
        self.assertTrue(management["semantic_ready"])
        self.assertEqual("PASS", management["status"])
        self.assertEqual(1, management["schema"])
        self.assertEqual(1024, management["struct_size"])
        self.assertEqual(1, management["generation"])
        self.assertEqual(1, management["cells"])
        self.assertEqual(1, management["nodes"])
        self.assertEqual(1, management["bound_nodes"])
        self.assertEqual(2, management["nodebits"])
        self.assertEqual(2, management["bound_nodebits"])
        self.assertEqual(1, management["source_valid"])
        self.assertEqual(1, management["generation_valid"])
        self.assertEqual(1, management["duplicate_rejected"])
        self.assertEqual(1, management["orphan_rejected"])
        self.assertEqual(1, management["unknown_rejected"])
        self.assertEqual(1, management["stale_rejected"])
        self.assertEqual(1, management["overflow_rejected"])
        self.assertEqual(1, management["tail_rejected"])
        self.assertEqual(1, management["observation_only"])
        self.assertEqual(1, management["management_only"])
        self.assertEqual(1, management["record_count"])
        self.assertEqual(1, management["fullmatch_count"])
        self.assertTrue(management["order"]["passed"])

    def test_management_hierarchy_rejects_malformed_duplicate_and_order(
        self,
    ) -> None:
        canonical = KERNEL_ROOM_MANAGEMENT_PATTERN
        mutations = (
            "[ROOM] management hierarchy selftest PASS schema=1",
            canonical.replace("struct_size=1024", "struct_size=1023"),
            canonical.replace("generation=1", "generation=2", 1),
            canonical.replace("bound_nodes=1", "bound_nodes=0"),
            canonical.replace("bound_nodebits=2", "bound_nodebits=1"),
            canonical.replace("source_valid=1", "source_valid=0"),
            canonical.replace("generation_valid=1", "generation_valid=0"),
            canonical.replace("duplicate_rejected=1", "duplicate_rejected=0"),
            canonical.replace("orphan_rejected=1", "orphan_rejected=0"),
            canonical.replace("unknown_rejected=1", "unknown_rejected=0"),
            canonical.replace("stale_rejected=1", "stale_rejected=0"),
            canonical.replace("overflow_rejected=1", "overflow_rejected=0"),
            canonical.replace("tail_rejected=1", "tail_rejected=0"),
            canonical.replace("observation_only=1", "observation_only=0"),
            canonical.replace("management_only=1", "management_only=0"),
            canonical + " extra=1",
            f"  {canonical}",
            f'"{canonical}"',
        )
        for line in mutations:
            with self.subTest(line=line):
                management = self._summary(line)[
                    "kernel_room_management"
                ]
                self.assertFalse(management["ready"])

        for extra in (
            canonical,
            "[ROOM] management hierarchy selftest PARTIAL schema=1",
            "[ROOM] management hierarchy",
        ):
            with self.subTest(extra=extra):
                summary = parse_boot_log_text(
                    "\n".join(
                        (
                            RING3_ENTRY_AC_HARDENING_PATTERNS["default"],
                            canonical,
                            KERNEL_ROOM_BINDING_PATTERN,
                            ROOM_SNAPSHOT_LINE,
                            extra,
                        )
                    ),
                    "minimal",
                    "synthetic.log",
                )
                management = summary["kernel_room_management"]
                self.assertFalse(management["ready"])
                self.assertEqual(2, management["record_count"])
                self.assertTrue(management["duplicate"])

        for reordered in (
            "\n".join(
                (
                    canonical,
                    RING3_ENTRY_AC_HARDENING_PATTERNS["default"],
                    KERNEL_ROOM_BINDING_PATTERN,
                    ROOM_SNAPSHOT_LINE,
                )
            ),
            "\n".join(
                (
                    RING3_ENTRY_AC_HARDENING_PATTERNS["default"],
                    KERNEL_ROOM_BINDING_PATTERN,
                    ROOM_SNAPSHOT_LINE,
                    canonical,
                )
            ),
        ):
            with self.subTest(reordered=reordered):
                management = parse_boot_log_text(
                    reordered, "minimal", "synthetic.log"
                )["kernel_room_management"]
                self.assertFalse(management["ready"])
                self.assertFalse(management["order"]["passed"])


class BootLogKernelRoomBindingTests(unittest.TestCase):
    def _summary(self, binding: str) -> dict[str, object]:
        return parse_boot_log_text(
            "\n".join(
                (
                    RING3_ENTRY_AC_HARDENING_PATTERNS["default"],
                    KERNEL_ROOM_MANAGEMENT_PATTERN,
                    binding,
                    ROOM_SNAPSHOT_LINE,
                )
            ),
            "minimal",
            "synthetic.log",
        )

    def test_source_binding_record_is_structured_and_ordered(self) -> None:
        binding = self._summary(KERNEL_ROOM_BINDING_PATTERN)[
            "kernel_room_binding"
        ]

        self.assertTrue(binding["ready"])
        self.assertTrue(binding["semantic_ready"])
        self.assertEqual("PASS", binding["status"])
        self.assertEqual(1, binding["schema"])
        self.assertEqual(256, binding["struct_size"])
        self.assertEqual(1, binding["binding_generation"])
        self.assertEqual(1, binding["bindings"])
        self.assertEqual(2, binding["capacity"])
        self.assertEqual(2, binding["canonical_namespace"])
        self.assertEqual(101, binding["canonical_id"])
        self.assertEqual(1, binding["canonical_kind"])
        self.assertEqual(1, binding["canonical_generation"])
        self.assertEqual(1, binding["source_namespace"])
        self.assertEqual(1, binding["source_id"])
        self.assertEqual(1, binding["source_instance"])
        self.assertEqual(1, binding["source_generation"])
        self.assertEqual(1, binding["producer_owned"])
        self.assertEqual(1, binding["copied_read"])
        self.assertEqual(1, binding["generation_rollback_rejected"])
        self.assertEqual(1, binding["binding_valid"])
        self.assertEqual(1, binding["observation_only"])
        self.assertEqual(1, binding["management_only"])
        self.assertEqual(1, binding["record_count"])
        self.assertEqual(1, binding["fullmatch_count"])
        self.assertTrue(binding["order"]["passed"])

    def test_source_binding_requires_semantic_management_record(self) -> None:
        invalid_management = KERNEL_ROOM_MANAGEMENT_PATTERN.replace(
            "schema=1", "schema=2", 1
        )
        summary = parse_boot_log_text(
            "\n".join(
                (
                    RING3_ENTRY_AC_HARDENING_PATTERNS["default"],
                    invalid_management,
                    KERNEL_ROOM_BINDING_PATTERN,
                    ROOM_SNAPSHOT_LINE,
                )
            ),
            "minimal",
            "synthetic.log",
        )

        self.assertFalse(summary["kernel_room_management"]["ready"])
        self.assertFalse(summary["kernel_room_binding"]["ready"])

    def test_source_binding_rejects_management_family_sibling(self) -> None:
        summary = parse_boot_log_text(
            "\n".join(
                (
                    RING3_ENTRY_AC_HARDENING_PATTERNS["default"],
                    KERNEL_ROOM_MANAGEMENT_PATTERN,
                    KERNEL_ROOM_BINDING_PATTERN,
                    ROOM_SNAPSHOT_LINE,
                    "[ROOM] management hierarchy selftest PARTIAL schema=1",
                )
            ),
            "minimal",
            "synthetic.log",
        )
        management = summary["kernel_room_management"]

        self.assertFalse(management["ready"])
        self.assertEqual(2, management["record_count"])
        self.assertTrue(management["duplicate"])
        self.assertFalse(summary["kernel_room_binding"]["ready"])

    def test_source_binding_rejects_malformed_duplicate_and_order(self) -> None:
        canonical = KERNEL_ROOM_BINDING_PATTERN
        mutations = (
            "[ROOM] source binding selftest PASS schema=1",
            canonical.replace("schema=1", "schema=01", 1),
            canonical.replace("struct_size=256", "struct_size=255"),
            canonical.replace("canonical_id=101", "canonical_id=102"),
            canonical.replace("source_instance=1", "source_instance=0"),
            canonical.replace("source_generation=1", "source_generation=2"),
            canonical.replace("kind_match=1", "kind_match=0"),
            canonical.replace("role_match=1", "role_match=0"),
            canonical.replace("producer_owned=1", "producer_owned=0"),
            canonical.replace("copied_read=1", "copied_read=0"),
            canonical.replace("stale_rejected=1", "stale_rejected=0"),
            canonical.replace("binding_valid=1", "binding_valid=0"),
            canonical.replace("observation_only=1", "observation_only=0"),
            canonical + " extra=1",
            f"  {canonical}",
            f'"{canonical}"',
        )
        for line in mutations:
            with self.subTest(line=line):
                binding = self._summary(line)["kernel_room_binding"]
                self.assertFalse(binding["ready"])

        for extra in (
            canonical,
            "[ROOM] source binding selftest PARTIAL schema=1",
            "[ROOM] source binding",
        ):
            with self.subTest(extra=extra):
                summary = parse_boot_log_text(
                    "\n".join(
                        (
                            RING3_ENTRY_AC_HARDENING_PATTERNS["default"],
                            KERNEL_ROOM_MANAGEMENT_PATTERN,
                            canonical,
                            ROOM_SNAPSHOT_LINE,
                            extra,
                        )
                    ),
                    "minimal",
                    "synthetic.log",
                )
                binding = summary["kernel_room_binding"]
                self.assertFalse(binding["ready"])
                self.assertEqual(2, binding["record_count"])
                self.assertTrue(binding["duplicate"])

        for reordered in (
            "\n".join(
                (
                    RING3_ENTRY_AC_HARDENING_PATTERNS["default"],
                    canonical,
                    KERNEL_ROOM_MANAGEMENT_PATTERN,
                    ROOM_SNAPSHOT_LINE,
                )
            ),
            "\n".join(
                (
                    RING3_ENTRY_AC_HARDENING_PATTERNS["default"],
                    KERNEL_ROOM_MANAGEMENT_PATTERN,
                    ROOM_SNAPSHOT_LINE,
                    canonical,
                )
            ),
        ):
            with self.subTest(reordered=reordered):
                binding = parse_boot_log_text(
                    reordered, "minimal", "synthetic.log"
                )["kernel_room_binding"]
                self.assertFalse(binding["ready"])
                self.assertFalse(binding["order"]["passed"])


class BootLogSecurityTests(unittest.TestCase):
    def test_security_summary_is_profile_bound_and_exact(self) -> None:
        room = ROOM_SNAPSHOT_LINE
        for cpu_profile in ("default", "max-smap"):
            with self.subTest(cpu_profile=cpu_profile):
                log = "\n".join(
                    (
                        CPU_SECURITY_PATTERNS[cpu_profile],
                        RING3_ENTRY_AC_HARDENING_PATTERNS[cpu_profile],
                        room,
                    )
                )
                summary = parse_boot_log_text(
                    log, "minimal", cpu_profile=cpu_profile
                )
                security = summary["security"]
                self.assertTrue(security["ready"])
                self.assertTrue(summary["kernel_room"]["ready"])
                self.assertTrue(security["profile_match"])
                self.assertEqual(
                    1, security["feature"]["record_count"]
                )
                self.assertEqual(
                    1, security["feature"]["fullmatch_count"]
                )
                self.assertEqual(
                    1,
                    security["entry_ac_hardening"]["record_count"],
                )
                self.assertEqual(
                    1,
                    security["entry_ac_hardening"]["fullmatch_count"],
                )
                self.assertEqual(1, security["room"]["record_count"])
                self.assertEqual(
                    1, security["room"]["fullmatch_count"]
                )
                self.assertTrue(security["room"]["semantic_ready"])
                self.assertTrue(security["order"]["passed"])

                other = "max-smap" if cpu_profile == "default" else "default"
                mismatch = parse_boot_log_text(
                    log, "minimal", cpu_profile=other
                )["security"]
                self.assertFalse(mismatch["ready"])
                self.assertFalse(mismatch["profile_match"])

    def test_security_summary_rejects_malformed_and_duplicate_rows(self) -> None:
        feature = CPU_SECURITY_PATTERNS["default"]
        entry = RING3_ENTRY_AC_HARDENING_PATTERNS["default"]
        room = ROOM_SNAPSHOT_LINE
        cases = (
            f"{feature}\n{entry} extra=1\n{room}",
            f"{feature}\n{entry}\n{entry}\n{room}",
            f"{feature}\n{feature}\n{entry}\n{room}",
            f"{feature}\n{entry}\n{room}\n{feature} extra=1",
            f"{feature}\n{entry.replace('gate_mismatch=0', 'gate_mismatch=1')}\n{room}",
            f"{feature.replace('smap=0', 'smap=1')}\n{entry}\n{room}",
            f"{entry}\n{feature}\n{room}",
            f"{entry}\n{room}\n{feature}",
            f"{feature}\n{entry}\n{room}\n{room}",
            f"{feature}\n{entry}\n[ROOM] snapshot stability=stable",
            f"{feature}\n{entry}\n{room.replace('ok=18', 'ok=' + ('9' * 100))}",
            f"{feature}\n{entry}\n{room.replace('ok=18', 'ok=9٢')}",
            f"{feature}\n{entry}\n{room.replace('ok=18', 'ok=9２')}",
            f"{feature}\n{entry}\n{room.replace('failed=0', 'failed=1')}",
            f"{feature}\n{entry}\n{room.replace('unknown=2 ', '')}",
            f"{feature}\n{entry}\n{room} PASSFAIL",
            f"{feature}\n{entry}\n{room} PARTIAL",
            f"{feature}\n{entry}\n{room} extra=1",
            f"{feature}\n{entry}\n{room.replace('topology=segmented', 'topology=unknown')}",
            f"{feature}\n{entry.replace('schema=1', 'schema=01')}\n{room}",
            f"{feature}\n{entry.replace('schema=1', 'schema=١')}\n{room}",
            f"{feature}\n{entry.replace('gate_mismatch=0', 'gate_mismatch=000')}\n{room}",
            f"{feature}\n{entry.replace('schema=1', 'schema=' + ('9' * 100))}\n{room}",
        )
        for log in cases:
            with self.subTest(log=log):
                security = parse_boot_log_text(
                    log, "minimal", cpu_profile="default"
                )["security"]
                self.assertFalse(security["ready"])


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
