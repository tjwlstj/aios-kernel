from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
REPLAY_PATH = REPO_ROOT / "tools" / "hosted" / "binding_trace_replay.py"
CONTRACT_PATH = REPO_ROOT / "hosted" / "contracts" / "binding-trace-v1.contract.json"

SPEC = importlib.util.spec_from_file_location("binding_trace_replay", REPLAY_PATH)
assert SPEC and SPEC.loader
replay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(replay)

CONTRACT = replay.load_contract(CONTRACT_PATH)


def base_event(**overrides):
    record = {
        "schema_version": 1,
        "record_type": "event",
        "trace_id": "h1a-selftest",
        "trace_sequence": 1,
        "host_instance": "7001",
        "event": "discover",
        "claimed_outcome": "accepted",
        "claimed_reason": "none",
        "canonical_namespace": "node",
        "canonical_id": "101",
        "canonical_kind": "ai-service",
        "canonical_generation": "1",
        "canonical_valid": 1,
        "parent_cell_id": "1",
        "parent_generation": "1",
        "parent_valid": 1,
        "producer_instance": "9001",
        "producer_owned": 1,
        "copied_read": 1,
        "source_namespace": "native-slm-agent-tree",
        "source_id": "1",
        "source_instance": "11",
        "source_generation": "1",
        "source_kind": "ai-service",
        "source_role": "main",
        "source_valid": 1,
        "bound_source_instance": "0",
        "bound_source_generation": "0",
        "binding_generation": "0",
        "binding_valid": 0,
        "binding_current": 0,
        "kind_match": 1,
        "role_match": 1,
        "generation_valid": 1,
        "lifecycle_state": "active",
        "lifecycle_valid": 1,
        "observed_at_ns": "0",
        "observed_at_valid": 0,
        "observation_only": 1,
        "management_only": 1,
    }
    for key, value in overrides.items():
        if value is None:
            record.pop(key, None)
        else:
            record[key] = value
    return record


def base_terminal(
    sequence,
    record_count,
    accepted,
    rejected,
    final_state="discovered",
    final_binding_generation="0",
    **overrides,
):
    record = {
        "schema_version": 1,
        "record_type": "terminal",
        "trace_id": "h1a-selftest",
        "trace_sequence": sequence,
        "host_instance": "7001",
        "producer_instance": "9001",
        "record_count": record_count,
        "accepted_count": accepted,
        "rejected_count": rejected,
        "final_state": final_state,
        "final_binding_generation": final_binding_generation,
        "observation_only": 1,
        "management_only": 1,
    }
    record.update(overrides)
    return record


def full_lifecycle_records():
    records = [base_event(trace_sequence=1)]
    records.append(
        base_event(
            trace_sequence=2,
            event="bind",
            bound_source_instance="11",
            bound_source_generation="1",
            binding_generation="1",
            binding_valid=1,
            binding_current=1,
        )
    )
    records.append(
        base_event(
            trace_sequence=3,
            event="observe",
            bound_source_instance="11",
            bound_source_generation="1",
            binding_generation="1",
            binding_valid=1,
            binding_current=1,
        )
    )
    records.append(
        base_event(
            trace_sequence=4,
            event="update",
            source_generation="2",
            bound_source_instance="11",
            bound_source_generation="1",
            binding_generation="1",
            binding_valid=1,
            binding_current=0,
        )
    )
    records.append(
        base_event(
            trace_sequence=5,
            event="observe",
            claimed_outcome="rejected",
            claimed_reason="stale",
            source_generation="2",
            bound_source_instance="11",
            bound_source_generation="1",
            binding_generation="1",
            binding_valid=1,
            binding_current=0,
        )
    )
    records.append(
        base_event(
            trace_sequence=6,
            event="rebind",
            source_generation="2",
            bound_source_instance="11",
            bound_source_generation="2",
            binding_generation="2",
            binding_valid=1,
            binding_current=1,
        )
    )
    records.append(
        base_event(
            trace_sequence=7,
            event="observe",
            source_generation="2",
            bound_source_instance="11",
            bound_source_generation="2",
            binding_generation="2",
            binding_valid=1,
            binding_current=1,
        )
    )
    records.append(
        base_event(
            trace_sequence=8,
            event="exit",
            source_generation="3",
            bound_source_instance="11",
            bound_source_generation="2",
            binding_generation="3",
            binding_valid=1,
            binding_current=0,
            lifecycle_state="exited",
        )
    )
    records.append(
        base_event(
            trace_sequence=9,
            event="observe",
            claimed_outcome="rejected",
            claimed_reason="stale",
            source_generation="3",
            bound_source_instance="11",
            bound_source_generation="2",
            binding_generation="3",
            binding_valid=1,
            binding_current=0,
            lifecycle_state="exited",
        )
    )
    records.append(
        base_event(
            trace_sequence=10,
            event="discover",
            source_instance="12",
            source_generation="1",
            bound_source_instance="11",
            bound_source_generation="2",
            binding_generation="3",
            binding_valid=1,
            binding_current=0,
        )
    )
    records.append(
        base_event(
            trace_sequence=11,
            event="rebind",
            source_instance="12",
            source_generation="1",
            bound_source_instance="12",
            bound_source_generation="1",
            binding_generation="4",
            binding_valid=1,
            binding_current=1,
        )
    )
    records.append(
        base_event(
            trace_sequence=12,
            event="observe",
            source_instance="12",
            source_generation="1",
            bound_source_instance="12",
            bound_source_generation="1",
            binding_generation="4",
            binding_valid=1,
            binding_current=1,
        )
    )
    records.append(base_terminal(13, 12, 10, 2, "bound", "4"))
    return records


def serialize(records, line_ending="\n"):
    text = line_ending.join(json.dumps(record) for record in records) + line_ending
    return text.encode("utf-8")


def verdict_of(records, line_ending="\n"):
    return replay.verify_trace_bytes(serialize(records, line_ending), CONTRACT)


def first_reason(records, line_ending="\n"):
    verdict = verdict_of(records, line_ending)
    assert not verdict["passed"], verdict
    return verdict["first_failure"]["reason"]


def raw_verdict(raw: bytes):
    return replay.verify_trace_bytes(raw, CONTRACT)


class ValidTraceTests(unittest.TestCase):
    def test_minimal_discover_terminal_passes(self):
        verdict = verdict_of([base_event(), base_terminal(2, 1, 1, 0)])
        self.assertTrue(verdict["passed"], verdict)
        self.assertEqual("PASS", verdict["outcome"])
        self.assertEqual("h1a-selftest", verdict["trace_id"])
        self.assertEqual(1, verdict["record_count"])
        self.assertEqual(1, verdict["accepted_count"])
        self.assertEqual(0, verdict["rejected_count"])
        self.assertEqual(2, verdict["last_sequence"])
        self.assertIsNone(verdict["first_failure"])
        self.assertEqual([], verdict["reasons"])

    def test_crlf_parity_produces_same_pass(self):
        lf = verdict_of([base_event(), base_terminal(2, 1, 1, 0)], "\n")
        crlf = verdict_of([base_event(), base_terminal(2, 1, 1, 0)], "\r\n")
        self.assertTrue(crlf["passed"])
        self.assertEqual(lf["first_failure"], crlf["first_failure"])
        self.assertEqual(lf["record_count"], crlf["record_count"])

    def test_timestamp_unsupported_path_passes(self):
        records = [base_event(observed_at_valid=0, observed_at_ns="0"), base_terminal(2, 1, 1, 0)]
        self.assertTrue(verdict_of(records)["passed"])

    def test_zero_sentinel_bound_tuple_on_discover_passes(self):
        records = [
            base_event(bound_source_instance="0", bound_source_generation="0",
                       binding_generation="0", binding_valid=0, binding_current=0),
            base_terminal(2, 1, 1, 0),
        ]
        self.assertTrue(verdict_of(records)["passed"])

    def test_full_lifecycle_replays_to_exact_terminal(self):
        verdict = verdict_of(full_lifecycle_records())
        self.assertTrue(verdict["passed"], verdict)
        self.assertEqual(10, verdict["accepted_count"])
        self.assertEqual(2, verdict["rejected_count"])
        self.assertEqual("bound", verdict["final_state"])
        self.assertEqual("4", verdict["final_binding_generation"])
        self.assertEqual(13, verdict["last_sequence"])

    def test_valid_timestamp_requires_and_accepts_nonzero_value(self):
        records = [
            base_event(observed_at_valid=1, observed_at_ns="123"),
            base_terminal(2, 1, 1, 0),
        ]
        self.assertTrue(verdict_of(records)["passed"])

    def test_generation_comparison_is_numeric_not_lexical(self):
        records = [
            base_event(trace_sequence=1),
            base_event(
                trace_sequence=2,
                event="bind",
                bound_source_instance="11",
                bound_source_generation="1",
                binding_generation="1",
                binding_valid=1,
                binding_current=1,
            ),
            base_event(
                trace_sequence=3,
                event="update",
                source_generation="10",
                bound_source_instance="11",
                bound_source_generation="1",
                binding_generation="1",
                binding_valid=1,
                binding_current=0,
            ),
            base_terminal(4, 3, 3, 0, "discovered", "1"),
        ]
        self.assertTrue(verdict_of(records)["passed"])

    def test_exit_terminal_keeps_invalidation_epoch(self):
        records = [
            base_event(trace_sequence=1),
            base_event(
                trace_sequence=2,
                event="bind",
                bound_source_instance="11",
                bound_source_generation="1",
                binding_generation="1",
                binding_valid=1,
                binding_current=1,
            ),
            base_event(
                trace_sequence=3,
                event="exit",
                source_generation="2",
                bound_source_instance="11",
                bound_source_generation="1",
                binding_generation="2",
                binding_valid=1,
                binding_current=0,
                lifecycle_state="exited",
            ),
            base_terminal(4, 3, 3, 0, "exited", "2"),
        ]
        self.assertTrue(verdict_of(records)["passed"])


class SemanticReplayTests(unittest.TestCase):
    def test_stale_observe_claimed_accepted_fails_with_computed_stale(self):
        records = full_lifecycle_records()
        records[4]["claimed_outcome"] = "accepted"
        records[4]["claimed_reason"] = "none"
        verdict = verdict_of(records)
        self.assertEqual("stale", verdict["first_failure"]["reason"])
        self.assertEqual("rejected", verdict["first_failure"]["computed_outcome"])
        self.assertEqual("stale", verdict["first_failure"]["computed_reason"])
        self.assertEqual("accepted", verdict["first_failure"]["claimed_outcome"])

    def test_computed_acceptance_claimed_rejected_fails_trace_outcome(self):
        records = [
            base_event(claimed_outcome="rejected", claimed_reason="stale"),
            base_terminal(2, 1, 0, 1),
        ]
        verdict = verdict_of(records)
        self.assertEqual("trace.outcome", verdict["first_failure"]["reason"])
        self.assertEqual("accepted", verdict["first_failure"]["computed_outcome"])
        self.assertEqual("none", verdict["first_failure"]["computed_reason"])

    def test_exact_non_stale_rejection_is_still_an_invalid_trace(self):
        records = [
            base_event(
                parent_cell_id="2",
                claimed_outcome="rejected",
                claimed_reason="orphan",
            ),
            base_terminal(2, 1, 0, 1),
        ]
        self.assertEqual("orphan", first_reason(records))

    def test_orphan_parent_fails_against_trusted_target(self):
        records = [base_event(parent_cell_id="2"), base_terminal(2, 1, 1, 0)]
        self.assertEqual("orphan", first_reason(records))

    def test_same_instance_generation_rollback_fails(self):
        records = full_lifecycle_records()
        records[3]["source_generation"] = "1"
        self.assertEqual("generation-rollback", first_reason(records))

    def test_retired_source_instance_reuse_fails(self):
        records = full_lifecycle_records()
        records[9]["source_instance"] = "11"
        self.assertEqual("trace.source-reuse", first_reason(records))

    def test_rebind_before_rediscover_fails_state_transition(self):
        records = full_lifecycle_records()
        records[9]["event"] = "rebind"
        self.assertEqual("trace.state-transition", first_reason(records))

    def test_host_instance_drift_fails_after_native_checks(self):
        records = full_lifecycle_records()
        records[2]["host_instance"] = "7002"
        self.assertEqual("trace.host-instance", first_reason(records))

    def test_producer_instance_drift_fails(self):
        records = full_lifecycle_records()
        records[2]["producer_instance"] = "9002"
        self.assertEqual("trace.producer-instance", first_reason(records))

    def test_terminal_host_and_producer_identity_are_replayed(self):
        records = [base_event(), base_terminal(2, 1, 1, 0, host_instance="7002")]
        self.assertEqual("trace.host-instance", first_reason(records))

        records = [base_event(), base_terminal(2, 1, 1, 0, producer_instance="9002")]
        self.assertEqual("trace.producer-instance", first_reason(records))

    def test_semantic_native_reason_precedes_host_drift(self):
        records = full_lifecycle_records()
        records[3]["source_generation"] = "1"
        records[3]["host_instance"] = "7002"
        self.assertEqual("generation-rollback", first_reason(records))

    def test_host_drift_precedes_producer_drift(self):
        records = full_lifecycle_records()
        records[2]["host_instance"] = "7002"
        records[2]["producer_instance"] = "9002"
        self.assertEqual("trace.host-instance", first_reason(records))

    def test_orphan_precedes_kind_and_kind_precedes_role(self):
        records = [
            base_event(parent_cell_id="2", source_kind="other", source_role="worker"),
            base_terminal(2, 1, 1, 0),
        ]
        self.assertEqual("orphan", first_reason(records))

        records = [
            base_event(source_kind="other", source_role="worker"),
            base_terminal(2, 1, 1, 0),
        ]
        self.assertEqual("kind", first_reason(records))

    def test_stale_precedes_binding_generation_rollback(self):
        records = full_lifecycle_records()
        records[6]["bound_source_generation"] = "1"
        records[6]["binding_generation"] = "1"
        self.assertEqual("stale", first_reason(records))

    def test_cross_axis_stale_precedes_canonical_generation_rollback(self):
        records = full_lifecycle_records()
        records[6]["canonical_generation"] = "2"
        records[6]["source_generation"] = "1"
        self.assertEqual("stale", first_reason(records))

    def test_source_instance_mismatch_precedes_generation_rollback(self):
        records = full_lifecycle_records()
        records[6]["canonical_generation"] = "2"
        records[6]["source_instance"] = "99"
        self.assertEqual("instance", first_reason(records))

    def test_terminal_final_state_and_generation_are_recomputed(self):
        records = [base_event(), base_terminal(2, 1, 1, 0, "bound", "0")]
        self.assertEqual("trace.terminal", first_reason(records))

        records = [base_event(), base_terminal(2, 1, 1, 0, "discovered", "1")]
        self.assertEqual("trace.terminal", first_reason(records))

    def test_semantic_phase_does_not_run_after_transport_failure(self):
        verdict = verdict_of([base_event()])
        self.assertEqual("trace.terminal", verdict["first_failure"]["reason"])
        self.assertFalse(verdict["semantic_phase_executed"])
        self.assertNotIn("semantic", verdict["verified_phases"])

    def test_json_member_order_does_not_change_verdict(self):
        records = full_lifecycle_records()
        reversed_records = [dict(reversed(list(record.items()))) for record in records]
        normal = verdict_of(records)
        reversed_verdict = verdict_of(reversed_records)
        self.assertEqual(normal["outcome"], reversed_verdict["outcome"])
        self.assertEqual(normal["first_failure"], reversed_verdict["first_failure"])
        self.assertEqual(normal["final_state"], reversed_verdict["final_state"])


class ContractDriftTests(unittest.TestCase):
    def test_native_reject_reason_enum_matches_contract_string_order(self):
        import re

        header = (
            REPO_ROOT / "kernel" / "include" / "kernel" / "kernel_room_source_binding.h"
        ).read_text(encoding="utf-8")
        pairs = re.findall(
            r"^\s*KERNEL_ROOM_BINDING_REJECT_([A-Z_]+)\s*=\s*(\d+),",
            header,
            flags=re.MULTILINE,
        )
        numeric = {
            int(value): name.lower().replace("_", "-")
            for name, value in pairs
            if name != "COUNT"
        }
        self.assertEqual(
            [numeric[index] for index in range(len(numeric))],
            CONTRACT["enums"]["claimed_reason"],
        )

    def test_trusted_target_ids_and_generation_match_k1_header(self):
        import re

        header = (
            REPO_ROOT / "kernel" / "include" / "kernel" / "kernel_room_management.h"
        ).read_text(encoding="utf-8")

        def macro(name):
            match = re.search(rf"^#define\s+{name}\s+(\d+)(?:U|ULL)$", header, re.MULTILINE)
            self.assertIsNotNone(match, name)
            return match.group(1)

        target = CONTRACT["trusted_target_v1"]
        self.assertEqual(macro("KERNEL_ROOM_NODE_ID_MAIN_AI"), target["canonical_id"])
        self.assertEqual(macro("KERNEL_ROOM_CELL_ID_MAIN"), target["parent_cell_id"])
        self.assertEqual(
            macro("KERNEL_ROOM_MANAGEMENT_GENERATION"), target["canonical_generation"]
        )
        self.assertEqual(
            macro("KERNEL_ROOM_MANAGEMENT_GENERATION"), target["parent_generation"]
        )

    def test_string_mappings_match_native_header_numeric_sources(self):
        import re

        management = (
            REPO_ROOT / "kernel" / "include" / "kernel" / "kernel_room_management.h"
        ).read_text(encoding="utf-8")
        binding = (
            REPO_ROOT / "kernel" / "include" / "kernel" / "kernel_room_source_binding.h"
        ).read_text(encoding="utf-8")
        slm = (
            REPO_ROOT / "kernel" / "include" / "runtime" / "slm_orchestrator.h"
        ).read_text(encoding="utf-8")

        def enum_value(text, name):
            match = re.search(rf"^\s*{name}\s*=\s*(\d+),", text, re.MULTILINE)
            self.assertIsNotNone(match, name)
            return int(match.group(1))

        mappings = CONTRACT["string_mappings_v1"]
        self.assertEqual(
            enum_value(management, "KERNEL_ROOM_NAMESPACE_NODE"),
            mappings["canonical_namespace"]["node"],
        )
        self.assertEqual(
            enum_value(management, "KERNEL_ROOM_NODE_KIND_AI_SERVICE"),
            mappings["canonical_kind"]["ai-service"],
        )
        paired = (
            ("source_namespace", "native-slm-agent-tree", binding,
             "KERNEL_ROOM_BINDING_SOURCE_NAMESPACE_NATIVE_SLM_AGENT_TREE", slm,
             "SLM_AGENT_SOURCE_NAMESPACE_AGENT_TREE"),
            ("source_kind", "ai-service", binding,
             "KERNEL_ROOM_BINDING_SOURCE_KIND_AI_SERVICE", slm,
             "SLM_AGENT_SOURCE_KIND_AI_SERVICE"),
            ("source_role", "main", binding,
             "KERNEL_ROOM_BINDING_SOURCE_ROLE_MAIN", slm,
             "SLM_AGENT_SOURCE_ROLE_MAIN"),
            ("lifecycle_state", "active", binding,
             "KERNEL_ROOM_BINDING_SOURCE_LIFECYCLE_ACTIVE", slm,
             "SLM_AGENT_SOURCE_LIFECYCLE_ACTIVE"),
        )
        for mapping_name, token, left, left_name, right, right_name in paired:
            with self.subTest(mapping=mapping_name):
                expected = mappings[mapping_name][token]
                self.assertEqual(enum_value(left, left_name), expected)
                self.assertEqual(enum_value(right, right_name), expected)

    def test_native_projection_boot_tuple_matches_native_sources(self):
        import re

        slm_header = (
            REPO_ROOT / "kernel" / "include" / "runtime" / "slm_orchestrator.h"
        ).read_text(encoding="utf-8")
        binding_header = (
            REPO_ROOT / "kernel" / "include" / "kernel" / "kernel_room_source_binding.h"
        ).read_text(encoding="utf-8")
        producer = (
            REPO_ROOT / "kernel" / "runtime" / "slm_orchestrator.c"
        ).read_text(encoding="utf-8")

        def macro(text, name):
            match = re.search(rf"^#define\s+{name}\s+(\d+)(?:U|ULL)$", text, re.MULTILINE)
            self.assertIsNotNone(match, name)
            return match.group(1)

        target = CONTRACT["trusted_target_v1"]
        self.assertEqual(
            macro(slm_header, "SLM_AGENT_SOURCE_BOOT_INSTANCE"),
            target["native_source_instance"],
        )
        self.assertEqual(
            macro(slm_header, "SLM_AGENT_SOURCE_BOOT_GENERATION"),
            target["initial_source_generation"],
        )
        self.assertEqual(
            macro(binding_header, "KERNEL_ROOM_SOURCE_BINDING_GENERATION"),
            target["native_binding_generation"],
        )
        source_id = re.search(r"main_node->node_id != (\d+)U", producer)
        self.assertIsNotNone(source_id)
        self.assertEqual(source_id.group(1), target["native_source_id"])


class RawBoundaryTests(unittest.TestCase):
    def test_empty_trace_fails_with_io(self):
        verdict = raw_verdict(b"")
        self.assertFalse(verdict["passed"])
        self.assertEqual("trace.io", verdict["first_failure"]["reason"])

    def test_total_size_limit_fails(self):
        big_line = json.dumps(base_event(trace_id="a" * 300)).encode("utf-8")
        raw = b"\n".join([big_line] * 900) + b"\n"
        verdict = raw_verdict(raw)
        self.assertEqual("trace.limit", verdict["first_failure"]["reason"])

    def test_oversized_line_fails_with_limit(self):
        event = base_event()
        event["trace_id"] = "t" + "-x" * 2000
        raw = serialize([event, base_terminal(2, 1, 1, 0)])
        self.assertLess(len(raw), CONTRACT["transport"]["max_total_bytes"])
        verdict = raw_verdict(raw)
        self.assertEqual("trace.limit", verdict["first_failure"]["reason"])
        self.assertIn("limit", verdict["first_failure"]["detail"])

    def test_first_record_bom_fails_with_line_aware_encoding(self):
        payload = serialize([base_event(), base_terminal(2, 1, 1, 0)])
        verdict = raw_verdict(b"\xef\xbb\xbf" + payload)
        self.assertEqual("trace.encoding", verdict["first_failure"]["reason"])
        self.assertEqual(1, verdict["first_failure"]["line"])

    def test_raw_phase_returns_decoded_strings(self):
        payload = serialize([base_event(), base_terminal(2, 1, 1, 0)])
        lines, failures = replay._phase_raw(payload, CONTRACT)
        self.assertEqual([], failures)
        self.assertTrue(lines)
        self.assertTrue(all(isinstance(line, str) for line in lines))

    def test_crcrlf_and_bare_cr_fail_with_encoding(self):
        payload = serialize([base_event(), base_terminal(2, 1, 1, 0)])

        crcrlf = payload.replace(b"\n", b"\r\r\n")
        verdict = raw_verdict(crcrlf)
        self.assertEqual("trace.encoding", verdict["first_failure"]["reason"])
        self.assertEqual(1, verdict["first_failure"]["line"])
        self.assertIn("bare CR", verdict["first_failure"]["detail"])

        first_line, second_line = payload.splitlines(keepends=True)
        internal_bare_cr = first_line[:-1] + b"\r \n" + second_line
        verdict = raw_verdict(internal_bare_cr)
        self.assertEqual("trace.encoding", verdict["first_failure"]["reason"])
        self.assertEqual(1, verdict["first_failure"]["line"])
        self.assertIn("bare CR", verdict["first_failure"]["detail"])

    def test_middle_record_bom_fails_with_line_aware_encoding(self):
        payload = serialize([base_event(), base_terminal(2, 1, 1, 0)])
        first_line, second_line = payload.splitlines(keepends=True)
        verdict = raw_verdict(first_line + b"\xef\xbb\xbf" + second_line)
        self.assertEqual("trace.encoding", verdict["first_failure"]["reason"])
        self.assertEqual(2, verdict["first_failure"]["line"])
        self.assertIn("BOM", verdict["first_failure"]["detail"])

    def test_earlier_invalid_utf8_precedes_later_record_bom(self):
        terminal_line = serialize([base_terminal(2, 1, 1, 0)])
        raw = b'{"record_type":"event","bad":"\xff"}\n' + b"\xef\xbb\xbf" + terminal_line
        verdict = raw_verdict(raw)
        self.assertEqual("trace.encoding", verdict["first_failure"]["reason"])
        self.assertEqual(1, verdict["first_failure"]["line"])
        self.assertIn("not valid UTF-8", verdict["first_failure"]["detail"])

    def test_max_records_counts_terminal_and_allows_64_total_records(self):
        max_records = CONTRACT["transport"]["max_records"]
        events = [base_event(trace_sequence=1)]
        events.append(
            base_event(
                trace_sequence=2,
                event="bind",
                bound_source_instance="11",
                bound_source_generation="1",
                binding_generation="1",
                binding_valid=1,
                binding_current=1,
            )
        )
        events.extend(
            base_event(
                trace_sequence=index,
                event="observe",
                bound_source_instance="11",
                bound_source_generation="1",
                binding_generation="1",
                binding_valid=1,
                binding_current=1,
            )
            for index in range(3, max_records)
        )
        terminal = base_terminal(
            max_records,
            max_records - 1,
            max_records - 1,
            0,
            "bound",
            "1",
        )
        verdict = verdict_of([*events, terminal])
        self.assertTrue(verdict["passed"], verdict)
        self.assertEqual(max_records - 1, verdict["record_count"])

    def test_max_records_rejects_65_total_records_with_limit(self):
        max_records = CONTRACT["transport"]["max_records"]
        events = [base_event(trace_sequence=index) for index in range(1, max_records + 1)]
        terminal = base_terminal(max_records + 1, max_records, max_records, 0)
        raw = serialize([*events, terminal])
        self.assertLess(len(raw), CONTRACT["transport"]["max_total_bytes"])
        verdict = raw_verdict(raw)
        self.assertEqual("trace.limit", verdict["first_failure"]["reason"])
        self.assertEqual(max_records + 1, verdict["first_failure"]["line"])
        self.assertIn(f"exceeds {max_records}", verdict["first_failure"]["detail"])

    def test_earlier_line_limit_precedes_record_count_limit(self):
        max_records = CONTRACT["transport"]["max_records"]
        events = [base_event(trace_sequence=index) for index in range(1, max_records + 1)]
        events[0]["trace_id"] = "t" + "-x" * 2100
        terminal = base_terminal(max_records + 1, max_records, max_records, 0)
        raw = serialize([*events, terminal])
        self.assertLess(len(raw), CONTRACT["transport"]["max_total_bytes"])
        verdict = raw_verdict(raw)
        self.assertEqual("trace.limit", verdict["first_failure"]["reason"])
        self.assertEqual(1, verdict["first_failure"]["line"])
        self.assertIn("bytes, limit", verdict["first_failure"]["detail"])

    def test_record_limit_precedes_bom_encoding_failure(self):
        max_records = CONTRACT["transport"]["max_records"]
        events = [base_event(trace_sequence=index) for index in range(1, max_records + 1)]
        terminal = base_terminal(max_records + 1, max_records, max_records, 0)
        lines = serialize([*events, terminal]).splitlines(keepends=True)
        lines[1] = b"\xef\xbb\xbf" + lines[1]
        verdict = raw_verdict(b"".join(lines))
        self.assertEqual("trace.limit", verdict["first_failure"]["reason"])
        self.assertEqual(max_records + 1, verdict["first_failure"]["line"])

    def test_invalid_utf8_fails_with_encoding_at_line(self):
        good = serialize([base_event()]).decode("utf-8")
        raw = (good + "{\xff}\n").encode("latin-1")
        verdict = raw_verdict(raw)
        self.assertEqual("trace.encoding", verdict["first_failure"]["reason"])
        self.assertEqual(2, verdict["first_failure"]["line"])

    def test_missing_final_newline_fails_truncated(self):
        payload = serialize([base_event(), base_terminal(2, 1, 1, 0)])
        verdict = raw_verdict(payload[:-1])
        self.assertEqual("trace.truncated", verdict["first_failure"]["reason"])

    def test_unterminated_lone_cr_fails_encoding_before_truncated(self):
        verdict = raw_verdict(b"{}\r")
        self.assertEqual("trace.encoding", verdict["first_failure"]["reason"])
        self.assertEqual(1, verdict["first_failure"]["line"])
        self.assertIn("bare CR", verdict["first_failure"]["detail"])
        self.assertIn("trace.truncated", verdict["reasons"])

    def test_actual_crlf_terminator_is_not_a_bare_cr(self):
        verdict = raw_verdict(b"{}\r\n")
        self.assertNotIn("trace.encoding", verdict["reasons"])
        self.assertEqual("trace.missing-field", verdict["first_failure"]["reason"])

    def test_blank_line_fails_with_syntax(self):
        good = serialize([base_event()]).decode("utf-8")
        raw = (good + "\n" + good).encode("utf-8")
        verdict = raw_verdict(raw)
        self.assertEqual("trace.syntax", verdict["first_failure"]["reason"])
        self.assertEqual(2, verdict["first_failure"]["line"])


class JsonPhaseTests(unittest.TestCase):
    def test_malformed_json_fails_with_syntax(self):
        raw = b"{not json at all\n"
        verdict = raw_verdict(raw)
        self.assertEqual("trace.syntax", verdict["first_failure"]["reason"])
        self.assertEqual(1, verdict["first_failure"]["line"])

    def test_deep_json_within_line_limit_fails_with_stable_syntax(self):
        raw = b"[" * 1200 + b"]" * 1200 + b"\n"
        self.assertLess(len(raw) - 1, CONTRACT["transport"]["max_line_bytes"])
        verdict = raw_verdict(raw)
        self.assertFalse(verdict["passed"])
        self.assertEqual("trace.syntax", verdict["first_failure"]["reason"])
        self.assertEqual(1, verdict["first_failure"]["line"])
        self.assertEqual("JSON nesting exceeds decoder limit", verdict["first_failure"]["detail"])
        self.assertFalse(verdict["semantic_phase_executed"])

    def test_duplicate_json_key_fails_before_shape(self):
        raw = b'{"schema_version": 1, "schema_version": 1}\n'
        verdict = raw_verdict(raw)
        self.assertEqual("trace.duplicate-key", verdict["first_failure"]["reason"])

    def test_non_finite_json_tokens_fail_with_syntax(self):
        for token in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(token=token):
                text = json.dumps(base_event()).replace(
                    '"observed_at_valid": 0', f'"observed_at_valid": {token}'
                )
                verdict = raw_verdict((text + "\n").encode("utf-8"))
                self.assertEqual("trace.syntax", verdict["first_failure"]["reason"])

    def test_top_level_array_is_not_an_object(self):
        raw = b'[{"schema_version": 1}]\n'
        verdict = raw_verdict(raw)
        self.assertEqual("trace.type", verdict["first_failure"]["reason"])
        self.assertIn("flat JSON object", verdict["first_failure"]["detail"])

    def test_nested_object_in_extra_field_reports_unknown_first(self):
        event_text = json.dumps(base_event())[:-1] + ', "nested": {"deep": true}}'
        verdict = raw_verdict((event_text + "\n").encode("utf-8"))
        self.assertEqual("trace.unknown-field", verdict["first_failure"]["reason"])

    def test_array_valued_schema_field_fails_type(self):
        records = [base_event(trace_id=["not-a-string"])]
        verdict = verdict_of(records)
        self.assertEqual("trace.type", verdict["first_failure"]["reason"])
        self.assertIn("list", verdict["first_failure"]["detail"])


class ShapeTests(unittest.TestCase):
    def test_invalid_first_trace_id_is_not_promoted_or_replaced_by_later_id(self):
        for trace_id in ("\ud800", "Bad_ID", "", "a" * 65, 123):
            with self.subTest(trace_id=repr(trace_id)):
                verdict = verdict_of(
                    [base_event(trace_id=trace_id), base_terminal(2, 1, 1, 0)]
                )
                self.assertFalse(verdict["passed"])
                self.assertEqual("trace.type", verdict["first_failure"]["reason"])
                self.assertIsNone(verdict["trace_id"])

    def test_missing_field_fails_before_unknown_field(self):
        event = base_event()
        del event["copied_read"]
        event["mystery_field"] = 1
        records = [event]
        verdict = verdict_of(records)
        self.assertEqual("trace.missing-field", verdict["first_failure"]["reason"])

    def test_unknown_field_fails(self):
        records = [base_event(mystery=7)]
        self.assertEqual("trace.unknown-field", first_reason(records))

    def test_missing_fields_list_contract_order(self):
        event = base_event()
        del event["schema_version"]
        del event["claimed_reason"]
        del event["management_only"]
        verdict = verdict_of([event])
        self.assertEqual("trace.missing-field", verdict["first_failure"]["reason"])
        detail = verdict["first_failure"]["detail"]
        self.assertLess(detail.index("schema_version"), detail.index("claimed_reason"))
        self.assertLess(detail.index("claimed_reason"), detail.index("management_only"))

    def test_bool_for_flag_fails_type(self):
        self.assertEqual("trace.type", first_reason([base_event(producer_owned=True)]))
        self.assertEqual("trace.type", first_reason([base_event(schema_version=True)]))

    def test_float_for_u32_fails_type(self):
        self.assertEqual("trace.type", first_reason([base_event(trace_sequence=1.0)]))

    def test_exponent_for_u32_fails_type(self):
        raw = json.dumps(base_event()).replace(
            '"producer_instance": "9001"', '"producer_instance": 9e3', 1
        )
        verdict = raw_verdict((raw + "\n").encode("utf-8"))
        self.assertEqual("trace.type", verdict["first_failure"]["reason"])

    def test_negative_and_overflow_u32_fail_range(self):
        self.assertEqual("trace.range", first_reason([base_event(trace_sequence=-1)]))
        self.assertEqual("trace.range", first_reason([base_event(trace_sequence=4294967296)]))

    def test_flag_out_of_range_fails(self):
        self.assertEqual("trace.range", first_reason([base_event(binding_current=2)]))

    def test_u64_decimal_as_number_fails_type(self):
        self.assertEqual("trace.type", first_reason([base_event(host_instance=7001)]))

    def test_u64_decimal_bad_format_fails_range(self):
        self.assertEqual("trace.range", first_reason([base_event(host_instance="00701")]))
        self.assertEqual("trace.range", first_reason([base_event(host_instance="9x")]))

    def test_u64_decimal_overflow_fails_range(self):
        self.assertEqual(
            "trace.range", first_reason([base_event(host_instance="18446744073709551616")])
        )
        self.assertEqual(
            "trace.range", first_reason([base_event(host_instance="100000000000000000000")])
        )

    def test_schema_version_constant_violation_fails_range(self):
        self.assertEqual("trace.range", first_reason([base_event(schema_version=2)]))

    def test_observation_only_zero_fails_range(self):
        self.assertEqual("trace.range", first_reason([base_event(observation_only=0)]))

    def test_management_only_zero_fails_range(self):
        self.assertEqual("trace.range", first_reason([base_event(management_only=0)]))

    def test_unknown_record_type_fails_event(self):
        raw = json.dumps(base_event()).replace('"record_type": "event"', '"record_type": "summary"', 1)
        verdict = raw_verdict((raw + "\n").encode("utf-8"))
        self.assertEqual("trace.event", verdict["first_failure"]["reason"])

    def test_unknown_event_name_fails_event(self):
        self.assertEqual("trace.event", first_reason([base_event(event="reconcile")]))

    def test_claimed_pairing_violations_fail_outcome(self):
        self.assertEqual(
            "trace.outcome",
            first_reason([base_event(claimed_outcome="accepted", claimed_reason="stale")]),
        )
        self.assertEqual(
            "trace.outcome",
            first_reason([base_event(claimed_outcome="rejected", claimed_reason="none")]),
        )
        self.assertEqual(
            "trace.outcome", first_reason([base_event(claimed_reason="exploded")])
        )

    def test_unknown_lifecycle_state_fails_type_membership(self):
        raw = json.dumps(base_event()).replace(
            '"lifecycle_state": "active"', '"lifecycle_state": "zombie"', 1
        )
        verdict = raw_verdict((raw + "\n").encode("utf-8"))
        self.assertEqual("trace.type", verdict["first_failure"]["reason"])

    def test_kebab_token_violation_fails_type(self):
        self.assertEqual("trace.type", first_reason([base_event(trace_id="Bad_ID")]))
        self.assertEqual("trace.type", first_reason([base_event(source_role="MAIN")]))


class EnvelopeTests(unittest.TestCase):
    def test_event_trace_id_drift_fails_with_dedicated_reason(self):
        records = full_lifecycle_records()
        records[2]["trace_id"] = "different-trace"
        self.assertEqual("trace.trace-id", first_reason(records))

    def test_terminal_trace_id_drift_fails_with_dedicated_reason(self):
        records = [base_event(), base_terminal(2, 1, 1, 0, trace_id="different-trace")]
        self.assertEqual("trace.trace-id", first_reason(records))

    def test_sequence_zero_start_fails(self):
        records = [base_event(trace_sequence=0), base_terminal(2, 1, 1, 0)]
        self.assertEqual("trace.sequence", first_reason(records))

    def test_sequence_gap_fails(self):
        records = [
            base_event(trace_sequence=1),
            base_event(trace_sequence=3),
            base_terminal(4, 2, 2, 0),
        ]
        verdict = verdict_of(records)
        self.assertEqual("trace.sequence", verdict["first_failure"]["reason"])
        self.assertEqual(2, verdict["first_failure"]["line"])

    def test_duplicate_sequence_fails(self):
        records = [base_event(trace_sequence=1), base_event(trace_sequence=1)]
        self.assertEqual("trace.sequence", first_reason(records))

    def test_reordered_sequence_fails(self):
        records = [base_event(trace_sequence=2), base_event(trace_sequence=1)]
        self.assertEqual("trace.sequence", first_reason(records))

    def test_missing_terminal_fails(self):
        self.assertEqual("trace.terminal", first_reason([base_event()]))

    def test_double_terminal_fails(self):
        records = [base_event(), base_terminal(2, 1, 1, 0), base_terminal(3, 1, 1, 0)]
        verdict = verdict_of(records)
        self.assertEqual("trace.terminal", verdict["first_failure"]["reason"])
        self.assertEqual(3, verdict["first_failure"]["line"])

    def test_record_after_terminal_fails(self):
        records = [base_terminal(1, 0, 0, 0, "discovered"), base_event(trace_sequence=2)]
        verdict = verdict_of(records)
        self.assertEqual("trace.terminal", verdict["first_failure"]["reason"])

    def test_record_count_mismatch_fails(self):
        records = [base_event(), base_terminal(2, 5, 5, 0)]
        verdict = verdict_of(records)
        self.assertEqual("trace.terminal", verdict["first_failure"]["reason"])
        self.assertIn("does not match 1 event rows", verdict["first_failure"]["detail"])

    def test_count_arithmetic_mismatch_fails(self):
        records = [base_event(), base_event(trace_sequence=2), base_terminal(3, 2, 2, 1)]
        self.assertEqual("trace.terminal", first_reason(records))

    def test_terminal_sequence_not_record_count_plus_one_fails(self):
        records = [base_event(), base_terminal(7, 1, 1, 0)]
        verdict = verdict_of(records)
        self.assertEqual("trace.sequence", verdict["first_failure"]["reason"])
        self.assertEqual(2, verdict["first_failure"]["line"])


class PhasePriorityTests(unittest.TestCase):
    def test_later_encoding_failure_beats_earlier_blank_line_syntax(self):
        cases = (
            (b"\xef\xbb\xbf{}", "BOM"),
            (b'{"bad":"\xff"}', "not valid UTF-8"),
        )
        for malformed_line, expected_detail in cases:
            with self.subTest(expected_detail=expected_detail):
                verdict = raw_verdict(b"\n" + malformed_line + b"\n")
                self.assertEqual("trace.encoding", verdict["first_failure"]["reason"])
                self.assertEqual(2, verdict["first_failure"]["line"])
                self.assertIn(expected_detail, verdict["first_failure"]["detail"])
                self.assertIn("trace.syntax", verdict["reasons"])

    def test_phase_three_failure_beats_later_phase_four_failure(self):
        row1 = base_event(trace_sequence=99)
        row2 = base_event(trace_sequence=2, mystery=1)
        records = [row1, row2]
        verdict = verdict_of(records)
        self.assertEqual("trace.unknown-field", verdict["first_failure"]["reason"])
        self.assertEqual(2, verdict["first_failure"]["line"])

    def test_raw_phase_failure_beats_everything(self):
        records = [base_event(mystery=1)]
        text = serialize(records).decode("utf-8")
        raw = ("   \n" + text).encode("utf-8")
        verdict = raw_verdict(raw)
        self.assertEqual("trace.syntax", verdict["first_failure"]["reason"])
        self.assertEqual("blank line", verdict["first_failure"]["detail"])


class CliTests(unittest.TestCase):
    def _run_cli(self, raw: bytes, extra_args=None, *, json_output=True):
        import os
        import subprocess
        import sys

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as handle:
            handle.write(raw)
            trace_path = Path(handle.name)
        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    str(REPLAY_PATH),
                    str(trace_path),
                    *(["--json"] if json_output else []),
                    *(extra_args or []),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                env={**os.environ, "PYTHONIOENCODING": "utf-8:strict"},
                cwd=str(REPO_ROOT),
            )
            return completed
        finally:
            trace_path.unlink(missing_ok=True)

    def test_cli_exit_codes_match_verdict(self):
        passing = self._run_cli(serialize([base_event(), base_terminal(2, 1, 1, 0)]))
        self.assertEqual(0, passing.returncode, passing.stdout)

        failing = self._run_cli(serialize([base_event()]))
        self.assertEqual(1, failing.returncode)
        verdict = json.loads(failing.stdout)
        self.assertEqual("FAIL", verdict["outcome"])
        self.assertEqual("trace.terminal", verdict["first_failure"]["reason"])

    def test_cli_json_verdict_round_trip(self):
        completed = self._run_cli(serialize([base_event(), base_terminal(2, 1, 1, 0)]))
        verdict = json.loads(completed.stdout)
        expected_keys = {
            "schema_version", "outcome", "passed", "trace_id", "record_count",
            "accepted_count", "rejected_count", "last_sequence", "final_state",
            "final_binding_generation", "first_failure", "reasons",
            "observation_only", "management_only",
        }
        self.assertTrue(expected_keys.issubset(verdict.keys()))
        self.assertEqual(1, verdict["schema_version"])
        self.assertEqual(1, verdict["observation_only"])
        self.assertEqual(1, verdict["management_only"])
        self.assertEqual(verdict["semantic_replay"], "implemented")
        self.assertTrue(verdict["semantic_phase_executed"])
        self.assertIn("semantic", verdict["verified_phases"])

    def test_deep_json_cli_emits_fail_json_without_traceback(self):
        completed = self._run_cli(b"[" * 1200 + b"]" * 1200 + b"\n")
        self.assertEqual(1, completed.returncode, completed.stderr)
        verdict = json.loads(completed.stdout)
        self.assertFalse(verdict["passed"])
        self.assertEqual("trace.syntax", verdict["first_failure"]["reason"])
        self.assertNotIn("Traceback", completed.stderr)

    def test_surrogate_claims_emit_utf8_fail_json_without_traceback(self):
        cases = (
            (
                [base_event(trace_id="\ud800"), base_terminal(2, 1, 1, 0)],
                "trace.type",
                None,
            ),
            (
                [base_event(), base_terminal(2, 1, "\ud800", 0)],
                "trace.type",
                "h1a-selftest",
            ),
            (
                [base_event(**{"bad\ud800key": 1}), base_terminal(2, 1, 1, 0)],
                "trace.unknown-field",
                "h1a-selftest",
            ),
        )
        for records, reason, trace_id in cases:
            with self.subTest(reason=reason, trace_id=trace_id):
                raw = ("\n".join(json.dumps(record) for record in records) + "\n").encode("utf-8")
                completed = self._run_cli(raw)
                self.assertEqual(1, completed.returncode, completed.stderr)
                verdict = json.loads(completed.stdout)
                self.assertFalse(verdict["passed"])
                self.assertEqual(reason, verdict["first_failure"]["reason"])
                self.assertEqual(trace_id, verdict["trace_id"])
                self.assertNotIn("Traceback", completed.stderr)

    def test_surrogate_keys_emit_utf8_human_failure_without_traceback(self):
        event = base_event(**{"bad\ud800key": 1})
        cases = (
            (
                (json.dumps(event) + "\n").encode("utf-8"),
                "trace.unknown-field",
            ),
            (
                b'{"bad\\ud800key":1,"bad\\ud800key":2}\n',
                "trace.duplicate-key",
            ),
        )
        for raw, reason in cases:
            with self.subTest(reason=reason):
                completed = self._run_cli(raw, json_output=False)
                self.assertEqual(1, completed.returncode, completed.stderr)
                self.assertIn(f"[BINDING-TRACE] FAIL first_reason={reason}", completed.stdout)
                self.assertIn(r"bad\ud800key", completed.stdout)
                self.assertNotIn("Traceback", completed.stderr)


if __name__ == "__main__":
    unittest.main()
