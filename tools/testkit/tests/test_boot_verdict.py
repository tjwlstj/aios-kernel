from __future__ import annotations

import unittest

from lib.boot_verdict import evaluate_normal_boot
from lib.kernel_lane import (
    CPU_SECURITY_PATTERNS,
    KERNEL_ROOM_MANAGEMENT_PATTERN,
    PRESSURE_SELFTEST_PATTERN,
    PROCESS_TRAP_SNAPSHOT_PATTERN,
    PROCESS_EVENT_JOURNAL_PATTERN,
    RESOURCE_SELFTEST_PATTERN,
    RING3_ENTRY_AC_HARDENING_PATTERNS,
    TRAPFRAME_CONTRACT_PATTERN,
    USER_TRAP_CAPTURE_PATTERN,
    build_qemu_smoke_command,
    required_smoke_patterns,
)


REQUIRED_PATTERNS = [
    "[BOOT] profile-required",
    KERNEL_ROOM_MANAGEMENT_PATTERN,
]
ROOM_SNAPSHOT_LINE = (
    "[ROOM] snapshot stability=stable ok=18 degraded=0 failed=0 "
    "unknown=2 topology=segmented domains=4 windows=0 drivers=1/1 "
    "plans=5 nodes=10 rings=0 active=0 user=1 "
    "nodebit_active=1 nodebit_risky=0"
)


def normal_lines() -> list[str]:
    return [
        "[BOOT] profile-required",
        "[USER] Ring3 scaffold ready=1 tr=0x28",
        "[PROC] bootstrap ownership selftest PASS slots=2",
        "[USER] ring3 exec PASS exit_code=42",
        "[USER] private address space exec PASS slot=0",
        "[USER] bootstrap process stack PASS pid=1",
        "[USER] bootstrap process pair PASS runs=2",
        "[TRAP] user frame capture PASS pid_a=1 pid_b=2",
        "[PROC] trap evidence snapshot PASS schema=1 captures=2",
        "[PROC] process event journal PASS schema=1 events=6",
        RING3_ENTRY_AC_HARDENING_PATTERNS["default"],
        KERNEL_ROOM_MANAGEMENT_PATTERN,
        ROOM_SNAPSHOT_LINE,
        "[HEALTH] stability=stable ok=18 degraded=0 failed=0 unknown=2",
        "=== AIOS Kernel Ready ===",
        "[KERNEL] Boot complete. Launching interactive shell...",
        "[SHELL] Interactive shell started",
    ]


def evaluate(lines: list[str]) -> dict[str, object]:
    return evaluate_normal_boot("\n".join(lines), REQUIRED_PATTERNS)


def reason_codes(verdict: dict[str, object]) -> list[str]:
    return [reason["code"] for reason in verdict["reasons"]]


class NormalBootVerdictTests(unittest.TestCase):
    def test_complete_normal_log_passes(self) -> None:
        verdict = evaluate(normal_lines())

        self.assertEqual("PASS", verdict["outcome"])
        self.assertTrue(verdict["passed"])
        self.assertTrue(verdict["health"]["passed"])
        self.assertTrue(verdict["checkpoints"]["passed"])
        self.assertEqual("not-evaluated", verdict["termination"]["reason"])

    def test_cpu_profile_commands_are_orthogonal_to_smoke_profiles(self) -> None:
        default_cmd = build_qemu_smoke_command(
            "qemu", "kernel.iso", "file:serial.log", "minimal", "default"
        )
        max_cmd = build_qemu_smoke_command(
            "qemu", "kernel.iso", "file:serial.log", "minimal", "max-smap"
        )

        self.assertNotIn("-cpu", default_cmd)
        self.assertEqual(1, max_cmd.count("-cpu"))
        cpu_index = max_cmd.index("-cpu")
        self.assertEqual("max", max_cmd[cpu_index + 1])
        self.assertIn("-nic", default_cmd)
        self.assertIn("-nic", max_cmd)

    def test_security_cpu_profiles_require_distinct_exact_evidence(self) -> None:
        for cpu_profile in ("default", "max-smap"):
            with self.subTest(cpu_profile=cpu_profile):
                lines = normal_lines()
                lines.insert(1, CPU_SECURITY_PATTERNS["default"])
                if cpu_profile == "max-smap":
                    lines = [
                        CPU_SECURITY_PATTERNS["max-smap"]
                        if line == CPU_SECURITY_PATTERNS["default"]
                        else RING3_ENTRY_AC_HARDENING_PATTERNS["max-smap"]
                        if line == RING3_ENTRY_AC_HARDENING_PATTERNS["default"]
                        else line
                        for line in lines
                    ]
                required = [
                    CPU_SECURITY_PATTERNS[cpu_profile],
                    RING3_ENTRY_AC_HARDENING_PATTERNS[cpu_profile],
                ]
                verdict = evaluate_normal_boot("\n".join(lines), required)
                self.assertTrue(verdict["passed"])

                other = "max-smap" if cpu_profile == "default" else "default"
                mismatched = evaluate_normal_boot(
                    "\n".join(lines),
                    [
                        CPU_SECURITY_PATTERNS[other],
                        RING3_ENTRY_AC_HARDENING_PATTERNS[other],
                    ],
                )
                self.assertFalse(mismatched["passed"])
                self.assertIn(
                    "MISSING_REQUIRED_PATTERNS", reason_codes(mismatched)
                )

    def test_security_feature_entry_room_order_is_fail_closed(self) -> None:
        feature = CPU_SECURITY_PATTERNS["default"]
        entry = RING3_ENTRY_AC_HARDENING_PATTERNS["default"]
        required = [feature, entry]
        ordered = normal_lines()
        ordered.insert(1, feature)
        self.assertTrue(
            evaluate_normal_boot("\n".join(ordered), required)["passed"]
        )

        feature_after_entry: list[str] = []
        for line in ordered:
            if line == feature:
                continue
            feature_after_entry.append(line)
            if line == entry:
                feature_after_entry.append(feature)
        moved_after_shell = [
            line for line in ordered if line != feature
        ] + [feature]
        for reordered in (feature_after_entry, moved_after_shell):
            with self.subTest(reordered=reordered[-3:]):
                verdict = evaluate_normal_boot(
                    "\n".join(reordered), required
                )
                self.assertFalse(verdict["passed"])
                self.assertIn(
                    "SECURITY_CHECKPOINT_CHAIN_INVALID",
                    reason_codes(verdict),
                )
                self.assertFalse(
                    verdict["security_checkpoints"]["passed"]
                )
                self.assertFalse(verdict["checkpoints"]["passed"])

        duplicate_family = [
            *ordered,
            f"{feature} extra=1",
        ]
        verdict = evaluate_normal_boot(
            "\n".join(duplicate_family), required
        )
        self.assertFalse(verdict["passed"])
        self.assertIn("EVIDENCE_RECORD_INVALID", reason_codes(verdict))

        duplicate_room_family = []
        for line in ordered:
            if line.startswith("[ROOM] snapshot stability=stable"):
                duplicate_room_family.append(
                    ROOM_SNAPSHOT_LINE.replace(
                        "stability=stable", "stability=degraded"
                    ).replace("ok=18", "ok=17").replace(
                        "degraded=0", "degraded=1"
                    )
                )
            duplicate_room_family.append(line)
        verdict = evaluate_normal_boot(
            "\n".join(duplicate_room_family), required
        )
        self.assertFalse(verdict["passed"])
        self.assertIn("EVIDENCE_RECORD_INVALID", reason_codes(verdict))

    def test_room_snapshot_requires_full_stable_semantics(self) -> None:
        canonical = ROOM_SNAPSHOT_LINE
        mutations = (
            "[ROOM] snapshot stability=stable",
            canonical.replace("ok=18", "ok=0"),
            canonical.replace("ok=18", "ok=" + ("9" * 100)),
            canonical.replace("ok=18", "ok=9\u0662"),
            canonical.replace("ok=18", "ok=9\uff12"),
            canonical.replace("degraded=0", "degraded=1"),
            canonical.replace("failed=0", "failed=1"),
            canonical.replace("unknown=2 ", ""),
            canonical.replace("topology=segmented", "topology=unknown"),
            canonical.replace("domains=4", "domains=0"),
            canonical.replace("drivers=1/1", "drivers=2/1"),
            canonical.replace("rings=0 active=0", "rings=0 active=1"),
            canonical.replace("user=1", "user=0"),
            canonical.replace(
                "nodebit_active=1 nodebit_risky=0",
                "nodebit_active=1 nodebit_risky=2",
            ),
            canonical.replace(
                "ok=18 degraded=0", "degraded=0 ok=18"
            ),
            canonical + " PASSFAIL",
            canonical + " PARTIAL",
            canonical + " extra=1",
        )
        for invalid in mutations:
            with self.subTest(invalid=invalid):
                lines = [
                    invalid if line == canonical else line
                    for line in normal_lines()
                ]
                verdict = evaluate(lines)
                self.assertFalse(verdict["passed"])
                self.assertIn(
                    "EVIDENCE_RECORD_INVALID", reason_codes(verdict)
                )

    def test_kernel_room_management_contract_is_exact_and_fails_closed(
        self,
    ) -> None:
        canonical = KERNEL_ROOM_MANAGEMENT_PATTERN
        mutations = (
            "[ROOM] management hierarchy selftest PASS schema=1",
            canonical.replace("struct_size=1024", "struct_size=1023"),
            canonical.replace("generation=1", "generation=2", 1),
            canonical.replace("cells=1", "cells=0"),
            canonical.replace("nodes=1", "nodes=0"),
            canonical.replace("bound_nodes=1", "bound_nodes=0"),
            canonical.replace("nodebits=2", "nodebits=1", 1),
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
            canonical.replace(
                "generation=1", "generation=1 generation=1", 1
            ),
            canonical.replace("selftest PASS", "selftest PASSFAIL"),
            canonical + " apply_enabled=1",
            "  " + canonical,
            '"' + canonical + '"',
        )
        for invalid in mutations:
            with self.subTest(invalid=invalid):
                lines = [
                    invalid if line == canonical else line
                    for line in normal_lines()
                ]
                verdict = evaluate(lines)
                self.assertFalse(verdict["passed"])

        missing = [line for line in normal_lines() if line != canonical]
        self.assertFalse(evaluate(missing)["passed"])

        for extra in (
            canonical,
            "[ROOM] management hierarchy selftest PARTIAL schema=1",
            "[ROOM] management hierarchy",
        ):
            with self.subTest(extra=extra):
                verdict = evaluate([*normal_lines(), extra])
                self.assertFalse(verdict["passed"])
                self.assertIn(
                    "EVIDENCE_RECORD_INVALID", reason_codes(verdict)
                )

        entry = RING3_ENTRY_AC_HARDENING_PATTERNS["default"]
        room = ROOM_SNAPSHOT_LINE
        before_entry = [
            line
            for line in normal_lines()
            if line != canonical
        ]
        before_entry.insert(before_entry.index(entry), canonical)
        after_room = [
            line
            for line in normal_lines()
            if line != canonical
        ]
        after_room.insert(after_room.index(room) + 1, canonical)
        for reordered in (before_entry, after_room):
            with self.subTest(reordered=reordered):
                verdict = evaluate(reordered)
                self.assertFalse(verdict["passed"])
                self.assertIn(
                    "TERMINAL_CHECKPOINT_ORDER_INVALID",
                    reason_codes(verdict),
                )

    def test_entry_ac_hardening_contract_fails_closed(self) -> None:
        canonical = RING3_ENTRY_AC_HARDENING_PATTERNS["default"]
        required = [CPU_SECURITY_PATTERNS["default"], canonical]
        mutations = (
            canonical.replace("common_saved_ac=2", "common_saved_ac=1"),
            canonical.replace("int80_saved_ac=4", "int80_saved_ac=3"),
            canonical.replace("common_fallback=2", "common_fallback=1"),
            canonical.replace("int80_fallback=6", "int80_fallback=5"),
            canonical.replace("common_post_ac0=2", "common_post_ac0=1"),
            canonical.replace("int80_post_ac0=6", "int80_post_ac0=5"),
            canonical.replace("gate_skips=8", "gate_skips=7"),
            canonical.replace("gate_mismatch=0", "gate_mismatch=1"),
            canonical + " extra=1",
            canonical.replace(
                "gate_active=0", "gate_active=0 gate_active=0"
            ),
            "  " + canonical,
            '"' + canonical + '"',
        )
        for invalid in mutations:
            with self.subTest(invalid=invalid):
                lines = [invalid if line == canonical else line for line in normal_lines()]
                lines.insert(1, CPU_SECURITY_PATTERNS["default"])
                verdict = evaluate_normal_boot("\n".join(lines), required)
                self.assertFalse(verdict["passed"])

        duplicate_family = [
            CPU_SECURITY_PATTERNS["default"],
            *normal_lines(),
            "[SEC] ring3 entry AC hardening PARTIAL schema=1",
        ]
        verdict = evaluate_normal_boot("\n".join(duplicate_family), required)
        self.assertFalse(verdict["passed"])
        self.assertIn("EVIDENCE_RECORD_INVALID", reason_codes(verdict))

        fatal_after = [
            CPU_SECURITY_PATTERNS["default"],
            *normal_lines(),
            "!!! EXCEPTION: Invalid Opcode (#UD)",
        ]
        verdict = evaluate_normal_boot("\n".join(fatal_after), required)
        self.assertFalse(verdict["passed"])
        self.assertIn("FATAL_EVENTS_PRESENT", reason_codes(verdict))

    def test_required_marker_missing_fails(self) -> None:
        lines = normal_lines()
        lines.remove("[BOOT] profile-required")

        verdict = evaluate(lines)

        self.assertFalse(verdict["passed"])
        self.assertIn("MISSING_REQUIRED_PATTERNS", reason_codes(verdict))

    def test_pressure_contract_is_required_and_fails_closed(self) -> None:
        for profile in ("full", "minimal", "storage-only"):
            with self.subTest(profile=profile):
                self.assertIn(
                    PRESSURE_SELFTEST_PATTERN,
                    required_smoke_patterns(profile),
                )

        valid = evaluate_normal_boot(
            "\n".join([*normal_lines(), PRESSURE_SELFTEST_PATTERN]),
            [PRESSURE_SELFTEST_PATTERN],
        )
        self.assertTrue(valid["passed"])

        for invalid in (
            PRESSURE_SELFTEST_PATTERN.replace(
                "observation_only=1", "observation_only=0"
            ),
            PRESSURE_SELFTEST_PATTERN.replace("gate_mask=1", "gate_mask=0"),
            "[PRESSURE] tracker selftest PASS schema=1 planes=3",
            f"{PRESSURE_SELFTEST_PATTERN} apply_enabled=1",
            f"  {PRESSURE_SELFTEST_PATTERN}",
        ):
            with self.subTest(invalid=invalid):
                verdict = evaluate_normal_boot(
                    "\n".join([*normal_lines(), invalid]),
                    [PRESSURE_SELFTEST_PATTERN],
                )
                self.assertFalse(verdict["passed"])
                self.assertTrue(
                    {"MISSING_REQUIRED_PATTERNS", "EVIDENCE_RECORD_INVALID"}
                    & set(reason_codes(verdict))
                )

        duplicate = evaluate_normal_boot(
            "\n".join(
                [
                    *normal_lines(),
                    PRESSURE_SELFTEST_PATTERN,
                    PRESSURE_SELFTEST_PATTERN,
                ]
            ),
            [PRESSURE_SELFTEST_PATTERN],
        )
        self.assertFalse(duplicate["passed"])

    def test_process_trap_snapshot_is_exact_and_fails_closed(self) -> None:
        for profile in ("full", "minimal", "storage-only"):
            with self.subTest(profile=profile):
                self.assertIn(
                    PROCESS_TRAP_SNAPSHOT_PATTERN,
                    required_smoke_patterns(profile),
                )

        def snapshot_lines(record: str) -> list[str]:
            lines = normal_lines()
            index = lines.index(
                "[PROC] trap evidence snapshot PASS schema=1 captures=2"
            )
            lines[index] = record
            return lines

        valid = evaluate_normal_boot(
            "\n".join(snapshot_lines(PROCESS_TRAP_SNAPSHOT_PATTERN)),
            [PROCESS_TRAP_SNAPSHOT_PATTERN],
        )
        self.assertTrue(valid["passed"])

        for invalid in (
            "[PROC] trap evidence snapshot PASS schema=1 captures=2",
            PROCESS_TRAP_SNAPSHOT_PATTERN.replace("owner_b=1", "owner_b=0"),
            PROCESS_TRAP_SNAPSHOT_PATTERN.replace("seq_b=2", "seq_b=1"),
            PROCESS_TRAP_SNAPSHOT_PATTERN.replace(
                "current_pid=0", "current_pid=2"
            ),
            PROCESS_TRAP_SNAPSHOT_PATTERN.replace(
                "stale_owner=0", "stale_owner=1"
            ),
            PROCESS_TRAP_SNAPSHOT_PATTERN.replace(
                "resume_ready=0", "resume_ready=1"
            ),
            f"{PROCESS_TRAP_SNAPSHOT_PATTERN} extra=1",
            f"  {PROCESS_TRAP_SNAPSHOT_PATTERN}",
            f'"{PROCESS_TRAP_SNAPSHOT_PATTERN}"',
            PROCESS_TRAP_SNAPSHOT_PATTERN.replace(
                "owner_b=1", "owner_b=0 owner_b=1"
            ),
        ):
            with self.subTest(invalid_snapshot=invalid):
                verdict = evaluate_normal_boot(
                    "\n".join(snapshot_lines(invalid)),
                    [PROCESS_TRAP_SNAPSHOT_PATTERN],
                )
                self.assertFalse(verdict["passed"])

        duplicate = evaluate_normal_boot(
            "\n".join(
                [
                    *snapshot_lines(PROCESS_TRAP_SNAPSHOT_PATTERN),
                    PROCESS_TRAP_SNAPSHOT_PATTERN,
                ]
            ),
            [PROCESS_TRAP_SNAPSHOT_PATTERN],
        )
        self.assertFalse(duplicate["passed"])
        self.assertIn("EVIDENCE_RECORD_DUPLICATED", reason_codes(duplicate))

        conflicting_duplicate = evaluate_normal_boot(
            "\n".join(
                [
                    *snapshot_lines(PROCESS_TRAP_SNAPSHOT_PATTERN),
                    "[PROC] trap evidence snapshot PASS schema=1 captures=2",
                ]
            ),
            [PROCESS_TRAP_SNAPSHOT_PATTERN],
        )
        self.assertFalse(conflicting_duplicate["passed"])
        self.assertIn(
            "TERMINAL_CHECKPOINTS_DUPLICATED",
            reason_codes(conflicting_duplicate),
        )

    def test_process_event_journal_is_exact_and_fails_closed(self) -> None:
        for profile in ("full", "minimal", "storage-only"):
            with self.subTest(profile=profile):
                self.assertIn(
                    PROCESS_EVENT_JOURNAL_PATTERN,
                    required_smoke_patterns(profile),
                )

        def journal_lines(record: str) -> list[str]:
            lines = normal_lines()
            index = lines.index(
                "[PROC] process event journal PASS schema=1 events=6"
            )
            lines[index] = record
            return lines

        valid = evaluate_normal_boot(
            "\n".join(journal_lines(PROCESS_EVENT_JOURNAL_PATTERN)),
            [PROCESS_EVENT_JOURNAL_PATTERN],
        )
        self.assertTrue(valid["passed"])

        for invalid in (
            "[PROC] process event journal PASS schema=1 events=6",
            PROCESS_EVENT_JOURNAL_PATTERN.replace(
                "seqs=1,2,3,4,5,6", "seqs=1,2,4,3,5,6"
            ),
            PROCESS_EVENT_JOURNAL_PATTERN.replace(
                "kinds=1,2,3,1,2,3", "kinds=1,2,4,1,2,3"
            ),
            PROCESS_EVENT_JOURNAL_PATTERN.replace(
                "reasons=1,2,3,1,2,3", "reasons=1,2,0,1,2,3"
            ),
            PROCESS_EVENT_JOURNAL_PATTERN.replace(
                "from_pids=0,1,1,0,2,2", "from_pids=0,1,2,0,2,2"
            ),
            PROCESS_EVENT_JOURNAL_PATTERN.replace(
                "capture_seqs=0,1,1,0,2,2",
                "capture_seqs=0,2,1,0,1,2",
            ),
            PROCESS_EVENT_JOURNAL_PATTERN.replace(
                "owner_ok=1,1,1,1,1,1", "owner_ok=1,1,1,1,0,1"
            ),
            PROCESS_EVENT_JOURNAL_PATTERN.replace(
                "cr3_ok=1,1,1,1,1,1", "cr3_ok=1,1,0,1,1,1"
            ),
            PROCESS_EVENT_JOURNAL_PATTERN.replace(
                "rsp0_ok=1,1,1,1,1,1", "rsp0_ok=1,0,1,1,1,1"
            ),
            PROCESS_EVENT_JOURNAL_PATTERN.replace(
                "if0=1,1,1,1,1,1", "if0=1,1,1,1,1,0"
            ),
            PROCESS_EVENT_JOURNAL_PATTERN.replace(
                "snapshot_refs=0,1,1,0,1,1",
                "snapshot_refs=0,0,1,0,1,1",
            ),
            PROCESS_EVENT_JOURNAL_PATTERN.replace(
                "outcomes=1,1,1,1,1,1", "outcomes=1,1,1,1,1,2"
            ),
            PROCESS_EVENT_JOURNAL_PATTERN.replace(
                "capture_seq_separate=1", "capture_seq_separate=0"
            ),
            PROCESS_EVENT_JOURNAL_PATTERN.replace(
                "stale_owner=0", "stale_owner=1"
            ),
            PROCESS_EVENT_JOURNAL_PATTERN.replace("dropped=0", "dropped=1"),
            PROCESS_EVENT_JOURNAL_PATTERN.replace("overflow=0", "overflow=1"),
            PROCESS_EVENT_JOURNAL_PATTERN.replace(
                "evidence_only=1", "evidence_only=0"
            ),
            PROCESS_EVENT_JOURNAL_PATTERN.replace(
                "switch_events=0", "switch_events=1"
            ),
            PROCESS_EVENT_JOURNAL_PATTERN.replace(
                "resume_ready=0", "resume_ready=1"
            ),
            f"{PROCESS_EVENT_JOURNAL_PATTERN} extra=1",
            f"  {PROCESS_EVENT_JOURNAL_PATTERN}",
            f'"{PROCESS_EVENT_JOURNAL_PATTERN}"',
            PROCESS_EVENT_JOURNAL_PATTERN.replace(
                "switch_events=0", "switch_events=1 switch_events=0"
            ),
        ):
            with self.subTest(invalid_transition=invalid):
                verdict = evaluate_normal_boot(
                    "\n".join(journal_lines(invalid)),
                    [PROCESS_EVENT_JOURNAL_PATTERN],
                )
                self.assertFalse(verdict["passed"])

        malformed_duplicate = evaluate_normal_boot(
            "\n".join(
                [
                    *journal_lines(PROCESS_EVENT_JOURNAL_PATTERN),
                    "[PROC] process event journal schema=1 events=6",
                ]
            ),
            [PROCESS_EVENT_JOURNAL_PATTERN],
        )
        self.assertFalse(malformed_duplicate["passed"])
        self.assertIn(
            "EVIDENCE_RECORD_INVALID",
            reason_codes(malformed_duplicate),
        )

        duplicate = evaluate_normal_boot(
            "\n".join(
                [
                    *journal_lines(PROCESS_EVENT_JOURNAL_PATTERN),
                    PROCESS_EVENT_JOURNAL_PATTERN,
                ]
            ),
            [PROCESS_EVENT_JOURNAL_PATTERN],
        )
        self.assertFalse(duplicate["passed"])
        self.assertIn("EVIDENCE_RECORD_DUPLICATED", reason_codes(duplicate))

    def test_trapframe_contract_is_exact_and_fails_closed(self) -> None:
        for profile in ("full", "minimal", "storage-only"):
            with self.subTest(profile=profile):
                self.assertIn(
                    TRAPFRAME_CONTRACT_PATTERN,
                    required_smoke_patterns(profile),
                )
                self.assertIn(
                    USER_TRAP_CAPTURE_PATTERN,
                    required_smoke_patterns(profile),
                )

        contract_valid = evaluate_normal_boot(
            "\n".join([*normal_lines(), TRAPFRAME_CONTRACT_PATTERN]),
            [TRAPFRAME_CONTRACT_PATTERN],
        )
        self.assertTrue(contract_valid["passed"])

        for invalid in (
            TRAPFRAME_CONTRACT_PATTERN.replace("canaries=15", "canaries=14"),
            TRAPFRAME_CONTRACT_PATTERN.replace("cpl0=1", "cpl0=0"),
            TRAPFRAME_CONTRACT_PATTERN.replace(
                "frame_addr_exact=1", "frame_addr_exact=0"
            ),
            "[TRAP] frame contract selftest PASS size=176",
            f"{TRAPFRAME_CONTRACT_PATTERN} extra=1",
            f"  {TRAPFRAME_CONTRACT_PATTERN}",
        ):
            with self.subTest(invalid_contract=invalid):
                verdict = evaluate_normal_boot(
                    "\n".join([*normal_lines(), invalid]),
                    [TRAPFRAME_CONTRACT_PATTERN],
                )
                self.assertFalse(verdict["passed"])
                self.assertTrue(
                    {"MISSING_REQUIRED_PATTERNS", "EVIDENCE_RECORD_INVALID"}
                    & set(reason_codes(verdict))
                )

        # The user capture is also a terminal checkpoint: swap the fixture's
        # short chain line for the full record so the chain stays exact-once.
        def capture_lines(record: str) -> list[str]:
            lines = normal_lines()
            index = lines.index("[TRAP] user frame capture PASS pid_a=1 pid_b=2")
            lines[index] = record
            return lines

        capture_valid = evaluate_normal_boot(
            "\n".join(capture_lines(USER_TRAP_CAPTURE_PATTERN)),
            [USER_TRAP_CAPTURE_PATTERN],
        )
        self.assertTrue(capture_valid["passed"])

        for invalid in (
            USER_TRAP_CAPTURE_PATTERN.replace("from_user=1", "from_user=0"),
            USER_TRAP_CAPTURE_PATTERN.replace("cs=0x23", "cs=0x8"),
            USER_TRAP_CAPTURE_PATTERN.replace(
                "frame_in_kstack=1", "frame_in_kstack=0"
            ),
            USER_TRAP_CAPTURE_PATTERN.replace("contract=1", "contract=0"),
            f"{USER_TRAP_CAPTURE_PATTERN} extra=1",
        ):
            with self.subTest(invalid_capture=invalid):
                verdict = evaluate_normal_boot(
                    "\n".join(capture_lines(invalid)),
                    [USER_TRAP_CAPTURE_PATTERN],
                )
                self.assertFalse(verdict["passed"])
                self.assertTrue(
                    {"MISSING_REQUIRED_PATTERNS", "EVIDENCE_RECORD_INVALID"}
                    & set(reason_codes(verdict))
                )

        duplicate = evaluate_normal_boot(
            "\n".join(
                [*capture_lines(USER_TRAP_CAPTURE_PATTERN),
                 USER_TRAP_CAPTURE_PATTERN]
            ),
            [USER_TRAP_CAPTURE_PATTERN],
        )
        self.assertFalse(duplicate["passed"])

    def test_resource_contract_is_exact_and_fails_closed(self) -> None:
        for profile in ("full", "minimal", "storage-only"):
            with self.subTest(profile=profile):
                self.assertIn(
                    RESOURCE_SELFTEST_PATTERN,
                    required_smoke_patterns(profile),
                )

        valid = evaluate_normal_boot(
            "\n".join([*normal_lines(), RESOURCE_SELFTEST_PATTERN]),
            [RESOURCE_SELFTEST_PATTERN],
        )
        self.assertTrue(valid["passed"])

        invalid_cases = (
            RESOURCE_SELFTEST_PATTERN.replace(
                "observation_only=1", "observation_only=0"
            ),
            "[RESOURCE] ledger selftest PASS schema=1 kinds=5",
            f"{RESOURCE_SELFTEST_PATTERN} apply_enabled=1",
            f"  {RESOURCE_SELFTEST_PATTERN}",
        )
        for invalid in invalid_cases:
            with self.subTest(invalid=invalid):
                verdict = evaluate_normal_boot(
                    "\n".join([*normal_lines(), invalid]),
                    [RESOURCE_SELFTEST_PATTERN],
                )
                self.assertFalse(verdict["passed"])
                self.assertTrue(
                    {"MISSING_REQUIRED_PATTERNS", "EVIDENCE_RECORD_INVALID"}
                    & set(reason_codes(verdict))
                )

        duplicate = evaluate_normal_boot(
            "\n".join(
                [
                    *normal_lines(),
                    RESOURCE_SELFTEST_PATTERN,
                    RESOURCE_SELFTEST_PATTERN,
                ]
            ),
            [RESOURCE_SELFTEST_PATTERN],
        )
        self.assertFalse(duplicate["passed"])
        self.assertIn("EVIDENCE_RECORD_DUPLICATED", reason_codes(duplicate))

    def test_bootstrap_process_pair_checkpoint_missing_fails(self) -> None:
        lines = normal_lines()
        lines.remove("[USER] bootstrap process pair PASS runs=2")

        verdict = evaluate(lines)

        self.assertFalse(verdict["passed"])
        self.assertIn("TERMINAL_CHECKPOINTS_MISSING", reason_codes(verdict))

    def test_required_marker_token_suffix_does_not_match(self) -> None:
        lines = normal_lines()
        lines[0] = "[BOOT] profile-required-extra"

        verdict = evaluate(lines)

        self.assertFalse(verdict["passed"])
        self.assertIn("MISSING_REQUIRED_PATTERNS", reason_codes(verdict))

    def test_fatal_after_all_pass_markers_fails(self) -> None:
        fatal_lines = (
            "*** KERNEL PANIC *** late panic",
            "!!! EXCEPTION vector=14",
            "[SELFTEST] explicit FAIL reason=late",
            "[GUARD] FATAL reason=late",
        )
        for fatal_line in fatal_lines:
            with self.subTest(fatal_line=fatal_line):
                verdict = evaluate([*normal_lines(), fatal_line])
                self.assertFalse(verdict["passed"])
                self.assertIn("FATAL_EVENTS_PRESENT", reason_codes(verdict))
                self.assertGreater(verdict["fatal_events"][0]["line"], 10)

    def test_harmless_failed_fields_do_not_trigger_fatal_scan(self) -> None:
        lines = normal_lines()
        lines.insert(-1, "[STATE] slm failed=0 apply_failed=0")

        verdict = evaluate(lines)

        self.assertTrue(verdict["passed"])
        self.assertEqual([], verdict["fatal_events"])

    def test_degraded_failed_and_malformed_health_fail(self) -> None:
        health_lines = (
            "[HEALTH] stability=degraded ok=17 degraded=1 failed=0 unknown=2",
            "[HEALTH] stability=stable ok=17 degraded=0 failed=1 unknown=2",
            "[HEALTH] stability=stable degraded=zero failed=0",
            "[HEALTH] stability=stable ok=18 degraded=0 failed=0 degraded=1",
            "[HEALTH] stability=stable not-degraded=0 not-failed=0",
        )
        for health_line in health_lines:
            with self.subTest(health_line=health_line):
                lines = normal_lines()
                lines[7] = health_line
                verdict = evaluate(lines)
                self.assertFalse(verdict["passed"])
                self.assertIn("HEALTH_INVALID", reason_codes(verdict))

    def test_health_zero_fields_require_canonical_unsigned_zero(self) -> None:
        health_lines = (
            "[HEALTH] stability=stable ok=18 degraded=-0 failed=0 unknown=2",
            "[HEALTH] stability=stable ok=18 degraded=0 failed=+0 unknown=2",
            "[HEALTH] stability=stable ok=18 degraded=000 failed=0 unknown=2",
        )
        for health_line in health_lines:
            with self.subTest(health_line=health_line):
                lines = normal_lines()
                lines[7] = health_line
                verdict = evaluate(lines)
                self.assertFalse(verdict["passed"])
                self.assertIn("HEALTH_INVALID", reason_codes(verdict))

    def test_terminal_checkpoint_token_suffixes_do_not_match(self) -> None:
        variants = (
            (1, "[USER] Ring3 scaffold ready=10 tr=0x28"),
            (2, "[PROC] bootstrap ownership selftest PASSFAIL slots=2"),
            (6, "[ROOM] snapshot stability=stable_bad ok=18 degraded=0 failed=0"),
        )
        for index, replacement in variants:
            with self.subTest(replacement=replacement):
                lines = normal_lines()
                lines[index] = replacement
                verdict = evaluate(lines)
                self.assertFalse(verdict["passed"])
                self.assertIn("TERMINAL_CHECKPOINTS_MISSING", reason_codes(verdict))

    def test_duplicate_contract_field_fails_even_after_required_substring(self) -> None:
        lines = normal_lines()
        lines[4] = (
            "[USER] private address space exec PASS slot=0 cr3_restored=1 "
            "if_restored=1 cr3_restored=0"
        )

        verdict = evaluate(lines)

        self.assertFalse(verdict["passed"])
        self.assertIn("EVIDENCE_FIELDS_DUPLICATED", reason_codes(verdict))

        ide_line = (
            "[STO] IDE channels primary=0x1f0/0x3f6 status=0x0 live=1 "
            "secondary=0x170/0x376 status=0x50 live=1"
        )
        for valid_record in (ide_line, f"{ide_line} \t"):
            with self.subTest(valid_record=valid_record):
                valid = evaluate_normal_boot(
                    "\n".join([*normal_lines(), valid_record]),
                    ["[STO] IDE channels"],
                )
                self.assertTrue(valid["passed"])

        conflicting = ide_line.replace(
            "primary=0x1f0/0x3f6",
            "primary=0x1f0/0x3f6 primary=0x170/0x376",
        )
        verdict = evaluate_normal_boot(
            "\n".join([*normal_lines(), conflicting]),
            ["[STO] IDE channels"],
        )
        self.assertFalse(verdict["passed"])
        self.assertIn("EVIDENCE_FIELDS_DUPLICATED", reason_codes(verdict))

        for malformed in (
            "[STO] IDE channels",
            "[STO] IDE channels primary=0x1f0/0x3f6 status=0x0 live=1",
            (
                "[STO] IDE channels primary=0x1f0/0x3f6 status=0x0 live=1 "
                "secondary=0x01f0/0x03f6 status=0x50 live=1"
            ),
            (
                "[STO] IDE channels primary=0x1f0/0x3f6 "
                "secondary=0x170/0x376 status=0x0 status=0x50 live=1 live=1"
            ),
            (
                "[STO] IDE channels primary=0x1f0/0x3f6 status=0x100 live=1 "
                "secondary=0x170/0x376 status=0x50 live=1"
            ),
            (
                "[STO] IDE channels primary=0x1f0/0x3f6 status=0x0 live=1 "
                "secondary=0x1f0/0x376 status=0x50 live=1"
            ),
            (
                "[STO] IDE channels Primary=0x1f0/0x3f6 Status=0x0 Live=1 "
                "Secondary=0x170/0x376 Status=0x50 Live=1"
            ),
        ):
            with self.subTest(malformed=malformed):
                verdict = evaluate_normal_boot(
                    "\n".join([*normal_lines(), malformed]),
                    ["[STO] IDE channels"],
                )
                self.assertFalse(verdict["passed"])
                self.assertIn("EVIDENCE_RECORD_INVALID", reason_codes(verdict))

    def test_diagnostic_text_cannot_impersonate_terminal_evidence(self) -> None:
        lines = normal_lines()
        lines[3] = "[WARN] old marker: [USER] ring3 exec PASS exit_code=42"

        verdict = evaluate(lines)

        self.assertFalse(verdict["passed"])
        self.assertIn("TERMINAL_CHECKPOINTS_MISSING", reason_codes(verdict))

        lines = normal_lines()
        lines[3] = "    [USER] ring3 exec PASS exit_code=42"
        verdict = evaluate(lines)
        self.assertFalse(verdict["passed"])
        self.assertIn("TERMINAL_CHECKPOINTS_MISSING", reason_codes(verdict))

    def test_profile_field_fragment_requires_its_evidence_record(self) -> None:
        valid = [*normal_lines(), "[SLM] Seeded plan 4 label=storage-bootstrap action=8"]
        quoted = [*normal_lines(), "[WARN] old label=storage-bootstrap ignored"]

        self.assertTrue(
            evaluate_normal_boot("\n".join(valid), ["label=storage-bootstrap"])["passed"]
        )
        verdict = evaluate_normal_boot(
            "\n".join(quoted),
            ["label=storage-bootstrap"],
        )
        self.assertFalse(verdict["passed"])
        self.assertIn("MISSING_REQUIRED_PATTERNS", reason_codes(verdict))

        conflicting = [
            *normal_lines(),
            "[SLM] Seeded plan 4 label=storage-bootstrap label=other action=8",
        ]
        verdict = evaluate_normal_boot(
            "\n".join(conflicting),
            ["label=storage-bootstrap"],
        )
        self.assertFalse(verdict["passed"])
        self.assertIn("EVIDENCE_FIELDS_DUPLICATED", reason_codes(verdict))

    def test_reordered_terminal_checkpoints_fail(self) -> None:
        lines = normal_lines()
        lines[3], lines[4] = lines[4], lines[3]

        verdict = evaluate(lines)

        self.assertFalse(verdict["passed"])
        self.assertIn("TERMINAL_CHECKPOINT_ORDER_INVALID", reason_codes(verdict))
        self.assertTrue(verdict["checkpoints"]["order_violations"])

    def test_duplicated_terminal_checkpoint_fails(self) -> None:
        lines = normal_lines()
        lines[3] += " [USER] ring3 exec PASS conflicting=1"

        verdict = evaluate(lines)

        self.assertFalse(verdict["passed"])
        self.assertIn("TERMINAL_CHECKPOINTS_DUPLICATED", reason_codes(verdict))
        self.assertTrue(verdict["checkpoints"]["duplicates"])

    def test_truncated_log_fails(self) -> None:
        verdict = evaluate(normal_lines()[:-3])

        self.assertFalse(verdict["passed"])
        self.assertIn("TERMINAL_CHECKPOINTS_MISSING", reason_codes(verdict))

    def test_verdict_line_numbers_match_raw_log(self) -> None:
        lines = normal_lines()
        lines.insert(1, "")
        lines.append("*** KERNEL PANIC *** after-blank")

        verdict = evaluate(lines)

        self.assertFalse(verdict["passed"])
        self.assertEqual(len(lines), verdict["fatal_events"][0]["line"])


if __name__ == "__main__":
    unittest.main()
