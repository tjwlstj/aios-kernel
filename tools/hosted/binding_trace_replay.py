#!/usr/bin/env python3
"""H1 strict JSONL loader and lifecycle replay verifier for binding traces v1.

This tool owns transport phases 1-4, the bounded H1-b semantic phase 5, and the
H1-c fixture-manifest/artifact-parity lane described in
docs/os/h1_binding_trace_replay_workplan_ko.md. Semantic replay is intentionally
bounded to the trusted Node 101 / Cell 1 projection in the checked-in contract;
it does not claim a live native lifecycle producer or Linux identity mapping.

Stdlib-only (Python 3.11). Exit codes: 0 replay PASS, 1 replay FAIL,
2 usage or unreadable input error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = REPO_ROOT / "hosted" / "contracts" / "binding-trace-v1.contract.json"
DEFAULT_FIXTURE_MANIFEST = REPO_ROOT / "hosted" / "contracts" / "fixtures" / "manifest.json"
VERIFIER_RELATIVE_PATH = "tools/hosted/binding_trace_replay.py"

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
R_TRACE_ID = "trace.trace-id"
R_EVENT = "trace.event"
R_OUTCOME = "trace.outcome"
R_TERMINAL = "trace.terminal"
R_HOST_INSTANCE = "trace.host-instance"
R_PRODUCER_INSTANCE = "trace.producer-instance"
R_SOURCE_REUSE = "trace.source-reuse"
R_STATE_TRANSITION = "trace.state-transition"
R_FIXTURE_MISMATCH = "trace.fixture-mismatch"

PARITY_LEFT_RUNNER_OS = "Linux"
PARITY_RIGHT_RUNNER_OS = "Windows"

PHASE_RAW = 1
PHASE_JSON = 2
PHASE_SHAPE = 3
PHASE_ENVELOPE = 4
PHASE_SEMANTIC = 5

U64_MAX = (1 << 64) - 1
U32_MAX = (1 << 32) - 1
UTF8_BOM = b"\xef\xbb\xbf"

TRACE_REASON_REGISTRY = (
    R_IO,
    R_ENCODING,
    R_SYNTAX,
    R_DUP_KEY,
    R_MISSING_FIELD,
    R_UNKNOWN_FIELD,
    R_TYPE,
    R_RANGE,
    R_LIMIT,
    R_TRUNCATED,
    R_SEQUENCE,
    R_TRACE_ID,
    R_EVENT,
    R_OUTCOME,
    R_TERMINAL,
    R_HOST_INSTANCE,
    R_PRODUCER_INSTANCE,
    R_SOURCE_REUSE,
    R_STATE_TRANSITION,
    R_FIXTURE_MISMATCH,
)

NATIVE_REASON_ORDER = (
    "none",
    "init-order",
    "missing",
    "schema",
    "malformed",
    "overflow",
    "duplicate",
    "orphan",
    "namespace",
    "kind",
    "role",
    "instance",
    "zero-generation",
    "generation-rollback",
    "stale",
    "tail",
)
NATIVE_VALIDATION_PRECEDENCE = (
    "init-order",
    "missing",
    "malformed",
    "schema",
    "overflow",
    "zero-generation",
    "tail",
    "namespace",
    "orphan",
    "kind",
    "role",
    "instance",
    "stale",
    "generation-rollback",
    "duplicate",
)
NATIVE_REASON_RANK = {
    reason: index for index, reason in enumerate(NATIVE_VALIDATION_PRECEDENCE)
}


class Failure:
    __slots__ = (
        "phase",
        "reason",
        "line",
        "sequence",
        "detail",
        "computed_outcome",
        "computed_reason",
        "claimed_outcome",
        "claimed_reason",
    )

    def __init__(
        self,
        phase: int,
        reason: str,
        line: int | None,
        sequence: int | None,
        detail: str,
        *,
        computed_outcome: str | None = None,
        computed_reason: str | None = None,
        claimed_outcome: str | None = None,
        claimed_reason: str | None = None,
    ):
        self.phase = phase
        self.reason = reason
        self.line = line
        self.sequence = sequence
        self.detail = detail
        self.computed_outcome = computed_outcome
        self.computed_reason = computed_reason
        self.claimed_outcome = claimed_outcome
        self.claimed_reason = claimed_reason

    def as_dict(self) -> dict[str, Any]:
        result = {
            "reason": self.reason,
            "line": self.line,
            "sequence": self.sequence,
            "detail": self.detail,
        }
        if self.computed_outcome is not None:
            result.update(
                {
                    "computed_outcome": self.computed_outcome,
                    "computed_reason": self.computed_reason,
                    "claimed_outcome": self.claimed_outcome,
                    "claimed_reason": self.claimed_reason,
                }
            )
        return result


def _object_without_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_nonfinite_json(token: str) -> None:
    raise ValueError(f"non-finite JSON token is forbidden: {token}")


def _decode_strict_json(text: str) -> Any:
    """Keep all external JSON decoding failures inside the verdict boundary."""
    try:
        return json.loads(
            text,
            object_pairs_hook=_object_without_duplicate_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except RecursionError as exc:
        # A bounded byte stream can still exceed the decoder's nesting limit.
        # Preserve one stable reason across trace, contract, and bundle inputs.
        raise ValueError("JSON nesting exceeds decoder limit") from exc


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _require_exact_keys(value: dict[str, Any], names: list[str], label: str) -> None:
    expected = set(names)
    actual = set(value)
    if actual != expected:
        missing = [name for name in names if name not in actual]
        unknown = sorted(actual - expected)
        parts = []
        if missing:
            parts.append("missing=" + ",".join(missing))
        if unknown:
            parts.append("unknown=" + ",".join(unknown))
        raise ValueError(f"{label} exact fields violated ({'; '.join(parts)})")


def _json_type_strict_equal(actual: Any, expected: Any) -> bool:
    """Compare decoded JSON without Python's bool/int/float equality coercion."""
    if type(actual) is not type(expected):
        return False
    if type(actual) is dict:
        if actual.keys() != expected.keys():
            return False
        return all(
            _json_type_strict_equal(actual[key], expected[key])
            for key in actual
        )
    if type(actual) is list:
        return len(actual) == len(expected) and all(
            _json_type_strict_equal(left, right)
            for left, right in zip(actual, expected)
        )
    return actual == expected


def validate_contract(contract: dict[str, Any]) -> None:
    required = (
        "contract_id",
        "schema_version",
        "transport",
        "scalar_types",
        "enums",
        "constants",
        "records",
        "trusted_target_v1",
        "string_mappings_v1",
        "outcome_pairing",
        "trace_reasons_v1",
        "fixture_manifest_v1",
    )
    for name in required:
        if name not in contract:
            raise ValueError(f"contract missing required member: {name}")
    if (
        type(contract["contract_id"]) is not str
        or contract["contract_id"] != "aios-binding-trace-v1"
        or type(contract["schema_version"]) is not int
        or contract["schema_version"] != 1
    ):
        raise ValueError("unsupported contract_id or schema_version")

    transport = _require_mapping(contract["transport"], "transport")
    for name in (
        "max_total_bytes",
        "max_line_bytes",
        "max_records",
        "allowed_line_endings",
        "bom_allowed",
        "blank_lines_allowed",
    ):
        if name not in transport:
            raise ValueError(f"transport missing required member: {name}")
    for name in ("max_total_bytes", "max_line_bytes", "max_records"):
        if type(transport[name]) is not int or transport[name] <= 0:
            raise ValueError(f"transport.{name} must be a positive integer")
    if type(transport["max_records"]) is not int or not 1 <= transport["max_records"] <= 64:
        raise ValueError("transport.max_records must be an integer in [1, 64]")
    if transport["max_line_bytes"] > transport["max_total_bytes"]:
        raise ValueError("transport.max_line_bytes cannot exceed max_total_bytes")
    if transport["allowed_line_endings"] != ["lf", "crlf"]:
        raise ValueError("transport.allowed_line_endings must remain ['lf', 'crlf']")
    if type(transport["bom_allowed"]) is not bool or type(
        transport["blank_lines_allowed"]
    ) is not bool:
        raise ValueError("transport BOM and blank-line switches must be booleans")

    scalar_types = _require_mapping(contract["scalar_types"], "scalar_types")
    for name in ("u32", "flag", "u64-decimal", "trace-id", "kebab-token"):
        spec = _require_mapping(scalar_types.get(name), f"scalar_types.{name}")
        pattern = spec.get("pattern")
        if pattern is not None:
            if type(pattern) is not str:
                raise ValueError(f"scalar_types.{name}.pattern must be a string")
            re.compile(pattern)
    if scalar_types["u32"].get("json_type") != "integer":
        raise ValueError("scalar_types.u32.json_type must be integer")
    if scalar_types["flag"].get("json_type") != "integer":
        raise ValueError("scalar_types.flag.json_type must be integer")
    if scalar_types["u64-decimal"].get("json_type") != "string":
        raise ValueError("scalar_types.u64-decimal.json_type must be string")
    for name in ("trace-id", "kebab-token"):
        if scalar_types[name].get("json_type") != "string":
            raise ValueError(f"scalar_types.{name}.json_type must be string")
    numeric_bounds = (
        ("scalar_types.u32.min_value", scalar_types["u32"].get("min_value"), 0),
        ("scalar_types.u32.max_value", scalar_types["u32"].get("max_value"), U32_MAX),
        ("scalar_types.flag.min_value", scalar_types["flag"].get("min_value"), 0),
        ("scalar_types.flag.max_value", scalar_types["flag"].get("max_value"), 1),
    )
    for label, actual, expected in numeric_bounds:
        if type(actual) is not int or actual != expected:
            raise ValueError(f"{label} does not match v1")
    max_u64_string = scalar_types["u64-decimal"].get("max_value_string")
    if type(max_u64_string) is not str or max_u64_string != str(U64_MAX):
        raise ValueError("scalar_types.u64-decimal.max_value_string does not match v1")

    enums = _require_mapping(contract["enums"], "enums")
    for name in (
        "record_type",
        "event",
        "claimed_outcome",
        "claimed_reason",
        "lifecycle_state",
        "final_state",
    ):
        values = enums.get(name)
        if not isinstance(values, list) or not values or not all(type(item) is str for item in values):
            raise ValueError(f"enums.{name} must be a non-empty string array")
        if len(values) != len(set(values)):
            raise ValueError(f"enums.{name} contains duplicates")
    if enums["claimed_reason"] != list(NATIVE_REASON_ORDER):
        raise ValueError("enums.claimed_reason does not match the append-only native registry")

    constants = _require_mapping(contract["constants"], "constants")
    if type(constants.get("schema_version")) is not int or constants.get("schema_version") != 1:
        raise ValueError("constants.schema_version must equal 1")
    for name in ("observation_only", "management_only"):
        if type(constants.get(name)) is not int or constants.get(name) != 1:
            raise ValueError(f"constants.{name} must equal integer 1")

    records = _require_mapping(contract["records"], "records")
    for record_type in ("event", "terminal"):
        schema = _require_mapping(records.get(record_type), f"records.{record_type}")
        fields = schema.get("fields_in_order")
        if not isinstance(fields, list) or not fields:
            raise ValueError(f"records.{record_type}.fields_in_order must be non-empty")
        names: list[str] = []
        for index, entry in enumerate(fields):
            entry = _require_mapping(entry, f"records.{record_type}.fields_in_order[{index}]")
            _require_exact_keys(entry, ["name", "type"], f"records.{record_type} field {index}")
            if type(entry["name"]) is not str or type(entry["type"]) is not str:
                raise ValueError(f"records.{record_type} field names and types must be strings")
            if entry["type"].startswith("enum:"):
                enum_name = entry["type"].split(":", 1)[1]
                if enum_name not in enums:
                    raise ValueError(
                        f"records.{record_type} field {entry['name']} references unknown enum"
                    )
            elif entry["type"] not in scalar_types:
                raise ValueError(
                    f"records.{record_type} field {entry['name']} references unknown scalar type"
                )
            names.append(entry["name"])
        if len(names) != len(set(names)):
            raise ValueError(f"records.{record_type} contains duplicate field names")

    target = _require_mapping(contract["trusted_target_v1"], "trusted_target_v1")
    target_fields = (
        "canonical_namespace",
        "canonical_id",
        "canonical_kind",
        "canonical_generation",
        "parent_cell_id",
        "parent_generation",
        "source_namespace",
        "source_kind",
        "source_role",
        "native_source_id",
        "native_source_instance",
        "native_binding_generation",
        "initial_source_generation",
    )
    _require_exact_keys(target, ["note", *target_fields], "trusted_target_v1")
    if type(target["note"]) is not str or not target["note"]:
        raise ValueError("trusted_target_v1.note must be a non-empty string")
    for name in target_fields:
        if type(target.get(name)) is not str:
            raise ValueError(f"trusted_target_v1.{name} must be a string")
    u64_pattern = re.compile(scalar_types["u64-decimal"]["pattern"])
    for name in (
        "canonical_id",
        "canonical_generation",
        "parent_cell_id",
        "parent_generation",
        "native_source_id",
        "native_source_instance",
        "native_binding_generation",
        "initial_source_generation",
    ):
        value = target[name]
        if not u64_pattern.fullmatch(value) or int(value) == 0 or int(value) > U64_MAX:
            raise ValueError(f"trusted_target_v1.{name} must be a non-zero u64 decimal")

    mappings = _require_mapping(contract["string_mappings_v1"], "string_mappings_v1")
    expected_mappings = {
        "canonical_namespace": {"node": 2},
        "source_namespace": {"native-slm-agent-tree": 1},
        "canonical_kind": {"ai-service": 1},
        "source_kind": {"ai-service": 1},
        "source_role": {"main": 1},
        "lifecycle_state": {"active": 1},
    }
    _require_exact_keys(
        mappings,
        ["note", *expected_mappings],
        "string_mappings_v1",
    )
    if type(mappings["note"]) is not str or not mappings["note"]:
        raise ValueError("string_mappings_v1.note must be a non-empty string")
    for name, expected in expected_mappings.items():
        mapping = _require_mapping(mappings[name], f"string_mappings_v1.{name}")
        if not _json_type_strict_equal(mapping, expected):
            raise ValueError(f"string_mappings_v1.{name} does not match v1 numeric meanings")

    pairing = _require_mapping(contract["outcome_pairing"], "outcome_pairing")
    _require_exact_keys(pairing, ["accepted", "rejected"], "outcome_pairing")
    if pairing["accepted"] != ["none"]:
        raise ValueError("outcome_pairing.accepted must equal ['none']")
    if pairing["rejected"] != enums["claimed_reason"][1:]:
        raise ValueError("outcome_pairing.rejected must match claimed_reason without none")

    trace_reasons = contract["trace_reasons_v1"]
    if not isinstance(trace_reasons, list) or not all(type(item) is str for item in trace_reasons):
        raise ValueError("trace_reasons_v1 must be a string array")
    if tuple(trace_reasons) != TRACE_REASON_REGISTRY:
        raise ValueError("trace_reasons_v1 does not match the verifier append-only registry")

    manifest_spec = _require_mapping(contract["fixture_manifest_v1"], "fixture_manifest_v1")
    for name in (
        "top_level_fields_in_order",
        "fixture_fields_in_order",
        "max_fixtures",
        "fixture_id_pattern",
        "evidence_kind",
        "expected_outcome",
        "trace_suffix",
    ):
        if name not in manifest_spec:
            raise ValueError(f"fixture_manifest_v1 missing required member: {name}")
    expected_top_fields = ["schema_version", "manifest_id", "contract_id", "fixtures"]
    expected_fixture_fields = [
        "id",
        "trace",
        "evidence_kind",
        "expected_outcome",
        "expected_first_reason",
    ]
    if manifest_spec["top_level_fields_in_order"] != expected_top_fields:
        raise ValueError("fixture manifest top-level field order does not match v1")
    if manifest_spec["fixture_fields_in_order"] != expected_fixture_fields:
        raise ValueError("fixture manifest case field order does not match v1")
    if (
        type(manifest_spec["max_fixtures"]) is not int
        or not 1 <= manifest_spec["max_fixtures"] <= 64
    ):
        raise ValueError("fixture_manifest_v1.max_fixtures must be in [1, 64]")
    if type(manifest_spec["fixture_id_pattern"]) is not str:
        raise ValueError("fixture_manifest_v1.fixture_id_pattern must be a string")
    re.compile(manifest_spec["fixture_id_pattern"])
    if manifest_spec["expected_outcome"] != ["PASS", "FAIL"]:
        raise ValueError("fixture_manifest_v1.expected_outcome must equal ['PASS', 'FAIL']")
    if not isinstance(manifest_spec["evidence_kind"], list) or not all(
        type(item) is str for item in manifest_spec["evidence_kind"]
    ):
        raise ValueError("fixture_manifest_v1.evidence_kind must be a string array")
    if manifest_spec["trace_suffix"] != ".jsonl":
        raise ValueError("fixture_manifest_v1.trace_suffix must equal .jsonl")


def load_contract(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    contract = _decode_strict_json(text)
    validate_contract(_require_mapping(contract, "contract"))
    if path.resolve() != DEFAULT_CONTRACT.resolve():
        try:
            canonical_text = DEFAULT_CONTRACT.read_text(encoding="utf-8")
            canonical = _decode_strict_json(canonical_text)
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(f"cannot load the checked-in canonical v1 contract: {exc}") from exc
        if not _json_type_strict_equal(contract, canonical):
            raise ValueError(
                "alternate aios-binding-trace-v1 contract must be semantically identical "
                "to the checked-in canonical contract"
            )
    return contract


def _compiled(contract: dict[str, Any]) -> tuple[dict[str, Any], dict[str, re.Pattern[str]]]:
    types = contract["scalar_types"]
    patterns = {
        name: re.compile(spec["pattern"])
        for name, spec in types.items()
        if "pattern" in spec
    }
    return types, patterns


def _phase_raw(raw: bytes, contract: dict[str, Any]) -> tuple[list[str], list[Failure]]:
    """Apply raw io -> limit(total/records/line) -> encoding -> truncated checks."""
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

    trailing_newline = raw.endswith(b"\n")
    line_bytes_list = raw.split(b"\n")
    if trailing_newline:
        line_bytes_list = line_bytes_list[:-1]

    contents: list[bytes] = []
    line_ending_failures: list[Failure] = []
    allowed_line_endings = set(transport["allowed_line_endings"])
    for zero_based_index, line in enumerate(line_bytes_list):
        index = zero_based_index + 1
        has_lf_terminator = trailing_newline or zero_based_index < len(line_bytes_list) - 1
        if has_lf_terminator and line.endswith(b"\r"):
            if "crlf" not in allowed_line_endings:
                line_ending_failures.append(
                    Failure(
                        PHASE_RAW,
                        R_ENCODING,
                        index,
                        None,
                        f"line {index} uses a CRLF terminator that is not allowed",
                    )
                )
            line = line[:-1]
        elif has_lf_terminator and "lf" not in allowed_line_endings:
            line_ending_failures.append(
                Failure(
                    PHASE_RAW,
                    R_ENCODING,
                    index,
                    None,
                    f"line {index} uses an LF terminator that is not allowed",
                )
            )
        if b"\r" in line:
            line_ending_failures.append(
                Failure(
                    PHASE_RAW,
                    R_ENCODING,
                    index,
                    None,
                    f"line {index} contains a bare CR outside its line terminator",
                )
            )
        contents.append(line)

    max_records = int(transport["max_records"])
    if len(contents) > max_records:
        failures.append(
            Failure(
                PHASE_RAW,
                R_LIMIT,
                max_records + 1,
                None,
                f"record count {len(contents)} exceeds {max_records}",
            )
        )

    max_line = int(transport["max_line_bytes"])
    for index, line in enumerate(contents, start=1):
        if len(line) > max_line:
            failures.append(
                Failure(PHASE_RAW, R_LIMIT, index, None, f"line {index} has {len(line)} bytes, limit {max_line}")
            )
    if failures:
        return [], failures

    failures.extend(line_ending_failures)

    decoded_lines: list[str] = []
    for index, line in enumerate(contents, start=1):
        if not bool(transport["bom_allowed"]) and line.startswith(UTF8_BOM):
            failures.append(
                Failure(
                    PHASE_RAW,
                    R_ENCODING,
                    index,
                    None,
                    f"line {index} starts with a UTF-8 BOM; BOM is not allowed",
                )
            )
            decoded_lines.append("")
            continue
        try:
            decoded_lines.append(line.decode("utf-8", errors="strict"))
        except UnicodeDecodeError:
            failures.append(Failure(PHASE_RAW, R_ENCODING, index, None, f"line {index} is not valid UTF-8"))
            decoded_lines.append("")

    if not trailing_newline:
        failures.append(
            Failure(PHASE_RAW, R_TRUNCATED, len(contents), None, "final newline missing")
        )

    return decoded_lines, failures


def _phase_json(lines: list[str], *, blank_lines_allowed: bool) -> tuple[list[Any], list[Failure]]:
    records: list[Any] = []
    failures: list[Failure] = []
    for index, text in enumerate(lines, start=1):
        record: Any = None
        if not blank_lines_allowed and text.strip() == "":
            failures.append(Failure(PHASE_JSON, R_SYNTAX, index, None, "blank line"))
            records.append(None)
            continue
        try:
            record = _decode_strict_json(text)
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
    expected_trace_id: str | None = None

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

        trace_id = record.get("trace_id")
        if isinstance(trace_id, str):
            if expected_trace_id is None:
                expected_trace_id = trace_id
            elif trace_id != expected_trace_id:
                failures.append(
                    Failure(
                        PHASE_ENVELOPE,
                        R_TRACE_ID,
                        row_index,
                        sequence,
                        f"trace_id {trace_id!r} must remain {expected_trace_id!r}",
                    )
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


class _ReplayState(NamedTuple):
    trace_id: str
    host_instance: int
    producer_instance: int
    source_id: int
    source_instance: int
    source_generation: int
    bound_source_instance: int
    bound_source_generation: int
    binding_generation: int
    lifecycle_state: str
    replay_state: str
    seen_source_instances: frozenset[int]


class _Transition(NamedTuple):
    computed_outcome: str
    computed_reason: str
    candidate: _ReplayState | None = None
    issue_reason: str | None = None
    issue_detail: str | None = None
    expected_rejection: bool = False


def _u64(record: dict[str, Any], name: str) -> int:
    return int(record[name])


def _semantic_failure(
    record: dict[str, Any],
    line: int,
    reason: str,
    detail: str,
    *,
    computed_outcome: str,
    computed_reason: str,
) -> Failure:
    return Failure(
        PHASE_SEMANTIC,
        reason,
        line,
        _sequence_of(record),
        detail,
        computed_outcome=computed_outcome,
        computed_reason=computed_reason,
        claimed_outcome=record.get("claimed_outcome"),
        claimed_reason=record.get("claimed_reason"),
    )


def _native_reason_rank(reason: str) -> int:
    return NATIVE_REASON_RANK.get(reason, len(NATIVE_VALIDATION_PRECEDENCE))


def _native_record_issue(
    record: dict[str, Any],
    state: _ReplayState | None,
    contract: dict[str, Any],
) -> tuple[str, str] | None:
    """Return the first H1-applicable native semantic issue for one event."""
    target = contract["trusted_target_v1"]

    if _u64(record, "source_id") == 0:
        return "missing", "source_id must be non-zero"

    if record["producer_owned"] != 1 or record["copied_read"] != 1:
        return "malformed", "producer_owned and copied_read must both equal 1"

    generation_fields = (
        "canonical_generation",
        "parent_generation",
        "source_generation",
    )
    for name in generation_fields:
        if _u64(record, name) == 0:
            return "zero-generation", f"{name} must be non-zero"

    if record["binding_valid"] == 1:
        if _u64(record, "bound_source_generation") == 0:
            return "zero-generation", "valid binding requires non-zero bound_source_generation"
        if _u64(record, "binding_generation") == 0:
            return "zero-generation", "valid binding requires non-zero binding_generation"

    for name in ("host_instance", "producer_instance", "source_instance"):
        if _u64(record, name) == 0:
            return "instance", f"{name} must be non-zero"
    if record["binding_valid"] == 1 and _u64(record, "bound_source_instance") == 0:
        return "instance", "valid binding requires non-zero bound_source_instance"

    required_valid_flags = (
        "canonical_valid",
        "parent_valid",
        "source_valid",
        "generation_valid",
        "lifecycle_valid",
    )
    for name in required_valid_flags:
        if record[name] != 1:
            return "malformed", f"{name} must equal 1"

    if record["observed_at_valid"] == 0 and _u64(record, "observed_at_ns") != 0:
        return "malformed", "observed_at_valid=0 requires observed_at_ns=0"
    if record["observed_at_valid"] == 1 and _u64(record, "observed_at_ns") == 0:
        return "malformed", "observed_at_valid=1 requires non-zero observed_at_ns"

    if record["binding_valid"] == 0:
        if (
            _u64(record, "bound_source_instance") != 0
            or _u64(record, "bound_source_generation") != 0
            or _u64(record, "binding_generation") != 0
            or record["binding_current"] != 0
        ):
            return "malformed", "invalid binding requires the exact zero sentinel projection"
    elif record["binding_current"] == 1 and record["lifecycle_state"] != "active":
        return "malformed", "current binding requires active lifecycle"

    if (
        record["canonical_namespace"] != target["canonical_namespace"]
        or record["source_namespace"] != target["source_namespace"]
    ):
        return "namespace", "canonical/source namespace does not match trusted_target_v1"

    if (
        record["canonical_id"] != target["canonical_id"]
        or record["parent_cell_id"] != target["parent_cell_id"]
    ):
        return "orphan", "canonical Node or parent Cell does not match trusted_target_v1"

    if (
        record["canonical_kind"] != target["canonical_kind"]
        or record["source_kind"] != target["source_kind"]
        or record["kind_match"] != 1
    ):
        return "kind", "canonical/source kind projection is inconsistent"

    if record["source_role"] != target["source_role"] or record["role_match"] != 1:
        return "role", "source role projection is inconsistent"

    if record["source_id"] != target["native_source_id"]:
        return "orphan", "source_id does not match the bounded native source target"

    if state is not None and _u64(record, "source_id") != state.source_id:
        return "orphan", "source_id changed inside one trace"

    canonical_generation = _u64(record, "canonical_generation")
    trusted_canonical_generation = int(target["canonical_generation"])
    parent_generation = _u64(record, "parent_generation")
    trusted_parent_generation = int(target["parent_generation"])
    if canonical_generation < trusted_canonical_generation:
        return "stale", "canonical_generation is older than trusted_target_v1"
    if parent_generation < trusted_parent_generation:
        return "stale", "parent_generation is older than trusted_target_v1"
    if canonical_generation > trusted_canonical_generation:
        return "generation-rollback", "canonical_generation is ahead of trusted_target_v1"
    if parent_generation > trusted_parent_generation:
        return "generation-rollback", "parent_generation is ahead of trusted_target_v1"

    return None


def _current_source_issue(
    record: dict[str, Any],
    state: _ReplayState,
    *,
    generation_mode: str,
) -> tuple[str, str] | None:
    source_instance = _u64(record, "source_instance")
    source_generation = _u64(record, "source_generation")
    if source_instance != state.source_instance:
        return "instance", (
            f"source_instance {source_instance} does not match current "
            f"instance {state.source_instance}"
        )

    if generation_mode == "exact":
        if source_generation < state.source_generation:
            return "stale", (
                f"source_generation {source_generation} is older than current "
                f"generation {state.source_generation}"
            )
        if source_generation > state.source_generation:
            return "generation-rollback", (
                f"source_generation {source_generation} is ahead of replayed "
                f"generation {state.source_generation}"
            )
    elif generation_mode == "increase" and source_generation <= state.source_generation:
        return "generation-rollback", (
            f"source_generation {source_generation} must be greater than "
            f"{state.source_generation} for this event"
        )
    else:
        if generation_mode not in {"exact", "increase"}:
            raise ValueError(f"unsupported generation mode: {generation_mode}")
    return None


def _binding_projection_issue(
    record: dict[str, Any],
    *,
    expected_instance: int,
    expected_source_generation: int,
    prior_binding_generation: int,
    generation_mode: str,
    binding_valid: int,
    binding_current: int,
) -> tuple[str, str] | None:
    if record["binding_valid"] != binding_valid or record["binding_current"] != binding_current:
        return "malformed", (
            "binding_valid/binding_current projection does not match the replayed state"
        )

    actual_instance = _u64(record, "bound_source_instance")
    actual_source_generation = _u64(record, "bound_source_generation")
    actual_binding_generation = _u64(record, "binding_generation")

    if actual_instance != expected_instance:
        return "instance", (
            f"bound_source_instance {actual_instance} does not match expected "
            f"instance {expected_instance}"
        )
    if actual_source_generation < expected_source_generation:
        return "stale", (
            f"bound_source_generation {actual_source_generation} is older than expected "
            f"generation {expected_source_generation}"
        )
    if actual_source_generation > expected_source_generation:
        return "generation-rollback", (
            f"bound_source_generation {actual_source_generation} is ahead of expected "
            f"generation {expected_source_generation}"
        )

    if generation_mode == "exact":
        if actual_binding_generation < prior_binding_generation:
            return "generation-rollback", (
                f"binding_generation {actual_binding_generation} rolled back from "
                f"{prior_binding_generation}"
            )
        if actual_binding_generation > prior_binding_generation:
            return R_STATE_TRANSITION, (
                f"binding_generation {actual_binding_generation} advanced without a "
                "bind, rebind, or exit transition"
            )
    elif generation_mode == "increase":
        if actual_binding_generation <= prior_binding_generation:
            return "generation-rollback", (
                f"binding_generation {actual_binding_generation} must be greater than "
                f"{prior_binding_generation} for this event"
            )
    else:
        raise ValueError(f"unsupported binding generation mode: {generation_mode}")
    return None


def _snapshot_issue(record: dict[str, Any], state: _ReplayState) -> tuple[str, str] | None:
    issue = _current_source_issue(record, state, generation_mode="exact")
    if issue is not None:
        return issue
    if record["lifecycle_state"] != state.lifecycle_state:
        return "malformed", (
            f"lifecycle_state {record['lifecycle_state']!r} does not match replayed "
            f"state {state.lifecycle_state!r}"
        )

    has_binding = state.bound_source_instance != 0
    return _binding_projection_issue(
        record,
        expected_instance=state.bound_source_instance,
        expected_source_generation=state.bound_source_generation,
        prior_binding_generation=state.binding_generation,
        generation_mode="exact",
        binding_valid=1 if has_binding else 0,
        binding_current=1 if state.replay_state == "bound" else 0,
    )


def _transition_for_event(
    record: dict[str, Any],
    state: _ReplayState | None,
    contract: dict[str, Any],
) -> _Transition:
    event = record["event"]

    if state is None:
        if event != "discover":
            return _Transition(
                "rejected",
                "init-order",
                issue_reason="init-order",
                issue_detail="the first event must be discover",
            )
        if record["lifecycle_state"] != "active":
            return _Transition(
                "rejected",
                "malformed",
                issue_reason="malformed",
                issue_detail="initial discover requires active lifecycle",
            )
        if _u64(record, "source_generation") != int(
            contract["trusted_target_v1"]["initial_source_generation"]
        ):
            return _Transition(
                "rejected",
                R_STATE_TRANSITION,
                issue_reason=R_STATE_TRANSITION,
                issue_detail="initial discover must start at source generation 1",
            )
        if record["binding_valid"] != 0 or record["binding_current"] != 0:
            return _Transition(
                "rejected",
                "init-order",
                issue_reason="init-order",
                issue_detail="initial discover cannot carry a pre-existing binding",
            )
        source_instance = _u64(record, "source_instance")
        candidate = _ReplayState(
            trace_id=record["trace_id"],
            host_instance=_u64(record, "host_instance"),
            producer_instance=_u64(record, "producer_instance"),
            source_id=_u64(record, "source_id"),
            source_instance=source_instance,
            source_generation=_u64(record, "source_generation"),
            bound_source_instance=0,
            bound_source_generation=0,
            binding_generation=0,
            lifecycle_state="active",
            replay_state="discovered",
            seen_source_instances=frozenset({source_instance}),
        )
        return _Transition("accepted", "none", candidate=candidate)

    if event == "discover" and state.replay_state != "exited":
        issue = _snapshot_issue(record, state)
        if issue is not None:
            return _Transition(
                "rejected", issue[0], issue_reason=issue[0], issue_detail=issue[1]
            )
        return _Transition(
            "rejected",
            "duplicate",
            issue_reason="duplicate",
            issue_detail="discover duplicated an active source lifetime",
        )

    if state.replay_state == "bound":
        if event == "observe":
            issue = _snapshot_issue(record, state)
            if issue is not None:
                return _Transition(
                    "rejected", issue[0], issue_reason=issue[0], issue_detail=issue[1]
                )
            return _Transition("accepted", "none", candidate=state)

        if event == "update":
            issue = _current_source_issue(record, state, generation_mode="increase")
            if issue is not None:
                return _Transition(
                    "rejected", issue[0], issue_reason=issue[0], issue_detail=issue[1]
                )
            if record["lifecycle_state"] != "active":
                return _Transition(
                    "rejected",
                    "malformed",
                    issue_reason="malformed",
                    issue_detail="update requires active lifecycle",
                )
            issue = _binding_projection_issue(
                record,
                expected_instance=state.bound_source_instance,
                expected_source_generation=state.bound_source_generation,
                prior_binding_generation=state.binding_generation,
                generation_mode="exact",
                binding_valid=1,
                binding_current=0,
            )
            if issue is not None:
                return _Transition(
                    "rejected", issue[0], issue_reason=issue[0], issue_detail=issue[1]
                )
            candidate = state._replace(
                source_generation=_u64(record, "source_generation"),
                replay_state="discovered",
            )
            return _Transition("accepted", "none", candidate=candidate)

        if event == "exit":
            issue = _current_source_issue(record, state, generation_mode="increase")
            if issue is not None:
                return _Transition(
                    "rejected", issue[0], issue_reason=issue[0], issue_detail=issue[1]
                )
            if record["lifecycle_state"] != "exited":
                return _Transition(
                    "rejected",
                    "malformed",
                    issue_reason="malformed",
                    issue_detail="exit requires exited lifecycle projection",
                )
            issue = _binding_projection_issue(
                record,
                expected_instance=state.bound_source_instance,
                expected_source_generation=state.bound_source_generation,
                prior_binding_generation=state.binding_generation,
                generation_mode="increase",
                binding_valid=1,
                binding_current=0,
            )
            if issue is not None:
                return _Transition(
                    "rejected", issue[0], issue_reason=issue[0], issue_detail=issue[1]
                )
            candidate = state._replace(
                source_generation=_u64(record, "source_generation"),
                binding_generation=_u64(record, "binding_generation"),
                lifecycle_state="exited",
                replay_state="exited",
            )
            return _Transition("accepted", "none", candidate=candidate)

        if event in {"bind", "rebind"}:
            issue = _snapshot_issue(record, state)
            if issue is not None:
                return _Transition(
                    "rejected", issue[0], issue_reason=issue[0], issue_detail=issue[1]
                )
            return _Transition(
                "rejected",
                "duplicate",
                issue_reason="duplicate",
                issue_detail="a current exact-one binding already exists",
            )

    if state.replay_state == "discovered":
        has_retained_binding = state.bound_source_instance != 0

        if event == "bind" and not has_retained_binding:
            issue = _current_source_issue(record, state, generation_mode="exact")
            if issue is not None:
                return _Transition(
                    "rejected", issue[0], issue_reason=issue[0], issue_detail=issue[1]
                )
            if record["lifecycle_state"] != "active":
                return _Transition(
                    "rejected",
                    "malformed",
                    issue_reason="malformed",
                    issue_detail="bind requires active lifecycle",
                )
            issue = _binding_projection_issue(
                record,
                expected_instance=state.source_instance,
                expected_source_generation=state.source_generation,
                prior_binding_generation=state.binding_generation,
                generation_mode="increase",
                binding_valid=1,
                binding_current=1,
            )
            if issue is not None:
                return _Transition(
                    "rejected", issue[0], issue_reason=issue[0], issue_detail=issue[1]
                )
            candidate = state._replace(
                bound_source_instance=state.source_instance,
                bound_source_generation=state.source_generation,
                binding_generation=_u64(record, "binding_generation"),
                replay_state="bound",
            )
            return _Transition("accepted", "none", candidate=candidate)

        if event == "rebind" and has_retained_binding:
            issue = _current_source_issue(record, state, generation_mode="exact")
            if issue is not None:
                return _Transition(
                    "rejected", issue[0], issue_reason=issue[0], issue_detail=issue[1]
                )
            if record["lifecycle_state"] != "active":
                return _Transition(
                    "rejected",
                    "malformed",
                    issue_reason="malformed",
                    issue_detail="rebind requires active lifecycle",
                )
            issue = _binding_projection_issue(
                record,
                expected_instance=state.source_instance,
                expected_source_generation=state.source_generation,
                prior_binding_generation=state.binding_generation,
                generation_mode="increase",
                binding_valid=1,
                binding_current=1,
            )
            if issue is not None:
                return _Transition(
                    "rejected", issue[0], issue_reason=issue[0], issue_detail=issue[1]
                )
            candidate = state._replace(
                bound_source_instance=state.source_instance,
                bound_source_generation=state.source_generation,
                binding_generation=_u64(record, "binding_generation"),
                replay_state="bound",
            )
            return _Transition("accepted", "none", candidate=candidate)

        if event == "observe" and has_retained_binding:
            issue = _snapshot_issue(record, state)
            if issue is not None:
                return _Transition(
                    "rejected", issue[0], issue_reason=issue[0], issue_detail=issue[1]
                )
            return _Transition(
                "rejected",
                "stale",
                expected_rejection=True,
                issue_detail="retained binding is not current after update or rediscovery",
            )

        if event == "observe" and not has_retained_binding:
            return _Transition(
                "rejected",
                "missing",
                issue_reason="missing",
                issue_detail="observe requires a retained binding",
            )

        return _Transition(
            "rejected",
            R_STATE_TRANSITION,
            issue_reason=R_STATE_TRANSITION,
            issue_detail=(
                f"event {event!r} is not allowed from discovered state with "
                f"retained_binding={int(has_retained_binding)}"
            ),
        )

    if state.replay_state == "exited":
        if event in {"observe", "update", "bind"}:
            issue = _snapshot_issue(record, state)
            if issue is not None:
                return _Transition(
                    "rejected", issue[0], issue_reason=issue[0], issue_detail=issue[1]
                )
            return _Transition(
                "rejected",
                "stale",
                expected_rejection=True,
                issue_detail="event targets an exited source with a retained old binding",
            )

        if event == "discover":
            source_instance = _u64(record, "source_instance")
            if source_instance in state.seen_source_instances:
                return _Transition(
                    "rejected",
                    R_SOURCE_REUSE,
                    issue_reason=R_SOURCE_REUSE,
                    issue_detail=f"source_instance {source_instance} reuses a retired lifetime",
                )
            if _u64(record, "source_generation") != int(
                contract["trusted_target_v1"]["initial_source_generation"]
            ):
                return _Transition(
                    "rejected",
                    R_STATE_TRANSITION,
                    issue_reason=R_STATE_TRANSITION,
                    issue_detail="rediscover must start the new source instance at generation 1",
                )
            if record["lifecycle_state"] != "active":
                return _Transition(
                    "rejected",
                    "malformed",
                    issue_reason="malformed",
                    issue_detail="rediscover requires active lifecycle",
                )
            issue = _binding_projection_issue(
                record,
                expected_instance=state.bound_source_instance,
                expected_source_generation=state.bound_source_generation,
                prior_binding_generation=state.binding_generation,
                generation_mode="exact",
                binding_valid=1,
                binding_current=0,
            )
            if issue is not None:
                return _Transition(
                    "rejected", issue[0], issue_reason=issue[0], issue_detail=issue[1]
                )
            candidate = state._replace(
                source_instance=source_instance,
                source_generation=_u64(record, "source_generation"),
                lifecycle_state="active",
                replay_state="discovered",
                seen_source_instances=state.seen_source_instances | {source_instance},
            )
            return _Transition("accepted", "none", candidate=candidate)

        return _Transition(
            "rejected",
            R_STATE_TRANSITION,
            issue_reason=R_STATE_TRANSITION,
            issue_detail=f"event {event!r} is not allowed from exited state",
        )

    return _Transition(
        "rejected",
        R_STATE_TRANSITION,
        issue_reason=R_STATE_TRANSITION,
        issue_detail=f"unhandled event {event!r} from state {state.replay_state!r}",
    )


def _phase_semantic(
    records: list[Any], contract: dict[str, Any]
) -> tuple[list[Failure], dict[str, Any]]:
    """Replay lifecycle semantics after phases 1-4 are completely clean."""
    event_records = [
        (line, record)
        for line, record in enumerate(records, start=1)
        if isinstance(record, dict) and record.get("record_type") == "event"
    ]
    terminal_line, terminal = next(
        (line, record)
        for line, record in enumerate(records, start=1)
        if isinstance(record, dict) and record.get("record_type") == "terminal"
    )

    if not event_records:
        return [
            Failure(
                PHASE_SEMANTIC,
                "init-order",
                terminal_line,
                _sequence_of(terminal),
                "at least one discover event is required before terminal",
            )
        ], {}

    state: _ReplayState | None = None
    accepted_count = 0
    rejected_count = 0

    for line, record in event_records:
        if state is None and record["event"] != "discover":
            transition = _transition_for_event(record, state, contract)
            return [
                _semantic_failure(
                    record,
                    line,
                    transition.issue_reason or transition.computed_reason,
                    transition.issue_detail or "invalid initial event",
                    computed_outcome=transition.computed_outcome,
                    computed_reason=transition.computed_reason,
                )
            ], {}

        native_issue = _native_record_issue(record, state, contract)
        transition = _transition_for_event(record, state, contract)

        if native_issue is not None:
            native_candidates = [native_issue]
            if transition.expected_rejection:
                native_candidates.append(
                    (
                        transition.computed_reason,
                        transition.issue_detail or "expected semantic rejection",
                    )
                )
            elif (
                transition.issue_reason is not None
                and not transition.issue_reason.startswith("trace.")
            ):
                native_candidates.append(
                    (
                        transition.issue_reason,
                        transition.issue_detail or "native semantic rejection",
                    )
                )
            selected_reason, selected_detail = min(
                native_candidates,
                key=lambda issue: _native_reason_rank(issue[0]),
            )
            return [
                _semantic_failure(
                    record,
                    line,
                    selected_reason,
                    selected_detail,
                    computed_outcome="rejected",
                    computed_reason=selected_reason,
                )
            ], {}

        if transition.issue_reason is not None and not transition.issue_reason.startswith("trace."):
            return [
                _semantic_failure(
                    record,
                    line,
                    transition.issue_reason,
                    transition.issue_detail or "native semantic rejection",
                    computed_outcome=transition.computed_outcome,
                    computed_reason=transition.computed_reason,
                )
            ], {}

        if transition.expected_rejection and (
            record["claimed_outcome"] != "rejected"
            or record["claimed_reason"] != transition.computed_reason
        ):
            return [
                _semantic_failure(
                    record,
                    line,
                    transition.computed_reason,
                    transition.issue_detail or "claimed result does not match stale rejection",
                    computed_outcome=transition.computed_outcome,
                    computed_reason=transition.computed_reason,
                )
            ], {}

        if state is not None and _u64(record, "host_instance") != state.host_instance:
            return [
                _semantic_failure(
                    record,
                    line,
                    R_HOST_INSTANCE,
                    f"host_instance {_u64(record, 'host_instance')} must remain {state.host_instance}",
                    computed_outcome=transition.computed_outcome,
                    computed_reason=transition.computed_reason,
                )
            ], {}

        if state is not None and _u64(record, "producer_instance") != state.producer_instance:
            return [
                _semantic_failure(
                    record,
                    line,
                    R_PRODUCER_INSTANCE,
                    "producer_instance changed inside one trace",
                    computed_outcome=transition.computed_outcome,
                    computed_reason=transition.computed_reason,
                )
            ], {}

        if transition.issue_reason is not None:
            return [
                _semantic_failure(
                    record,
                    line,
                    transition.issue_reason,
                    transition.issue_detail or "invalid lifecycle transition",
                    computed_outcome=transition.computed_outcome,
                    computed_reason=transition.computed_reason,
                )
            ], {}

        if transition.expected_rejection:
            rejected_count += 1
            continue

        if record["claimed_outcome"] != "accepted" or record["claimed_reason"] != "none":
            return [
                _semantic_failure(
                    record,
                    line,
                    R_OUTCOME,
                    "producer rejection claim does not match computed accepted/none",
                    computed_outcome="accepted",
                    computed_reason="none",
                )
            ], {}

        if transition.candidate is None:
            raise AssertionError("accepted transition did not provide candidate state")
        state = transition.candidate
        accepted_count += 1

    assert state is not None

    if _u64(terminal, "host_instance") != state.host_instance:
        return [
            Failure(
                PHASE_SEMANTIC,
                R_HOST_INSTANCE,
                terminal_line,
                _sequence_of(terminal),
                "terminal host_instance does not match the replayed trace",
            )
        ], {}
    if _u64(terminal, "producer_instance") != state.producer_instance:
        return [
            Failure(
                PHASE_SEMANTIC,
                R_PRODUCER_INSTANCE,
                terminal_line,
                _sequence_of(terminal),
                "terminal producer_instance does not match the replayed trace",
            )
        ], {}

    terminal_checks = (
        ("accepted_count", terminal["accepted_count"], accepted_count),
        ("rejected_count", terminal["rejected_count"], rejected_count),
        ("final_state", terminal["final_state"], state.replay_state),
        (
            "final_binding_generation",
            terminal["final_binding_generation"],
            str(state.binding_generation),
        ),
    )
    for name, actual, expected in terminal_checks:
        if actual != expected:
            return [
                Failure(
                    PHASE_SEMANTIC,
                    R_TERMINAL,
                    terminal_line,
                    _sequence_of(terminal),
                    f"terminal {name} {actual!r} does not match replayed value {expected!r}",
                )
            ], {}

    return [], {
        "accepted_count": accepted_count,
        "rejected_count": rejected_count,
        "final_state": state.replay_state,
        "final_binding_generation": str(state.binding_generation),
        "last_sequence": _sequence_of(terminal),
    }


def verify_trace_bytes(raw: bytes, contract: dict[str, Any]) -> dict[str, Any]:
    """Verify raw trace bytes against the v1 transport and semantic contract."""
    lines, raw_failures = _phase_raw(raw, contract)
    records, json_failures = _phase_json(
        lines,
        blank_lines_allowed=bool(contract["transport"]["blank_lines_allowed"]),
    )
    shape_failures = _phase_shape(records, contract)
    envelope_failures = _phase_envelope(records, contract)

    all_failures: list[Failure] = []
    all_failures.extend(raw_failures)
    all_failures.extend(json_failures)
    all_failures.extend(shape_failures)
    all_failures.extend(envelope_failures)
    semantic_failures: list[Failure] = []
    semantic_summary: dict[str, Any] = {}
    semantic_phase_executed = not all_failures
    if semantic_phase_executed:
        semantic_failures, semantic_summary = _phase_semantic(records, contract)
        all_failures.extend(semantic_failures)
    all_failures.sort(key=lambda item: (item.phase, item.line if item.line is not None else 0))

    passed = not all_failures

    trace_id = None
    terminal_summary: dict[str, Any] = {}
    trace_id_pattern = re.compile(contract["scalar_types"]["trace-id"]["pattern"])
    for record in records:
        if isinstance(record, dict) and "trace_id" in record:
            candidate_id = record["trace_id"]
            if type(candidate_id) is str and trace_id_pattern.fullmatch(candidate_id):
                trace_id = candidate_id
            break
    last_sequence: int | None = semantic_summary.get("last_sequence") if passed else None
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
        "accepted_count": semantic_summary.get(
            "accepted_count", terminal_summary.get("accepted_count")
        ),
        "rejected_count": semantic_summary.get(
            "rejected_count", terminal_summary.get("rejected_count")
        ),
        "last_sequence": last_sequence if passed else None,
        "final_state": semantic_summary.get("final_state") if passed else None,
        "final_binding_generation": semantic_summary.get("final_binding_generation") if passed else None,
        "first_failure": first_failure.as_dict() if first_failure is not None else None,
        "reasons": reasons,
        "observation_only": contract["constants"]["observation_only"],
        "management_only": contract["constants"]["management_only"],
        "verified_phases": [
            "raw",
            "json",
            "shape",
            "envelope",
            *(["semantic"] if semantic_phase_executed else []),
        ],
        "semantic_replay": "implemented",
        "semantic_phase_executed": semantic_phase_executed,
    }


def _load_strict_json(path: Path, label: str) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ValueError(f"unreadable {label}: {exc}") from exc
    try:
        text = raw.decode("utf-8", errors="strict")
        parsed = _decode_strict_json(text)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"invalid {label}: {exc}") from exc
    return _require_mapping(parsed, label), raw


def _validate_native_k2a_projection_bytes(
    raw: bytes,
    contract: dict[str, Any],
    label: str,
) -> None:
    """Keep the native evidence label tied to the checked-in K2-a boot tuple."""
    verdict = verify_trace_bytes(raw, contract)
    if verdict["passed"] is not True:
        reason = verdict["first_failure"]["reason"] if verdict["first_failure"] else R_IO
        raise ValueError(f"{label} native-k2a-projection is not a valid replay: {reason}")

    lines, raw_failures = _phase_raw(raw, contract)
    records, json_failures = _phase_json(
        lines,
        blank_lines_allowed=bool(contract["transport"]["blank_lines_allowed"]),
    )
    if raw_failures or json_failures:
        raise ValueError(f"{label} native-k2a-projection cannot be decoded")

    events = [record for record in records if record["record_type"] == "event"]
    terminal = records[-1]
    if [record["event"] for record in events] != ["discover", "bind", "observe"]:
        raise ValueError(
            f"{label} native-k2a-projection must be discover/bind/observe"
        )

    target = contract["trusted_target_v1"]
    for record in events:
        exact_fields = {
            "source_id": target["native_source_id"],
            "source_instance": target["native_source_instance"],
            "source_generation": target["initial_source_generation"],
            "source_namespace": target["source_namespace"],
            "source_kind": target["source_kind"],
            "source_role": target["source_role"],
            "lifecycle_state": "active",
            "observed_at_ns": "0",
            "observed_at_valid": 0,
        }
        for name, expected in exact_fields.items():
            if record[name] != expected:
                raise ValueError(
                    f"{label} native-k2a-projection {name} must equal {expected!r}"
                )
    if terminal["final_binding_generation"] != target["native_binding_generation"]:
        raise ValueError(
            f"{label} native-k2a-projection final binding generation does not match K2-a"
        )


def load_fixture_manifest(path: Path, contract: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    manifest, raw = _load_strict_json(path, "fixture manifest")
    spec = contract["fixture_manifest_v1"]
    top_fields = spec["top_level_fields_in_order"]
    _require_exact_keys(manifest, top_fields, "fixture manifest")

    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1:
        raise ValueError("fixture manifest schema_version must equal 1")
    if type(manifest["manifest_id"]) is not str or not re.fullmatch(
        spec["fixture_id_pattern"], manifest["manifest_id"]
    ):
        raise ValueError("fixture manifest manifest_id must be lower-kebab")
    if manifest["contract_id"] != contract["contract_id"]:
        raise ValueError("fixture manifest contract_id does not match the loaded contract")

    fixtures = manifest["fixtures"]
    if not isinstance(fixtures, list):
        raise ValueError("fixture manifest fixtures must be an array")
    if not 1 <= len(fixtures) <= int(spec["max_fixtures"]):
        raise ValueError(
            f"fixture count must be in [1, {int(spec['max_fixtures'])}]"
        )

    expected_fields = spec["fixture_fields_in_order"]
    id_pattern = re.compile(spec["fixture_id_pattern"])
    allowed_evidence = set(spec["evidence_kind"])
    allowed_outcome = set(spec["expected_outcome"])
    allowed_failure_reasons = (
        set(contract["enums"]["claimed_reason"]) - {"none"}
    ) | set(contract["trace_reasons_v1"])
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    manifest_root = path.parent.resolve()

    for index, fixture in enumerate(fixtures):
        fixture = _require_mapping(fixture, f"fixture[{index}]")
        _require_exact_keys(fixture, expected_fields, f"fixture[{index}]")

        fixture_id = fixture["id"]
        if type(fixture_id) is not str or not id_pattern.fullmatch(fixture_id):
            raise ValueError(f"fixture[{index}].id must be lower-kebab")
        folded_id = fixture_id.casefold()
        if folded_id in seen_ids:
            raise ValueError(f"duplicate fixture id: {fixture_id}")
        seen_ids.add(folded_id)

        trace = fixture["trace"]
        if type(trace) is not str or not trace:
            raise ValueError(f"fixture[{index}].trace must be a non-empty string")
        if "\\" in trace or ":" in trace or trace.startswith("/"):
            raise ValueError(f"fixture trace must be a relative POSIX path: {trace!r}")
        posix_path = PurePosixPath(trace)
        if any(part in {"", ".", ".."} for part in posix_path.parts):
            raise ValueError(f"fixture trace contains a forbidden path component: {trace!r}")
        if posix_path.as_posix() != trace or posix_path.suffix != spec["trace_suffix"]:
            raise ValueError(f"fixture trace is not a normalized {spec['trace_suffix']} path: {trace!r}")
        for part_index, part in enumerate(posix_path.parts):
            token = posix_path.stem if part_index == len(posix_path.parts) - 1 else part
            if not id_pattern.fullmatch(token):
                raise ValueError(f"fixture trace component must be lower-kebab: {trace!r}")
        folded_path = trace.casefold()
        if folded_path in seen_paths:
            raise ValueError(f"duplicate fixture trace path: {trace}")
        seen_paths.add(folded_path)

        resolved_trace = (manifest_root / Path(*posix_path.parts)).resolve()
        try:
            resolved_trace.relative_to(manifest_root)
        except ValueError as exc:
            raise ValueError(f"fixture trace escapes manifest root: {trace!r}") from exc
        if not resolved_trace.is_file():
            raise ValueError(f"fixture trace is missing or not a file: {trace!r}")

        if fixture["evidence_kind"] not in allowed_evidence:
            raise ValueError(f"fixture[{index}].evidence_kind is not allowed")
        expected_outcome = fixture["expected_outcome"]
        expected_reason = fixture["expected_first_reason"]
        if expected_outcome not in allowed_outcome:
            raise ValueError(f"fixture[{index}].expected_outcome is not allowed")
        if expected_outcome == "PASS":
            if expected_reason is not None:
                raise ValueError("PASS fixture requires expected_first_reason=null")
        elif type(expected_reason) is not str or expected_reason not in allowed_failure_reasons:
            raise ValueError("FAIL fixture requires a registered expected_first_reason")
        if fixture["evidence_kind"] == "native-k2a-projection":
            if expected_outcome != "PASS":
                raise ValueError("native-k2a-projection fixture must expect PASS")
            try:
                native_raw = resolved_trace.read_bytes()
            except OSError as exc:
                raise ValueError(f"unreadable native-k2a-projection fixture: {exc}") from exc
            _validate_native_k2a_projection_bytes(native_raw, contract, f"fixture[{index}]")

    return manifest, raw


def _sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _atomic_write_bytes(path: Path, raw: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="wb", prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(raw)
        handle.flush()
        os.fsync(handle.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> bytes:
    raw = (
        json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    _atomic_write_bytes(path, raw)
    return raw


def _prepare_artifact_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise ValueError(f"artifact directory already exists: {path}") from exc
    except OSError as exc:
        raise ValueError(f"cannot create artifact directory: {exc}") from exc


def _git_metadata() -> dict[str, Any]:
    def run(*args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if completed.returncode != 0:
            raise ValueError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
        return completed.stdout.strip()

    return {
        "head_sha": run("rev-parse", "HEAD"),
        "dirty": bool(run("status", "--porcelain=v1", "--untracked-files=normal")),
        "github_sha": os.environ.get("GITHUB_SHA"),
    }


def _artifact_entry(path: str, raw: bytes) -> dict[str, Any]:
    return {"path": path, "bytes": len(raw), "sha256": _sha256_bytes(raw)}


def _fixture_result(fixture: dict[str, Any], verdict: dict[str, Any]) -> dict[str, Any]:
    first_failure = verdict["first_failure"]
    actual_reason = first_failure["reason"] if first_failure is not None else None
    verdict_artifact = f"verdicts/{fixture['id']}.json"
    return {
        "id": fixture["id"],
        "trace": fixture["trace"],
        "evidence_kind": fixture["evidence_kind"],
        "expected_outcome": fixture["expected_outcome"],
        "expected_first_reason": fixture["expected_first_reason"],
        "actual_outcome": verdict["outcome"],
        "actual_first_reason": actual_reason,
        "actual_line": first_failure["line"] if first_failure is not None else None,
        "actual_sequence": first_failure["sequence"] if first_failure is not None else None,
        "matched": (
            verdict["outcome"] == fixture["expected_outcome"]
            and actual_reason == fixture["expected_first_reason"]
        ),
        "verdict_artifact": verdict_artifact,
    }


def _fixture_aggregate(
    manifest: dict[str, Any],
    fixture_results: list[dict[str, Any]],
    contract: dict[str, Any],
) -> dict[str, Any]:
    mismatches = [result for result in fixture_results if not result["matched"]]
    first_mismatch = mismatches[0] if mismatches else None
    return {
        "schema_version": 1,
        "kind": "fixture-manifest-verdict",
        "outcome": "FAIL" if mismatches else "PASS",
        "passed": not mismatches,
        "manifest_id": manifest["manifest_id"],
        "contract_id": manifest["contract_id"],
        "fixture_count": len(fixture_results),
        "matched_count": len(fixture_results) - len(mismatches),
        "mismatched_count": len(mismatches),
        "first_failure": (
            {
                "reason": R_FIXTURE_MISMATCH,
                "fixture_id": first_mismatch["id"],
                "expected_outcome": first_mismatch["expected_outcome"],
                "expected_first_reason": first_mismatch["expected_first_reason"],
                "actual_outcome": first_mismatch["actual_outcome"],
                "actual_first_reason": first_mismatch["actual_first_reason"],
            }
            if first_mismatch is not None
            else None
        ),
        "reasons": [R_FIXTURE_MISMATCH] if mismatches else [],
        "fixtures": fixture_results,
        "observation_only": contract["constants"]["observation_only"],
        "management_only": contract["constants"]["management_only"],
    }


def run_fixture_manifest(
    manifest_path: Path,
    contract_path: Path,
    contract: dict[str, Any],
    artifact_dir: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest, manifest_raw = load_fixture_manifest(manifest_path, contract)
    try:
        contract_raw = contract_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"unreadable contract bytes: {exc}") from exc

    git = _git_metadata()
    _prepare_artifact_dir(artifact_dir)

    inputs: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []

    contract_artifact = "inputs/binding-trace-v1.contract.json"
    _atomic_write_bytes(artifact_dir / Path(*PurePosixPath(contract_artifact).parts), contract_raw)
    inputs.append(_artifact_entry(contract_artifact, contract_raw))

    manifest_artifact = "inputs/fixtures/manifest.json"
    _atomic_write_bytes(artifact_dir / Path(*PurePosixPath(manifest_artifact).parts), manifest_raw)
    inputs.append(_artifact_entry(manifest_artifact, manifest_raw))

    fixture_results: list[dict[str, Any]] = []
    manifest_root = manifest_path.parent.resolve()
    for fixture in manifest["fixtures"]:
        trace_path = manifest_root / Path(*PurePosixPath(fixture["trace"]).parts)
        try:
            trace_raw = trace_path.read_bytes()
        except OSError as exc:
            raise ValueError(f"unreadable fixture {fixture['id']}: {exc}") from exc

        input_artifact = f"inputs/fixtures/{fixture['trace']}"
        _atomic_write_bytes(
            artifact_dir / Path(*PurePosixPath(input_artifact).parts), trace_raw
        )
        inputs.append(_artifact_entry(input_artifact, trace_raw))

        verdict = verify_trace_bytes(trace_raw, contract)
        verdict_artifact = f"verdicts/{fixture['id']}.json"
        verdict_raw = _atomic_write_json(
            artifact_dir / Path(*PurePosixPath(verdict_artifact).parts), verdict
        )
        outputs.append(_artifact_entry(verdict_artifact, verdict_raw))
        fixture_results.append(_fixture_result(fixture, verdict))

    aggregate = _fixture_aggregate(manifest, fixture_results, contract)
    aggregate_raw = _atomic_write_json(artifact_dir / "aggregate-verdict.json", aggregate)
    outputs.append(_artifact_entry("aggregate-verdict.json", aggregate_raw))

    provenance = {
        "bundle_schema_version": 1,
        "kind": "fixture-manifest-provenance",
        "manifest_id": manifest["manifest_id"],
        "contract_id": manifest["contract_id"],
        "git": git,
        "python": {
            "implementation": sys.implementation.name,
            "version": [sys.version_info.major, sys.version_info.minor, sys.version_info.micro],
            "full_version": platform.python_version(),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "runner_os": os.environ.get("RUNNER_OS", platform.system()),
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "verifier": _artifact_entry(
            VERIFIER_RELATIVE_PATH,
            (REPO_ROOT / Path(*PurePosixPath(VERIFIER_RELATIVE_PATH).parts)).read_bytes(),
        ),
        "inputs": inputs,
        "outputs": outputs,
    }
    _atomic_write_json(artifact_dir / "provenance.json", provenance)
    return aggregate, provenance


def _bundle_member_path(bundle: Path, relative: str) -> Path:
    if type(relative) is not str or not relative or "\\" in relative or relative.startswith("/"):
        raise ValueError(f"invalid bundle member path: {relative!r}")
    posix_path = PurePosixPath(relative)
    if any(part in {"", ".", ".."} for part in posix_path.parts):
        raise ValueError(f"invalid bundle member path: {relative!r}")
    resolved = (bundle / Path(*posix_path.parts)).resolve()
    try:
        resolved.relative_to(bundle.resolve())
    except ValueError as exc:
        raise ValueError(f"bundle member escapes root: {relative!r}") from exc
    return resolved


def _load_verified_bundle(bundle: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    bundle = bundle.resolve()
    if not bundle.is_dir():
        raise ValueError(f"fixture bundle is missing or not a directory: {bundle}")
    aggregate, _ = _load_strict_json(bundle / "aggregate-verdict.json", "aggregate verdict")
    provenance, _ = _load_strict_json(bundle / "provenance.json", "provenance")

    _require_exact_keys(
        provenance,
        [
            "bundle_schema_version",
            "kind",
            "manifest_id",
            "contract_id",
            "git",
            "python",
            "platform",
            "runner_os",
            "generated_at_utc",
            "verifier",
            "inputs",
            "outputs",
        ],
        "bundle provenance",
    )
    if (
        type(provenance["bundle_schema_version"]) is not int
        or provenance["bundle_schema_version"] != 1
        or provenance["kind"] != "fixture-manifest-provenance"
    ):
        raise ValueError("bundle provenance schema or kind is invalid")

    git = _require_mapping(provenance["git"], "provenance git")
    _require_exact_keys(git, ["head_sha", "dirty", "github_sha"], "provenance git")
    if type(git["head_sha"]) is not str or not re.fullmatch(r"[0-9a-f]{40}", git["head_sha"]):
        raise ValueError("provenance git.head_sha must be a lowercase 40-hex commit")
    if type(git["dirty"]) is not bool or git["dirty"] is not False:
        raise ValueError("fixture parity requires a clean producer checkout")
    if git["github_sha"] != git["head_sha"]:
        raise ValueError("provenance git.github_sha must equal git.head_sha")

    python_info = _require_mapping(provenance["python"], "provenance python")
    _require_exact_keys(
        python_info,
        ["implementation", "version", "full_version"],
        "provenance python",
    )
    version = python_info["version"]
    if (
        type(python_info["implementation"]) is not str
        or not isinstance(version, list)
        or len(version) != 3
        or not all(type(part) is int and part >= 0 for part in version)
        or type(python_info["full_version"]) is not str
    ):
        raise ValueError("provenance python fields are malformed")

    platform_info = _require_mapping(provenance["platform"], "provenance platform")
    _require_exact_keys(platform_info, ["system", "release", "machine"], "provenance platform")
    if not all(type(platform_info[name]) is str for name in ("system", "release", "machine")):
        raise ValueError("provenance platform fields must be strings")
    if provenance["runner_os"] != platform_info["system"]:
        raise ValueError("provenance runner_os does not match platform.system")
    if type(provenance["generated_at_utc"]) is not str or not provenance[
        "generated_at_utc"
    ].endswith("Z"):
        raise ValueError("provenance generated_at_utc must be a UTC string")

    verifier = _require_mapping(provenance["verifier"], "provenance verifier")
    _require_exact_keys(verifier, ["path", "bytes", "sha256"], "provenance verifier")
    verifier_path = REPO_ROOT / Path(*PurePosixPath(VERIFIER_RELATIVE_PATH).parts)
    try:
        current_verifier_raw = verifier_path.read_bytes()
    except OSError as exc:
        raise ValueError(f"cannot read current verifier source: {exc}") from exc
    if not _json_type_strict_equal(
        verifier,
        _artifact_entry(VERIFIER_RELATIVE_PATH, current_verifier_raw),
    ):
        raise ValueError("bundle verifier identity does not match the checked-out verifier")

    declared_paths: list[str] = []
    seen_paths: set[str] = set()
    for collection_name in ("inputs", "outputs"):
        collection = provenance.get(collection_name)
        if not isinstance(collection, list):
            raise ValueError(f"provenance {collection_name} must be an array")
        for entry in collection:
            entry = _require_mapping(entry, f"provenance {collection_name} entry")
            _require_exact_keys(entry, ["path", "bytes", "sha256"], "provenance entry")
            relative = entry["path"]
            if type(relative) is not str or relative.casefold() in seen_paths:
                raise ValueError(f"duplicate or invalid provenance path: {relative!r}")
            seen_paths.add(relative.casefold())
            declared_paths.append(relative)
            member = _bundle_member_path(bundle, relative)
            try:
                raw = member.read_bytes()
            except OSError as exc:
                raise ValueError(f"unreadable bundle member {relative!r}: {exc}") from exc
            if type(entry["bytes"]) is not int or len(raw) != entry["bytes"]:
                raise ValueError(f"bundle member byte count mismatch: {relative!r}")
            if (
                type(entry["sha256"]) is not str
                or not re.fullmatch(r"[0-9a-f]{64}", entry["sha256"])
                or _sha256_bytes(raw) != entry["sha256"]
            ):
                raise ValueError(f"bundle member SHA-256 mismatch: {relative!r}")

    actual_paths = {
        path.relative_to(bundle).as_posix()
        for path in bundle.rglob("*")
        if path.is_file()
    }
    expected_actual_paths = {*declared_paths, "provenance.json"}
    if actual_paths != expected_actual_paths:
        missing = sorted(expected_actual_paths - actual_paths)
        unknown = sorted(actual_paths - expected_actual_paths)
        raise ValueError(f"bundle file set mismatch: missing={missing!r} unknown={unknown!r}")

    contract_path = bundle / "inputs" / "binding-trace-v1.contract.json"
    manifest_path = bundle / "inputs" / "fixtures" / "manifest.json"
    contract = load_contract(contract_path)
    manifest, _ = load_fixture_manifest(manifest_path, contract)

    expected_input_paths = [
        "inputs/binding-trace-v1.contract.json",
        "inputs/fixtures/manifest.json",
        *[f"inputs/fixtures/{fixture['trace']}" for fixture in manifest["fixtures"]],
    ]
    actual_input_paths = [entry["path"] for entry in provenance["inputs"]]
    if actual_input_paths != expected_input_paths:
        raise ValueError("bundle provenance inputs do not match the manifest exact set/order")

    canonical_inputs: dict[str, Path] = {
        "inputs/binding-trace-v1.contract.json": DEFAULT_CONTRACT,
        "inputs/fixtures/manifest.json": DEFAULT_FIXTURE_MANIFEST,
    }
    canonical_fixture_root = DEFAULT_FIXTURE_MANIFEST.parent
    for fixture in manifest["fixtures"]:
        canonical_inputs[f"inputs/fixtures/{fixture['trace']}"] = (
            canonical_fixture_root / Path(*PurePosixPath(fixture["trace"]).parts)
        )
    for relative, canonical_path in canonical_inputs.items():
        try:
            bundled_raw = _bundle_member_path(bundle, relative).read_bytes()
            canonical_raw = canonical_path.read_bytes()
        except OSError as exc:
            raise ValueError(f"cannot compare canonical input {relative!r}: {exc}") from exc
        if bundled_raw != canonical_raw:
            raise ValueError(f"bundle input does not match checked-out canonical input: {relative!r}")

    fixture_results: list[dict[str, Any]] = []
    expected_output_paths: list[str] = []
    for fixture in manifest["fixtures"]:
        trace_relative = f"inputs/fixtures/{fixture['trace']}"
        trace_raw = _bundle_member_path(bundle, trace_relative).read_bytes()
        computed_verdict = verify_trace_bytes(trace_raw, contract)
        verdict_relative = f"verdicts/{fixture['id']}.json"
        persisted_verdict, _ = _load_strict_json(
            _bundle_member_path(bundle, verdict_relative),
            f"fixture verdict {fixture['id']}",
        )
        if not _json_type_strict_equal(persisted_verdict, computed_verdict):
            raise ValueError(f"fixture verdict was not reproduced: {fixture['id']}")
        fixture_results.append(_fixture_result(fixture, computed_verdict))
        expected_output_paths.append(verdict_relative)

    expected_aggregate = _fixture_aggregate(manifest, fixture_results, contract)
    if not _json_type_strict_equal(aggregate, expected_aggregate):
        raise ValueError("aggregate verdict does not match independently replayed fixtures")
    expected_output_paths.append("aggregate-verdict.json")
    actual_output_paths = [entry["path"] for entry in provenance["outputs"]]
    if actual_output_paths != expected_output_paths:
        raise ValueError("bundle provenance outputs do not match the manifest exact set/order")

    if (
        provenance["manifest_id"] != manifest["manifest_id"]
        or provenance["contract_id"] != contract["contract_id"]
        or aggregate["manifest_id"] != manifest["manifest_id"]
        or aggregate["contract_id"] != contract["contract_id"]
    ):
        raise ValueError("bundle manifest/contract identities are inconsistent")
    return aggregate, provenance


def compare_fixture_bundles(left: Path, right: Path) -> dict[str, Any]:
    left_aggregate, left_provenance = _load_verified_bundle(left)
    right_aggregate, right_provenance = _load_verified_bundle(right)
    mismatches: list[str] = []

    current_head = _git_metadata()["head_sha"]
    if left_provenance["git"]["head_sha"] != current_head:
        mismatches.append("left git.head_sha does not match the parity checkout")
    if right_provenance["git"]["head_sha"] != current_head:
        mismatches.append("right git.head_sha does not match the parity checkout")
    if left_provenance.get("git", {}).get("head_sha") != right_provenance.get("git", {}).get("head_sha"):
        mismatches.append("git.head_sha differs")
    if left_provenance.get("runner_os") != PARITY_LEFT_RUNNER_OS:
        mismatches.append(
            f"left runner_os is not {PARITY_LEFT_RUNNER_OS}"
        )
    if right_provenance.get("runner_os") != PARITY_RIGHT_RUNNER_OS:
        mismatches.append(
            f"right runner_os is not {PARITY_RIGHT_RUNNER_OS}"
        )
    for label, provenance in (("left", left_provenance), ("right", right_provenance)):
        python_info = provenance.get("python", {})
        if python_info.get("implementation") != "cpython":
            mismatches.append(f"{label} Python implementation is not cpython")
        version = python_info.get("version")
        if not isinstance(version, list) or version[:2] != [3, 11]:
            mismatches.append(f"{label} Python major/minor is not 3.11")

    left_inputs = [
        (entry["path"], entry["bytes"], entry["sha256"])
        for entry in left_provenance["inputs"]
    ]
    right_inputs = [
        (entry["path"], entry["bytes"], entry["sha256"])
        for entry in right_provenance["inputs"]
    ]
    if left_inputs != right_inputs:
        mismatches.append("contract, manifest, or raw fixture input hashes differ")

    for name in ("manifest_id", "contract_id", "fixture_count"):
        if left_aggregate.get(name) != right_aggregate.get(name):
            mismatches.append(f"aggregate {name} differs")
    if left_aggregate.get("passed") is not True:
        mismatches.append("left aggregate did not pass its fixture expectations")
    if right_aggregate.get("passed") is not True:
        mismatches.append("right aggregate did not pass its fixture expectations")

    parity_fields = (
        "id",
        "trace",
        "actual_outcome",
        "actual_first_reason",
        "actual_line",
        "actual_sequence",
        "matched",
    )
    left_fixtures = left_aggregate.get("fixtures")
    right_fixtures = right_aggregate.get("fixtures")
    if not isinstance(left_fixtures, list) or not isinstance(right_fixtures, list):
        raise ValueError("aggregate fixtures must be arrays")
    left_projection = [tuple(item.get(name) for name in parity_fields) for item in left_fixtures]
    right_projection = [tuple(item.get(name) for name in parity_fields) for item in right_fixtures]
    if left_projection != right_projection:
        mismatches.append("fixture order or replay verdict tuple differs")

    first_detail = mismatches[0] if mismatches else None
    return {
        "schema_version": 1,
        "kind": "fixture-bundle-parity-verdict",
        "outcome": "FAIL" if mismatches else "PASS",
        "passed": not mismatches,
        "contract_id": left_aggregate.get("contract_id"),
        "manifest_id": left_aggregate.get("manifest_id"),
        "compared_fixture_count": len(left_fixtures),
        "left_runner_os": left_provenance.get("runner_os"),
        "right_runner_os": right_provenance.get("runner_os"),
        "first_failure": (
            {"reason": R_FIXTURE_MISMATCH, "detail": first_detail}
            if first_detail is not None
            else None
        ),
        "reasons": [R_FIXTURE_MISMATCH] if mismatches else [],
        "observation_only": 1,
        "management_only": 1,
    }


def _parity_infrastructure_failure(detail: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "fixture-bundle-parity-verdict",
        "outcome": "FAIL",
        "passed": False,
        "contract_id": None,
        "manifest_id": None,
        "compared_fixture_count": 0,
        "left_runner_os": None,
        "right_runner_os": None,
        "first_failure": {"reason": R_IO, "detail": detail},
        "reasons": [R_IO],
        "observation_only": 1,
        "management_only": 1,
    }


def _human_detail(detail: str) -> str:
    """Escape unpaired JSON surrogate claims before strict UTF-8 terminal output."""
    return detail.encode("utf-8", errors="backslashreplace").decode("utf-8")


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
        f"line={first['line']} sequence={first['sequence']} detail={_human_detail(first['detail'])} "
        f"reasons={','.join(verdict['reasons'])}"
    )


def format_fixture_human_verdict(verdict: dict[str, Any]) -> str:
    if verdict["passed"]:
        return (
            f"[BINDING-TRACE-FIXTURES] PASS manifest={verdict['manifest_id']} "
            f"fixtures={verdict['fixture_count']} matched={verdict['matched_count']}"
        )
    first = verdict["first_failure"]
    return (
        f"[BINDING-TRACE-FIXTURES] FAIL manifest={verdict['manifest_id']} "
        f"first_reason={first['reason']} fixture={first['fixture_id']} "
        f"expected={first['expected_outcome']}/{first['expected_first_reason']} "
        f"actual={first['actual_outcome']}/{first['actual_first_reason']}"
    )


def format_parity_human_verdict(verdict: dict[str, Any]) -> str:
    if verdict["passed"]:
        return (
            f"[BINDING-TRACE-PARITY] PASS manifest={verdict['manifest_id']} "
            f"fixtures={verdict['compared_fixture_count']} "
            f"left={verdict['left_runner_os']} right={verdict['right_runner_os']}"
        )
    first = verdict["first_failure"]
    return (
        f"[BINDING-TRACE-PARITY] FAIL first_reason={first['reason']} "
        f"detail={_human_detail(first['detail'])}"
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", nargs="?", help="path to one binding trace JSONL file")
    parser.add_argument(
        "--fixture-manifest",
        help="run every fixture in the expected-result sidecar manifest",
    )
    parser.add_argument(
        "--compare-fixture-bundles",
        nargs=2,
        metavar=("LEFT", "RIGHT"),
        help="compare two self-contained fixture artifact bundles",
    )
    parser.add_argument(
        "--artifact-dir",
        help="new output directory required for fixture-manifest and parity modes",
    )
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT),
                        help=f"path to the contract manifest (default: {DEFAULT_CONTRACT})")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="print the machine-readable JSON verdict instead of the human line")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    selected_modes = sum(
        (
            args.trace is not None,
            args.fixture_manifest is not None,
            args.compare_fixture_bundles is not None,
        )
    )
    if selected_modes != 1:
        parser.print_usage(sys.stderr)
        print(
            "binding_trace_replay.py: error: select exactly one of trace, "
            "--fixture-manifest, or --compare-fixture-bundles",
            file=sys.stderr,
        )
        return 2

    if args.trace is not None and args.artifact_dir is not None:
        print("[BINDING-TRACE] FAIL first_reason=trace.io detail=single trace mode forbids --artifact-dir",
              file=sys.stderr)
        return 2
    if (args.fixture_manifest is not None or args.compare_fixture_bundles is not None) and not args.artifact_dir:
        print("[BINDING-TRACE] FAIL first_reason=trace.io detail=this mode requires --artifact-dir",
              file=sys.stderr)
        return 2

    if args.compare_fixture_bundles is not None:
        artifact_dir = Path(args.artifact_dir)
        try:
            _prepare_artifact_dir(artifact_dir)
        except ValueError as exc:
            print(f"[BINDING-TRACE-PARITY] FAIL first_reason=trace.io detail={exc}", file=sys.stderr)
            return 2
        try:
            parity = compare_fixture_bundles(
                Path(args.compare_fixture_bundles[0]),
                Path(args.compare_fixture_bundles[1]),
            )
            exit_code = 0 if parity["passed"] else 1
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
            parity = _parity_infrastructure_failure(str(exc))
            exit_code = 2
        try:
            _atomic_write_json(artifact_dir / "parity-verdict.json", parity)
        except OSError as exc:
            print(
                f"[BINDING-TRACE-PARITY] FAIL first_reason=trace.io "
                f"detail=cannot write parity verdict: {exc}",
                file=sys.stderr,
            )
            return 2
        if args.json_output:
            print(json.dumps(parity, ensure_ascii=True, sort_keys=True))
        else:
            print(format_parity_human_verdict(parity))
        return exit_code

    contract_path = Path(args.contract)
    try:
        contract = load_contract(contract_path)
    except (OSError, ValueError, KeyError, TypeError, re.error) as exc:
        print(f"[BINDING-TRACE] FAIL first_reason=trace.io detail=unreadable contract: {exc}",
              file=sys.stderr)
        return 2


    if args.fixture_manifest is not None:
        artifact_dir = Path(args.artifact_dir)
        artifact_preexisted = artifact_dir.exists()
        try:
            aggregate, _ = run_fixture_manifest(
                Path(args.fixture_manifest), contract_path, contract, artifact_dir
            )
        except (OSError, ValueError, KeyError, TypeError, re.error) as exc:
            if not artifact_preexisted:
                try:
                    artifact_dir.mkdir(parents=True, exist_ok=True)
                    _atomic_write_json(
                        artifact_dir / "aggregate-verdict.json",
                        {
                            "schema_version": 1,
                            "kind": "fixture-manifest-verdict",
                            "outcome": "FAIL",
                            "passed": False,
                            "manifest_id": None,
                            "contract_id": contract.get("contract_id"),
                            "fixture_count": 0,
                            "matched_count": 0,
                            "mismatched_count": 0,
                            "first_failure": {"reason": R_IO, "detail": str(exc)},
                            "reasons": [R_IO],
                            "fixtures": [],
                            "observation_only": contract["constants"]["observation_only"],
                            "management_only": contract["constants"]["management_only"],
                        },
                    )
                except OSError:
                    pass
            print(
                f"[BINDING-TRACE-FIXTURES] FAIL first_reason=trace.io detail={exc}",
                file=sys.stderr,
            )
            return 2
        if args.json_output:
            print(json.dumps(aggregate, ensure_ascii=True, sort_keys=True))
        else:
            print(format_fixture_human_verdict(aggregate))
        return 0 if aggregate["passed"] else 1

    trace_path = Path(args.trace)
    try:
        raw = trace_path.read_bytes()
    except OSError as exc:
        print(f"[BINDING-TRACE] FAIL first_reason=trace.io detail=unreadable trace: {exc}",
              file=sys.stderr)
        return 2

    try:
        verdict = verify_trace_bytes(raw, contract)
    except (ValueError, KeyError, TypeError, re.error) as exc:
        print(
            f"[BINDING-TRACE] FAIL first_reason=trace.io detail=invalid contract use: {exc}",
            file=sys.stderr,
        )
        return 2
    if args.json_output:
        print(json.dumps(verdict, ensure_ascii=True, sort_keys=True))
    else:
        print(format_human_verdict(verdict))
    return 0 if verdict["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
