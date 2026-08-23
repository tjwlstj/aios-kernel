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


def base_terminal(sequence, record_count, accepted, rejected, final_state="discovered"):
    return {
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
        "final_binding_generation": "0",
        "observation_only": 1,
        "management_only": 1,
    }


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

    def test_bom_prefix_fails_with_encoding(self):
        payload = serialize([base_event(), base_terminal(2, 1, 1, 0)])
        verdict = raw_verdict(b"\xef\xbb\xbf" + payload)
        self.assertEqual("trace.encoding", verdict["first_failure"]["reason"])

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

    def test_duplicate_json_key_fails_before_shape(self):
        raw = b'{"schema_version": 1, "schema_version": 1}\n'
        verdict = raw_verdict(raw)
        self.assertEqual("trace.duplicate-key", verdict["first_failure"]["reason"])

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
    def _run_cli(self, raw: bytes, extra_args=None):
        import subprocess
        import sys

        with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as handle:
            handle.write(raw)
            trace_path = Path(handle.name)
        try:
            completed = subprocess.run(
                [sys.executable, str(REPLAY_PATH), str(trace_path), "--json"],
                capture_output=True,
                text=True,
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
        self.assertNotEqual(verdict["semantic_replay"], "implemented")


if __name__ == "__main__":
    unittest.main()
