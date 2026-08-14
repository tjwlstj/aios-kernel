from __future__ import annotations

import json
import os
import tempfile
import threading
import time
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
    shell_exchanges,
    shell_result_passed,
    state_sec_expectations,
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

    def test_state_sec_contract_is_cpu_profile_bound(self) -> None:
        records: dict[str, str] = {}
        for cpu_profile in ("default", "max-smap"):
            expectations = state_sec_expectations(cpu_profile)
            record = " ".join(expectations) + "\n"
            records[cpu_profile] = record
            self.assertTrue(expectations_match(record, expectations))
            exchange = next(
                item
                for item in shell_exchanges(cpu_profile)
                if item["command"] == "state sec"
            )
            self.assertEqual(expectations, exchange["expect"])

        self.assertFalse(
            expectations_match(
                records["default"], state_sec_expectations("max-smap")
            )
        )
        self.assertFalse(
            expectations_match(
                records["default"]
                + "[STATE] sec schema=1 entry_ready=0\n",
                state_sec_expectations("default"),
            )
        )
        default_expectations = state_sec_expectations("default")
        for invalid in (
            records["default"].replace(
                "entry_common_saved_ac=2", "entry_common_saved_ac=1"
            ),
            records["default"].replace(
                "entry_int80_saved_ac=4", "entry_int80_saved_ac=3"
            ),
            records["default"].replace(
                "entry_gate_skips=8", "entry_gate_skips=7"
            ),
            records["default"].replace(
                "entry_gate_mismatch=0", "entry_gate_mismatch=1"
            ),
            records["default"].replace(
                "entry_gate_active=0",
                "entry_gate_active=1 entry_gate_active=0",
            ),
            records["default"].replace(
                " entry_int80=6", "\n[STATE] sec entry_int80=6"
            ),
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(
                    expectations_match(invalid, default_expectations)
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

    def test_state_sec_requires_exact_canonical_record(self) -> None:
        for cpu_profile in ("default", "max-smap"):
            expectations = state_sec_expectations(cpu_profile)
            canonical = " ".join(expectations)
            reordered = list(expectations)
            reordered[8], reordered[9] = reordered[9], reordered[8]
            invalid_records = (
                canonical + " PASSFAIL",
                canonical + " PARTIAL",
                canonical + " failed=1",
                canonical + " entry_apply=1",
                " ".join(expectations[:-1]),
                " ".join(
                    token for index, token in enumerate(expectations)
                    if index != 8
                ),
                " ".join(reordered),
            )
            self.assertTrue(
                expectations_match(canonical + "\n", expectations)
            )
            for invalid in invalid_records:
                with self.subTest(
                    cpu_profile=cpu_profile,
                    invalid=invalid,
                ):
                    self.assertFalse(
                        expectations_match(invalid + "\n", expectations)
                    )

    def test_state_room_requires_exact_canonical_hierarchy_record(
        self,
    ) -> None:
        exchange = next(
            item
            for item in DEFAULT_EXCHANGES
            if item["command"] == "state room"
        )
        expectations = list(exchange["expect"])
        canonical = " ".join(expectations)
        self.assertTrue(
            expectations_match(canonical + "\n", expectations)
        )

        reordered = list(expectations)
        reordered[5], reordered[6] = reordered[6], reordered[5]
        invalid_records = (
            " ".join(expectations[:-1]),
            canonical.replace("struct_size=1024", "struct_size=1023"),
            canonical.replace("ready=1", "ready=0"),
            canonical.replace("generation=1", "generation=2", 1),
            canonical.replace("cells=1", "cells=0"),
            canonical.replace("cell_capacity=2", "cell_capacity=1"),
            canonical.replace("nodes=1", "nodes=0"),
            canonical.replace("node_capacity=4", "node_capacity=3"),
            canonical.replace("bound_nodes=1", "bound_nodes=0"),
            canonical.replace("nodebits=2", "nodebits=1", 1),
            canonical.replace("nodebit_capacity=8", "nodebit_capacity=7"),
            canonical.replace("bound_nodebits=2", "bound_nodebits=1"),
            canonical.replace("cell_id=1", "cell_id=2"),
            canonical.replace("node_id=101", "node_id=102"),
            canonical.replace("node_parent=1", "node_parent=2"),
            canonical.replace("nodebit_ids=1001,1002", "nodebit_ids=1001"),
            canonical.replace(
                "nodebit_parents=101,101", "nodebit_parents=101,102"
            ),
            canonical.replace("source_valid=1", "source_valid=0"),
            canonical.replace("generation_valid=1", "generation_valid=0"),
            canonical.replace("duplicate=0", "duplicate=1"),
            canonical.replace("orphan=0", "orphan=1"),
            canonical.replace("unknown=0", "unknown=1"),
            canonical.replace("stale=0", "stale=1"),
            canonical.replace("overflow=0", "overflow=1"),
            canonical.replace("observation_only=1", "observation_only=0"),
            canonical.replace("management_only=1", "management_only=0"),
            canonical.replace(
                "generation=1", "generation=1 generation=1", 1
            ),
            canonical + " apply_enabled=1",
            canonical + " PARTIAL",
            " ".join(reordered),
            "  " + canonical,
            '"' + canonical + '"',
        )
        for invalid in invalid_records:
            with self.subTest(invalid=invalid):
                self.assertFalse(
                    expectations_match(invalid + "\n", expectations)
                )

        for sibling in (
            canonical,
            "[STATE] room error=unavailable",
            "[STATE] room",
        ):
            with self.subTest(sibling=sibling):
                self.assertFalse(
                    expectations_match(
                        canonical + "\n" + sibling + "\n",
                        expectations,
                    )
                )

    def test_state_binding_requires_exact_native_oracle_record(self) -> None:
        exchange = next(
            item
            for item in DEFAULT_EXCHANGES
            if item["command"] == "state binding"
        )
        expectations = list(exchange["expect"])
        canonical = " ".join(expectations)
        self.assertTrue(
            expectations_match(canonical + "\n", expectations)
        )

        reordered = list(expectations)
        reordered[7], reordered[8] = reordered[8], reordered[7]
        invalid_records = (
            " ".join(expectations[:-1]),
            canonical.replace("struct_size=256", "struct_size=255"),
            canonical.replace("ready=1", "ready=0"),
            canonical.replace("binding_generation=1", "binding_generation=2"),
            canonical.replace("canonical_id=101", "canonical_id=102"),
            canonical.replace("source_instance=1", "source_instance=0"),
            canonical.replace("source_generation=1", "source_generation=2"),
            canonical.replace("kind_match=1", "kind_match=0"),
            canonical.replace("role_match=1", "role_match=0"),
            canonical.replace("producer_owned=1", "producer_owned=0"),
            canonical.replace("source_valid=1", "source_valid=0"),
            canonical.replace("generation_valid=1", "generation_valid=0"),
            canonical.replace("binding_valid=1", "binding_valid=0"),
            canonical.replace("last_reject=0", "last_reject=1"),
            canonical.replace("observation_only=1", "observation_only=0"),
            canonical.replace("management_only=1", "management_only=0"),
            canonical.replace(
                "source_id=1", "source_id=1 source_id=1", 1
            ),
            canonical + " apply_enabled=1",
            canonical + " PARTIAL",
            " ".join(reordered),
            "  " + canonical,
            '"' + canonical + '"',
        )
        for invalid in invalid_records:
            with self.subTest(invalid=invalid):
                self.assertFalse(
                    expectations_match(invalid + "\n", expectations)
                )

        for sibling in (
            canonical,
            "[STATE] binding error=unavailable",
            "[STATE] binding",
        ):
            with self.subTest(sibling=sibling):
                self.assertFalse(
                    expectations_match(
                        canonical + "\n" + sibling + "\n",
                        expectations,
                    )
                )

    def test_state_health_and_autonomy_contracts_are_fail_closed(self) -> None:
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

        autonomy_exchange = next(
            item for item in DEFAULT_EXCHANGES
            if item["command"] == "state autonomy"
        )
        autonomy_expectations = list(autonomy_exchange["expect"])
        autonomy = (
            "[STATE] autonomy schema=1 observation_only=1 safe_mode=0 "
            "support_mem=observe-only support_sched=apply "
            "support_accel=observe-only support_infer=observe-only "
            "telemetry=0 proposed=0 approved=0 committed=0 rejected=0 "
            "rollbacks=0 queue_depth=0 event_depth=0 last_valid=0 "
            "last_action=0 last_target=none last_state=none last_reason=none\n"
        )
        self.assertTrue(expectations_match(autonomy, autonomy_expectations))

        for invalid in (
            autonomy.replace("observation_only=1", "observation_only=0"),
            autonomy.replace("support_sched=apply", "support_sched=observe-only"),
            autonomy.replace("support_mem=observe-only", "support_mem=apply"),
            autonomy.replace("last_valid=0", "last_valid=1"),
            autonomy.replace(
                "support_sched=apply",
                "support_sched=observe-only support_sched=apply",
            ),
        ):
            with self.subTest(invalid_autonomy=invalid):
                self.assertFalse(
                    expectations_match(invalid, autonomy_expectations)
                )

    def test_state_pressure_contract_is_observation_only(self) -> None:
        exchange = next(
            item for item in DEFAULT_EXCHANGES
            if item["command"] == "state pressure"
        )
        expectations = list(exchange["expect"])
        pressure = (
            "[STATE] pressure schema=1 observation_only=1 "
            "gate_filter_separate=1 max_levels=4 active_levels=2 planes=3 "
            "source_flags=7 sample=1 sampled_ns=10 sched_q10=0 memory_q10=0 "
            "policy_q10=512 hotspot=policy hotspot_valid=1 hotspot_q10=512 "
            "concentration_q10=1024 sched_queue_concentration_q10=0 "
            "queued=0 runnable=0 largest_queue=0 largest_policy=6 "
            "active_domains=4 active_windows=0 shared_windows=0 "
            "participant_links=0 writer_pairs=0 read_write_pairs=0 "
            "max_fanout=0 budget_bytes=4096 shared_bytes=0 "
            "weighted_shared_bytes=0 active_nodes=1 gate_evals=2 "
            "gate_denies=1 health_blocks=0\n"
        )
        self.assertTrue(expectations_match(pressure, expectations))

        for invalid in (
            pressure.replace("observation_only=1", "observation_only=0"),
            pressure.replace("gate_filter_separate=1", "gate_filter_separate=0"),
            pressure.replace("active_levels=2", "active_levels=3"),
            pressure.replace(
                "gate_filter_separate=1",
                "gate_filter_separate=0 gate_filter_separate=1",
            ),
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(expectations_match(invalid, expectations))

    def test_state_resource_contract_is_aggregate_and_observation_only(self) -> None:
        exchange = next(
            item for item in DEFAULT_EXCHANGES
            if item["command"] == "state resource"
        )
        expectations = list(exchange["expect"])
        resource = (
            "[STATE] resource schema=1 observation_only=1 kinds=5 units=2 "
            "entries=5 capacity=8 source_flags=0x1f sample=2 sampled_ns=10 "
            "owner_rows=0 unattributed_rows=5 heap_used=1024 "
            "heap_limit=2097152 tensor_used=0 tensor_limit=268435456 "
            "tensor_high_water=0 fabric_used=0 fabric_limit=64 "
            "rings_used=0 rings_limit=16 sched_used=0 sched_limit=256 "
            "high_water_kinds=1 denied_kinds=0\n"
        )
        self.assertTrue(expectations_match(resource, expectations))

        for invalid in (
            resource.replace("observation_only=1", "observation_only=0"),
            resource.replace("entries=5", "entries=4"),
            resource.replace("owner_rows=0", "owner_rows=1"),
            resource.replace("unattributed_rows=5", "unattributed_rows=4"),
            resource.replace("high_water_kinds=1", "high_water_kinds=0"),
            resource.replace("denied_kinds=0", "denied_kinds=1"),
            resource.replace(
                "observation_only=1",
                "observation_only=0 observation_only=1",
            ),
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(expectations_match(invalid, expectations))

    def test_state_user_trap_evidence_is_fail_closed(self) -> None:
        exchange = next(
            item for item in DEFAULT_EXCHANGES
            if item["command"] == "state user"
        )
        expectations = list(exchange["expect"])
        record = " ".join(expectations) + "\n"
        self.assertTrue(expectations_match(record, expectations))

        for invalid in (
            record.replace("trap_captured=1", "trap_captured=0"),
            record.replace("trap_from_user=1", "trap_from_user=0"),
            record.replace("trap_rsp_user=1", "trap_rsp_user=0"),
            record.replace("trap_rip_user=1", "trap_rip_user=0"),
            record.replace("trap_canary=1", "trap_canary=0"),
            record.replace("trap_frame_in_kstack=1", "trap_frame_in_kstack=0"),
            record.replace("trap_addr_exact=1", "trap_addr_exact=0"),
            record.replace("trap_contract=1", "trap_contract=0"),
            record.replace("saved_captures=2", "saved_captures=1"),
            record.replace("saved_seq_b=2", "saved_seq_b=1"),
            record.replace("saved_valid_a=1", "saved_valid_a=0"),
            record.replace("saved_valid_b=1", "saved_valid_b=0"),
            record.replace("saved_owner_b=1", "saved_owner_b=0"),
            record.replace("saved_frame_b=1", "saved_frame_b=0"),
            record.replace("saved_cr3_b=1", "saved_cr3_b=0"),
            record.replace("saved_rsp0_b=1", "saved_rsp0_b=0"),
            record.replace(
                "saved_distinct_storage=1", "saved_distinct_storage=0"
            ),
            record.replace("saved_current_pid=0", "saved_current_pid=2"),
            record.replace("saved_stale_owner=0", "saved_stale_owner=1"),
            record.replace("saved_resume_ready=0", "saved_resume_ready=1"),
            record.replace("event_schema=1", "event_schema=2"),
            record.replace("event_count=6", "event_count=5"),
            record.replace("event_lifecycle=4", "event_lifecycle=3"),
            record.replace("event_captures=2", "event_captures=1"),
            record.replace("event_first_seq=1", "event_first_seq=2"),
            record.replace("event_last_seq=6", "event_last_seq=5"),
            record.replace("event_ordered=1", "event_ordered=0"),
            record.replace("event_owner_ok=1", "event_owner_ok=0"),
            record.replace("event_cr3_ok=1", "event_cr3_ok=0"),
            record.replace("event_rsp0_ok=1", "event_rsp0_ok=0"),
            record.replace("event_if0=1", "event_if0=0"),
            record.replace("event_snapshot_refs=1", "event_snapshot_refs=0"),
            record.replace("event_outcomes_ok=1", "event_outcomes_ok=0"),
            record.replace(
                "event_capture_seq_separate=1",
                "event_capture_seq_separate=0",
            ),
            record.replace("event_current_pid=0", "event_current_pid=2"),
            record.replace("event_stale_owner=0", "event_stale_owner=1"),
            record.replace("event_dropped=0", "event_dropped=1"),
            record.replace("event_overflow=0", "event_overflow=1"),
            record.replace("event_evidence_only=1", "event_evidence_only=0"),
            record.replace("event_switches=0", "event_switches=1"),
            record.replace("event_resume_ready=0", "event_resume_ready=1"),
            record.replace(" event_count=6", ""),
            record.replace(
                "trap_contract=1", "trap_contract=0 trap_contract=1"
            ),
            record.replace(
                "saved_owner_b=1", "saved_owner_b=0 saved_owner_b=1"
            ),
            record.replace(
                "event_owner_ok=1",
                "event_owner_ok=0 event_owner_ok=1",
            ),
        ):
            with self.subTest(invalid=invalid):
                self.assertFalse(expectations_match(invalid, expectations))


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

    def test_state_sec_waits_for_prompt_before_rejecting_delayed_family(self) -> None:
        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "rb", buffering=0)

        class FakeProcess:
            stdout = reader

            @staticmethod
            def poll() -> None:
                return None

        expectations = state_sec_expectations("default")
        canonical = " ".join(expectations) + "\n"
        delayed_conflict = "[STATE] sec schema=1 entry_ready=0\n"
        session = SerialSession(FakeProcess())  # type: ignore[arg-type]

        def emit_response() -> None:
            try:
                os.write(write_fd, canonical.encode("ascii"))
                time.sleep(0.1)
                os.write(
                    write_fd,
                    (delayed_conflict + shell_lane.SHELL_PROMPT).encode(
                        "ascii"
                    ),
                )
            finally:
                os.close(write_fd)

        writer = threading.Thread(target=emit_response)
        writer.start()
        try:
            self.assertFalse(
                session.wait_for_response(
                    expectations, timeout_sec=1.0, start_at=0
                )
            )
            writer.join(timeout=1.0)
            self.assertFalse(writer.is_alive())
            self.assertTrue(session.drain())
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
