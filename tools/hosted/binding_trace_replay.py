#!/usr/bin/env python3
"""H1-a strict JSONL loader and transport verifier for AIOS binding traces v1.

This tool owns transport-level verdicts only (trace.* reason namespaces,
phases 1-4 of the workplan first-reason priority). Semantic lifecycle replay,
claimed-versus-computed outcome comparison, and fixture sidecars arrive in
H1-b/H1-c per docs/os/h1_binding_trace_replay_workplan_ko.md. A PASS from this
tool therefore proves structural contract conformance, not semantic binding
correctness.

Stdlib-only (Python 3.11). Exit codes: 0 replay PASS, 1 replay FAIL,
2 usage or unreadable input error.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = REPO_ROOT / "hosted" / "contracts" / "binding-trace-v1.contract.json"

VERDICT_SCHEMA_VERSION = 1

R_IO = "trace.io"
R_LIMIT = "trace.limit"
R_ENCODING = "trace.encoding"
R_TRUNCATED = "trace.truncated"
R_SYNTAX = "trace.syntax"
R_DUP_KEY = "trace.duplicate-key"
R_MISSING_FIELD = "trace.missing-field"
R_UNKNOWN_FIELD = "trace.unknown-field"
R_TYPE = "trace.type"
R_RANGE = "trace.range"
R_SEQUENCE = "trace.sequence"
R_EVENT = "trace.event"
R_OUTCOME = "trace.outcome"
R_TERMINAL = "trace.terminal"

PHASE_RAW = 1
PHASE_JSON = 2
PHASE_SHAPE = 3
PHASE_ENVELOPE = 4

U64_MAX = (1 << 64) - 1
U32_MAX = (1 << 32) - 1


class Failure:
    __slots__ = ("phase", "reason", "line", "sequence", "detail")

    def __init__(self, phase: int, reason: str, line: int | None, sequence: int | None, detail: str):
        self.phase = phase
        self.reason = reason
        self.line = line
        self.sequence = sequence
        self.detail = detail

    def as_dict(self) -> dict[str, Any]:
        return {
            "reason": self.reason,
            "line": self.line,
            "sequence": self.sequence,
            "detail": self.detail,
        }


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_contract(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    return json.loads(text, object_pairs_hook=_object_without_duplicate_keys)


def _compiled(contract: dict[str, Any]) -> tuple[dict[str, Any], dict[str, re.Pattern[str]]]:
    types = contract["scalar_types"]
    patterns = {
        name: re.compile(spec["pattern"])
        for name, spec in types.items()
        if "pattern" in spec
    }
    return types, patterns


def _phase_raw(raw: bytes, contract: dict[str, Any]) -> tuple[list[bytes], list[Failure]]:
    """io -> limit(total) -> encoding -> truncated, then per-line limits/decode."""
    transport = contract["transport"]
    failures: list[Failure] = []

    if len(raw) == 0:
        failures.append(Failure(PHASE_RAW, R_IO, None, None, "empty trace"))
        return [], failures

    max_total = int(transport["max_total_bytes"])
    if len(raw) > max_total:
        failures.append(
            Failure(PHASE_RAW, R_LIMIT, None, None, f"total size {len(raw)} exceeds {max_total}")
        )
        return [], failures

    if raw.startswith(b"\xef\xbb\xbf"):
        failures.append(Failure(PHASE_RAW, R_ENCODING, 1, None, "UTF-8 BOM is not allowed"))
        return [], failures

    body = raw
    trailing_newline = True
    if not body.endswith(b"\n"):
        trailing_newline = False
        body = body + b"\n"

    line_bytes_list = body[:-1].split(b"\n")
    contents: list[bytes] = []
    for index, line in enumerate(line_bytes_list, start=1):
        if line.endswith(b"\r"):
            line = line[:-1]
        contents.append(line)

    max_line = int(transport["max_line_bytes"])
    for index, line in enumerate(contents, start=1):
        if len(line) > max_line:
            failures.append(
                Failure(PHASE_RAW, R_LIMIT, index, None, f"line {index} has {len(line)} bytes, limit {max_line}")
            )

    decoded_lines: list[str] = []
    for index, line in enumerate(contents, start=1):
        try:
            decoded_lines.append(line.decode("utf-8", errors="strict"))
        except UnicodeDecodeError:
            failures.append(Failure(PHASE_RAW, R_ENCODING, index, None, f"line {index} is not valid UTF-8"))
            decoded_lines.append("")

    if not trailing_newline:
        failures.append(
            Failure(PHASE_RAW, R_TRUNCATED, len(contents), None, "final newline missing")
        )

    blank_allowed = bool(transport["blank_lines_allowed"])
    if not blank_allowed:
        for index, text in enumerate(decoded_lines, start=1):
            if text.strip() == "":
                failures.append(Failure(PHASE_RAW, R_SYNTAX, index, None, "blank line"))

    return contents, failures


def _phase_json(lines: list[str]) -> tuple[list[Any], list[Failure]]:
    records: list[Any] = []
    failures: list[Failure] = []
    for index, text in enumerate(lines, start=1):
        record: Any = None
        try:
            record = json.loads(text, object_pairs_hook=_object_without_duplicate_keys)
        except ValueError as exc:
            message = str(exc)
            if message.startswith("duplicate JSON key:"):
                failures.append(Failure(PHASE_JSON, R_DUP_KEY, index, None, message))
            else:
                detail = message.replace("\n", " ")
                failures.append(Failure(PHASE_JSON, R_SYNTAX, index, None, detail))
            records.append(None)
            continue
        records.append(record)
    return records, failures


def _sequence_of(record: Any) -> int | None:
    if isinstance(record, dict):
        value = record.get("trace_sequence")
        if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= U32_MAX:
            return value
    return None


def _check_scalar(field_name: str, spec_name: str, value: Any, types: dict[str, Any],
                  patterns: dict[str, re.Pattern[str]]) -> Failure | None:
    spec = types[spec_name]
    expected_json_type = spec["json_type"]

    if spec_name == "u64-decimal":
        if type(value) is not str:
            return Failure(PHASE_SHAPE, R_TYPE, None, None,
                           f"{field_name} must be a decimal string, got {type(value).__name__}")
        pattern = patterns["u64-decimal"]
        if not pattern.fullmatch(value):
            return Failure(PHASE_SHAPE, R_RANGE, None, None,
                           f"{field_name} value {value!r} violates u64-decimal format")
        if int(value) > int(spec["max_value_string"]):
            return Failure(PHASE_SHAPE, R_RANGE, None, None,
                           f"{field_name} value {value!r} exceeds u64 range")
        return None

    if expected_json_type == "string":
        if type(value) is not str:
            return Failure(PHASE_SHAPE, R_TYPE, None, None,
                           f"{field_name} must be a string, got {type(value).__name__}")
        pattern = patterns.get(spec_name)
        if pattern is not None and not pattern.fullmatch(value):
            return Failure(PHASE_SHAPE, R_TYPE, None, None,
                           f"{field_name} value {value!r} violates {spec_name} pattern")
        return None

    # integer families: u32, flag
    if isinstance(value, bool):
        return Failure(PHASE_SHAPE, R_TYPE, None, None,
                       f"{field_name} must be an integer, got boolean")
    if not isinstance(value, int):
        return Failure(PHASE_SHAPE, R_TYPE, None, None,
                       f"{field_name} must be an integer, got {type(value).__name__}")
    minimum = int(spec["min_value"])
    maximum = int(spec["max_value"])
    if value < minimum or value > maximum:
        return Failure(PHASE_SHAPE, R_RANGE, None, None,
                       f"{field_name} value {value} outside [{minimum}, {maximum}]")
    return None


def _check_constant(field_name: str, value: Any, constants: dict[str, Any]) -> Failure | None:
    if field_name in constants and value != constants[field_name]:
        return Failure(PHASE_SHAPE, R_RANGE, None, None,
                       f"{field_name} must equal {constants[field_name]}")
    return None


def _enum_failure(field_name: str, enum_name: str, value: Any,
                  enums: dict[str, Any]) -> Failure | None:
    allowed = enums[enum_name]
    if type(value) is not str:
        return Failure(PHASE_SHAPE, R_TYPE, None, None,
                       f"{field_name} must be a string, got {type(value).__name__}")
    if value not in allowed:
        if enum_name == "record_type" or enum_name == "event":
            reason = R_EVENT
        elif enum_name.startswith("claimed_"):
            reason = R_OUTCOME
        else:
            reason = R_TYPE
        return Failure(PHASE_SHAPE, reason, None, None,
                       f"{field_name} value {value!r} not in {enum_name} enum")
    return None


def _phase_shape(records: list[Any], contract: dict[str, Any]) -> list[Failure]:
    failures: list[Failure] = []
    types, patterns = _compiled(contract)
    enums = contract["enums"]
    constants = contract["constants"]
    schemas = contract["records"]

    for row_index, record in enumerate(records, start=1):
        sequence = _sequence_of(record)

        if not isinstance(record, dict):
            failures.append(
                Failure(PHASE_SHAPE, R_TYPE, row_index, sequence,
                        "record must be a flat JSON object")
            )
            continue

        record_type = record.get("record_type")
        if "record_type" not in record:
            failures.append(
                Failure(PHASE_SHAPE, R_MISSING_FIELD, row_index, sequence,
                        "missing fields: record_type")
            )
            continue
        if not isinstance(record_type, str):
            failures.append(
                Failure(PHASE_SHAPE, R_TYPE, row_index, sequence,
                        f"record_type must be a string, got {type(record_type).__name__}")
            )
            continue
        if record_type not in enums["record_type"]:
            failures.append(
                Failure(PHASE_SHAPE, R_EVENT, row_index, sequence,
                        f"record_type value {record_type!r} not in record_type enum")
            )
            continue
        schema = schemas[record_type]

        field_order = [entry["name"] for entry in schema["fields_in_order"]]
        expected = set(field_order)
        actual = set(record)

        missing = sorted(expected - actual, key=field_order.index)
        if missing:
            failures.append(
                Failure(PHASE_SHAPE, R_MISSING_FIELD, row_index, sequence,
                        "missing fields: " + ", ".join(missing))
            )
        unknown = sorted(actual - expected)
        if unknown:
            failures.append(
                Failure(PHASE_SHAPE, R_UNKNOWN_FIELD, row_index, sequence,
                        "unknown fields: " + ", ".join(unknown))
            )
        if missing:
            continue

        for entry in schema["fields_in_order"]:
            name = entry["name"]
            type_spec = entry["type"]
            value = record[name]
            failure: Failure | None = None
            if type_spec.startswith("enum:"):
                failure = _enum_failure(name, type_spec.split(":", 1)[1], value, enums)
            else:
                failure = _check_scalar(name, type_spec, value, types, patterns)
            if failure is None:
                failure = _check_constant(name, value, constants)
            if failure is not None:
                failure.line = row_index
                failure.sequence = sequence
                failures.append(failure)

        for name, value in record.items():
            if isinstance(value, dict):
                failures.append(
                    Failure(PHASE_SHAPE, R_TYPE, row_index, sequence,
                            f"{name} contains a nested object; flat records only")
                )
            elif isinstance(value, list):
                failures.append(
                    Failure(PHASE_SHAPE, R_TYPE, row_index, sequence,
                            f"{name} contains an array; arrays are forbidden")
                )
    return failures


def _phase_envelope(records: list[Any], contract: dict[str, Any]) -> list[Failure]:
    failures: list[Failure] = []

    for row_index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            continue
        sequence = _sequence_of(record)
        expected_sequence = row_index
        if sequence != expected_sequence:
            failures.append(
                Failure(PHASE_ENVELOPE, R_SEQUENCE, row_index, sequence,
                        f"trace_sequence {sequence} must equal record position {expected_sequence}")
            )

        outcome = record.get("claimed_outcome")
        reason = record.get("claimed_reason")
        pairing = contract["outcome_pairing"]
        if isinstance(outcome, str) and isinstance(reason, str) and outcome in pairing:
            if reason not in pairing[outcome]:
                failures.append(
                    Failure(PHASE_ENVELOPE, R_OUTCOME, row_index, sequence,
                            f"claimed_outcome {outcome!r} does not allow claimed_reason {reason!r}")
                )

    terminals = [
        (index, record)
        for index, record in enumerate(records, start=1)
        if isinstance(record, dict) and record.get("record_type") == "terminal"
    ]

    if len(terminals) == 0:
        failures.append(
            Failure(PHASE_ENVELOPE, R_TERMINAL, len(records), None, "terminal record missing")
        )
        return failures

    if len(terminals) > 1:
        first_extra = terminals[1][0]
        failures.append(
            Failure(PHASE_ENVELOPE, R_TERMINAL, first_extra, None,
                    f"expected exactly one terminal record, found {len(terminals)}")
        )

    terminal_index, terminal = terminals[0]
    if terminal_index != len(records):
        follower = terminal_index + 1
        failures.append(
            Failure(PHASE_ENVELOPE, R_TERMINAL, follower, None,
                    "terminal must be the last record")
        )
        return failures

    event_rows = len(records) - 1
    record_count = terminal.get("record_count")
    accepted_count = terminal.get("accepted_count")
    rejected_count = terminal.get("rejected_count")
    terminal_sequence = _sequence_of(terminal)

    if isinstance(record_count, int) and not isinstance(record_count, bool):
        if record_count != event_rows:
            failures.append(
                Failure(PHASE_ENVELOPE, R_TERMINAL, terminal_index, terminal_sequence,
                        f"terminal record_count {record_count} does not match {event_rows} event rows")
            )
        if isinstance(terminal_sequence, int) and terminal_sequence != record_count + 1:
            failures.append(
                Failure(PHASE_ENVELOPE, R_TERMINAL, terminal_index, terminal_sequence,
                        f"terminal trace_sequence {terminal_sequence} must equal record_count+1 ({record_count + 1})")
            )
        if (
            isinstance(accepted_count, int) and not isinstance(accepted_count, bool)
            and isinstance(rejected_count, int) and not isinstance(rejected_count, bool)
        ):
            if accepted_count + rejected_count != record_count:
                failures.append(
                    Failure(PHASE_ENVELOPE, R_TERMINAL, terminal_index, terminal_sequence,
                            f"accepted_count {accepted_count} + rejected_count {rejected_count} "
                            f"does not equal record_count {record_count}")
                )
    return failures


def verify_trace_bytes(raw: bytes, contract: dict[str, Any]) -> dict[str, Any]:
    """Verify raw trace bytes against the v1 contract (phases 1-4 only)."""
    lines, raw_failures = _phase_raw(raw, contract)
    records, json_failures = _phase_json(lines)
    shape_failures = _phase_shape(records, contract)
    envelope_failures = _phase_envelope(records, contract)

    all_failures: list[Failure] = []
    all_failures.extend(raw_failures)
    all_failures.extend(json_failures)
    all_failures.extend(shape_failures)
    all_failures.extend(envelope_failures)
    all_failures.sort(key=lambda item: (item.phase, item.line if item.line is not None else 0))

    passed = not all_failures

    trace_id = None
    terminal_summary: dict[str, Any] = {}
    for record in records:
        if isinstance(record, dict):
            if trace_id is None and isinstance(record.get("trace_id"), str):
                trace_id = record["trace_id"]
    parsed_rows = sum(1 for record in records if isinstance(record, dict))
    last_sequence: int | None = parsed_rows if passed else None
    for record in reversed(records):
        if isinstance(record, dict) and record.get("record_type") == "terminal":
            terminal_summary = {
                "record_count": record.get("record_count"),
                "accepted_count": record.get("accepted_count"),
                "rejected_count": record.get("rejected_count"),
                "final_state": record.get("final_state"),
                "final_binding_generation": record.get("final_binding_generation"),
            }
            break

    reasons: list[str] = []
    for failure in all_failures:
        if failure.reason not in reasons:
            reasons.append(failure.reason)

    first_failure = all_failures[0] if all_failures else None

    return {
        "schema_version": VERDICT_SCHEMA_VERSION,
        "outcome": "PASS" if passed else "FAIL",
        "passed": passed,
        "trace_id": trace_id,
        "record_count": terminal_summary.get("record_count"),
        "accepted_count": terminal_summary.get("accepted_count"),
        "rejected_count": terminal_summary.get("rejected_count"),
        "last_sequence": last_sequence if passed else None,
        "final_state": terminal_summary.get("final_state") if passed else None,
        "final_binding_generation": terminal_summary.get("final_binding_generation") if passed else None,
        "first_failure": first_failure.as_dict() if first_failure is not None else None,
        "reasons": reasons,
        "observation_only": contract["constants"]["observation_only"],
        "management_only": contract["constants"]["management_only"],
        "verified_phases": ["raw", "json", "shape", "envelope"],
        "semantic_replay": "not-implemented-h1-b-pending",
    }


def format_human_verdict(verdict: dict[str, Any]) -> str:
    if verdict["passed"]:
        return (
            f"[BINDING-TRACE] PASS trace={verdict['trace_id']} "
            f"records={verdict['record_count']} accepted={verdict['accepted_count']} "
            f"rejected={verdict['rejected_count']} final_state={verdict['final_state']} "
            f"final_binding_generation={verdict['final_binding_generation']}"
        )
    first = verdict["first_failure"]
    return (
        f"[BINDING-TRACE] FAIL first_reason={first['reason']} "
        f"line={first['line']} sequence={first['sequence']} detail={first['detail']} "
        f"reasons={','.join(verdict['reasons'])}"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", help="path to the binding trace JSONL file")
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT),
                        help=f"path to the contract manifest (default: {DEFAULT_CONTRACT})")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="print the machine-readable JSON verdict instead of the human line")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    contract_path = Path(args.contract)
    trace_path = Path(args.trace)
    try:
        contract = load_contract(contract_path)
    except (OSError, ValueError) as exc:
        print(f"[BINDING-TRACE] FAIL first_reason=trace.io detail=unreadable contract: {exc}",
              file=sys.stderr)
        return 2
    try:
        raw = trace_path.read_bytes()
    except OSError as exc:
        print(f"[BINDING-TRACE] FAIL first_reason=trace.io detail=unreadable trace: {exc}",
              file=sys.stderr)
        return 2

    verdict = verify_trace_bytes(raw, contract)
    if args.json_output:
        print(json.dumps(verdict, ensure_ascii=False, sort_keys=True))
    else:
        print(format_human_verdict(verdict))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
