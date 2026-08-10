from __future__ import annotations

import re
from collections.abc import Iterable, Mapping


SCHEMA_VERSION = 1

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
FAIL_RE = re.compile(r"\bFAIL\b")
FATAL_RE = re.compile(r"\bFATAL\b")
HEALTH_FIELD_RE = re.compile(
    r"(?<!\S)(?P<key>[a-z_]+)=(?P<value>\S+)"
)
EVIDENCE_FIELD_RE = re.compile(
    r"(?<!\S)(?P<key>[A-Za-z_][A-Za-z0-9_]*)=(?P<value>\S+)"
)
IDE_CHANNELS_RECORD_RE = re.compile(
    r"^\[STO\] IDE channels "
    r"primary=(?P<primary_command>0x[0-9A-Fa-f]+)/"
    r"(?P<primary_control>0x[0-9A-Fa-f]+) "
    r"status=(?P<primary_status>0x[0-9A-Fa-f]+) "
    r"live=(?P<primary_live>[01]) "
    r"secondary=(?P<secondary_command>0x[0-9A-Fa-f]+)/"
    r"(?P<secondary_control>0x[0-9A-Fa-f]+) "
    r"status=(?P<secondary_status>0x[0-9A-Fa-f]+) "
    r"live=(?P<secondary_live>[01])$"
)
ROOM_SNAPSHOT_RECORD_RE = re.compile(
    r"^\[ROOM\] snapshot stability=(?P<stability>[a-z][a-z0-9-]*) "
    r"ok=(?P<ok>0|[1-9][0-9]{0,9}) degraded=(?P<degraded>0|[1-9][0-9]{0,9}) "
    r"failed=(?P<failed>0|[1-9][0-9]{0,9}) unknown=(?P<unknown>0|[1-9][0-9]{0,9}) "
    r"topology=(?P<topology>[a-z][a-z0-9-]*) "
    r"domains=(?P<domains>0|[1-9][0-9]{0,9}) windows=(?P<windows>0|[1-9][0-9]{0,9}) "
    r"drivers=(?P<drivers_ready>0|[1-9][0-9]{0,9})/(?P<drivers>0|[1-9][0-9]{0,9}) "
    r"plans=(?P<plans>0|[1-9][0-9]{0,9}) nodes=(?P<nodes>0|[1-9][0-9]{0,9}) "
    r"rings=(?P<rings>0|[1-9][0-9]{0,9}) active=(?P<active>0|[1-9][0-9]{0,9}) "
    r"user=(?P<user>0|[1-9][0-9]{0,9}) "
    r"nodebit_active=(?P<nodebit_active>0|[1-9][0-9]{0,9}) "
    r"nodebit_risky=(?P<nodebit_risky>0|[1-9][0-9]{0,9})$"
)
ROOM_UINT32_MAX = (1 << 32) - 1
ROOM_TOPOLOGIES = frozenset(
    {"uniform", "segmented", "numa-hinted", "fabric-expandable"}
)

TERMINAL_CHECKPOINTS: tuple[tuple[str, str], ...] = (
    ("ring3_scaffold", "[USER] Ring3 scaffold ready=1"),
    ("bootstrap_process", "[PROC] bootstrap ownership selftest PASS"),
    ("ring3_exec", "[USER] ring3 exec PASS"),
    ("private_address_space_exec", "[USER] private address space exec PASS"),
    ("bootstrap_process_stack", "[USER] bootstrap process stack PASS"),
    ("bootstrap_process_pair", "[USER] bootstrap process pair PASS"),
    ("user_trap_capture", "[TRAP] user frame capture PASS"),
    ("process_trap_snapshot", "[PROC] trap evidence snapshot PASS"),
    ("process_event_journal", "[PROC] process event journal PASS"),
    ("ring3_entry_ac_hardening", "[SEC] ring3 entry AC hardening PASS"),
    (
        "kernel_room_management",
        "[ROOM] management hierarchy selftest PASS",
    ),
    ("kernel_room", "[ROOM] snapshot stability=stable"),
    ("health", "[HEALTH] stability="),
    ("kernel_ready", "=== AIOS Kernel Ready ==="),
    ("boot_complete", "[KERNEL] Boot complete. Launching interactive shell..."),
    ("shell_started", "[SHELL] Interactive shell started"),
)

INLINE_REQUIRED_PATTERNS = frozenset({"AIOS Kernel Ready", "label=storage-bootstrap"})
INLINE_REQUIRED_LINE_PREFIXES = {
    "AIOS Kernel Ready": "=== ",
    "label=storage-bootstrap": "[SLM] Seeded plan ",
}
ALLOWED_DUPLICATE_FIELDS_BY_PREFIX = {
    # One record intentionally describes both IDE channels. Only the two
    # per-channel fields may repeat; controller identity must remain unique.
    "[STO] IDE channels": frozenset({"status", "live"}),
}
EXACT_REQUIRED_RECORDS = (
    ("[RESOURCE] ledger selftest PASS ", "resource_ledger"),
    ("[PRESSURE] tracker selftest PASS ", "pressure_tracker"),
    ("[TRAP] frame contract selftest PASS ", "trapframe_contract"),
    ("[TRAP] user frame capture PASS ", "user_trap_capture"),
    ("[PROC] trap evidence snapshot PASS ", "process_trap_snapshot"),
    ("[PROC] process event journal PASS ", "process_event_journal"),
    ("[SEC] nx=", "cpu_security"),
    ("[SEC] ring3 entry AC hardening PASS ", "ring3_entry_ac_hardening"),
    (
        "[ROOM] management hierarchy selftest PASS ",
        "kernel_room_management",
    ),
)

PROCESS_EVENT_JOURNAL_FAMILY_PREFIX = "[PROC] process event journal "
CPU_SECURITY_FAMILY_PREFIX = "[SEC] nx="
RING3_ENTRY_AC_HARDENING_FAMILY_PREFIX = "[SEC] ring3 entry AC hardening "
KERNEL_ROOM_MANAGEMENT_FAMILY_PREFIX = "[ROOM] management hierarchy"
ROOM_SNAPSHOT_FAMILY_PREFIX = "[ROOM] snapshot "
SECURITY_CHECKPOINT_ORDER = (
    "cpu_security",
    "ring3_entry_ac_hardening",
    "kernel_room",
)


def _sanitize_lines(log_text: str) -> list[str]:
    lines: list[str] = []
    for raw_line in log_text.splitlines():
        clean = ANSI_ESCAPE_RE.sub("", raw_line).rstrip()
        # Preserve blank placeholders so verdict line numbers continue to point
        # at the raw serial artifact rather than a compacted view of it.
        lines.append(clean)
    return lines


def _line_occurrences(
    lines: list[str],
    needle: str,
    *,
    value_prefix: bool = False,
    anchor_start: bool = True,
) -> list[dict[str, object]]:
    occurrences: list[dict[str, object]] = []
    for index, line in enumerate(lines, start=1):
        start = 0
        while True:
            column = line.find(needle, start)
            if column < 0:
                break
            end = column + len(needle)
            if anchor_start:
                # The first proof must begin the sanitized line. Once a real
                # marker exists, later bounded copies are still counted so a
                # same-line or diagnostic duplicate cannot hide.
                left_bounded = column == 0 or (
                    bool(occurrences) and line[column - 1].isspace()
                )
            else:
                left_bounded = column == 0 or line[column - 1].isspace()
            if value_prefix:
                right_bounded = end < len(line) and not line[end].isspace()
            else:
                right_bounded = end == len(line) or line[end].isspace()
            if not left_bounded or not right_bounded:
                start = column + 1
                continue
            occurrences.append(
                {"line": index, "column": column + 1, "text": line}
            )
            start = end
    return occurrences


def _duplicate_evidence_fields(
    occurrence_groups: Iterable[list[dict[str, object]]],
) -> list[dict[str, object]]:
    duplicate_events: list[dict[str, object]] = []
    inspected_lines: set[int] = set()
    for occurrences in occurrence_groups:
        for occurrence in occurrences:
            line_number = int(occurrence["line"])
            if line_number in inspected_lines:
                continue
            inspected_lines.add(line_number)
            text = str(occurrence["text"])
            values: dict[str, list[str]] = {}
            for match in EVIDENCE_FIELD_RE.finditer(text):
                values.setdefault(match.group("key"), []).append(match.group("value"))
            duplicated = {
                key: field_values
                for key, field_values in values.items()
                if len(field_values) > 1
            }
            for prefix, allowed_keys in ALLOWED_DUPLICATE_FIELDS_BY_PREFIX.items():
                if text.startswith(prefix):
                    duplicated = {
                        key: field_values
                        for key, field_values in duplicated.items()
                        if key not in allowed_keys
                    }
                    break
            if duplicated:
                duplicate_events.append(
                    {
                        "line": line_number,
                        "text": text,
                        "fields": duplicated,
                    }
                )
    return duplicate_events


def _invalid_required_evidence_records(
    required_occurrences: Mapping[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    invalid_records: list[dict[str, object]] = []
    for occurrence in required_occurrences.get("[STO] IDE channels", []):
        text = str(occurrence["text"])
        values: dict[str, list[str]] = {}
        for match in EVIDENCE_FIELD_RE.finditer(text):
            values.setdefault(match.group("key"), []).append(match.group("value"))
        counts = {
            key: len(values.get(key, []))
            for key in ("primary", "secondary", "status", "live")
        }
        record_match = IDE_CHANNELS_RECORD_RE.fullmatch(text)
        valid = record_match is not None
        if record_match is not None:
            endpoints = [
                int(record_match.group(name), 16)
                for name in (
                    "primary_command",
                    "primary_control",
                    "secondary_command",
                    "secondary_control",
                )
            ]
            statuses = [
                int(record_match.group(name), 16)
                for name in ("primary_status", "secondary_status")
            ]
            valid = (
                len(set(endpoints)) == len(endpoints)
                and all(0 < endpoint <= 0xFFFF for endpoint in endpoints)
                and all(0 <= status <= 0xFF for status in statuses)
            )
        if not valid:
            invalid_records.append(
                {
                    "line": occurrence["line"],
                    "text": text,
                    "record": "ide_channels",
                    "field_counts": counts,
                }
            )
    for pattern, occurrences in required_occurrences.items():
        record_name = next(
            (
                name
                for prefix, name in EXACT_REQUIRED_RECORDS
                if pattern.startswith(prefix)
            ),
            None,
        )
        if record_name is None:
            continue
        for occurrence in occurrences:
            text = str(occurrence["text"])
            if text != pattern:
                invalid_records.append(
                    {
                        "line": occurrence["line"],
                        "text": text,
                        "record": record_name,
                    }
                )
    return invalid_records


def _invalid_process_event_journal_family_records(
    lines: list[str],
    required_occurrences: Mapping[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    """Reject a canonical journal summary plus an extra malformed row.

    Exact required-pattern matching alone cannot see a second family row that
    omits or mutates the PASS token. The event-journal contract is a single
    anchored summary, so every anchored family row must be that one canonical
    record.
    """

    canonical_patterns = [
        pattern
        for pattern in required_occurrences
        if pattern.startswith("[PROC] process event journal PASS ")
    ]
    if not canonical_patterns:
        return []

    canonical = canonical_patterns[0]
    family_rows = [
        {"line": index, "text": line}
        for index, line in enumerate(lines, start=1)
        if line.startswith(PROCESS_EVENT_JOURNAL_FAMILY_PREFIX)
    ]
    if len(family_rows) == 1 and family_rows[0]["text"] == canonical:
        return []

    return [
        {
            "line": row["line"],
            "text": row["text"],
            "record": "process_event_journal",
        }
        for row in family_rows
        if row["text"] != canonical or len(family_rows) != 1
    ]


def _invalid_kernel_room_management_family_records(
    lines: list[str],
    required_occurrences: Mapping[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    """Require one exact management-hierarchy record and no family siblings."""

    canonical_patterns = [
        pattern
        for pattern in required_occurrences
        if pattern.startswith(
            "[ROOM] management hierarchy selftest PASS "
        )
    ]
    if not canonical_patterns:
        return []

    canonical = canonical_patterns[0]
    family_rows = [
        {"line": index, "text": line}
        for index, line in enumerate(lines, start=1)
        if line.startswith(KERNEL_ROOM_MANAGEMENT_FAMILY_PREFIX)
    ]
    if len(family_rows) == 1 and family_rows[0]["text"] == canonical:
        return []

    return [
        {
            "line": row["line"],
            "text": row["text"],
            "record": "kernel_room_management",
        }
        for row in family_rows
        if row["text"] != canonical or len(family_rows) != 1
    ]


def _invalid_security_family_records(
    lines: list[str],
    required_occurrences: Mapping[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    invalid: list[dict[str, object]] = []
    contracts = (
        (CPU_SECURITY_FAMILY_PREFIX, "[SEC] nx=", "cpu_security"),
        (
            RING3_ENTRY_AC_HARDENING_FAMILY_PREFIX,
            "[SEC] ring3 entry AC hardening PASS ",
            "ring3_entry_ac_hardening",
        ),
    )
    for family_prefix, canonical_prefix, record_name in contracts:
        canonical_patterns = [
            pattern
            for pattern in required_occurrences
            if pattern.startswith(canonical_prefix)
        ]
        if not canonical_patterns:
            continue
        canonical = canonical_patterns[0]
        family_rows = [
            {"line": index, "text": line}
            for index, line in enumerate(lines, start=1)
            if line.startswith(family_prefix)
        ]
        if len(family_rows) == 1 and family_rows[0]["text"] == canonical:
            continue
        invalid.extend(
            {
                "line": row["line"],
                "text": row["text"],
                "record": record_name,
            }
            for row in family_rows
            if row["text"] != canonical or len(family_rows) != 1
        )
    return invalid


def _invalid_room_snapshot_family_records(
    lines: list[str],
    terminal_occurrences: Mapping[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    family_rows = [
        {"line": index, "text": line}
        for index, line in enumerate(lines, start=1)
        if line.startswith(ROOM_SNAPSHOT_FAMILY_PREFIX)
    ]
    canonical_rows = terminal_occurrences["kernel_room"]
    record_match = (
        ROOM_SNAPSHOT_RECORD_RE.fullmatch(str(family_rows[0]["text"]))
        if len(family_rows) == 1
        else None
    )
    semantic_valid = False
    if record_match is not None:
        values = {
            name: int(record_match.group(name))
            for name in (
                "ok", "degraded", "failed", "unknown", "domains",
                "windows", "drivers_ready", "drivers", "plans", "nodes",
                "rings", "active", "user", "nodebit_active",
                "nodebit_risky",
            )
        }
        semantic_valid = (
            all(value <= ROOM_UINT32_MAX for value in values.values())
            and record_match.group("stability") == "stable"
            and values["ok"] > 0
            and values["degraded"] == 0
            and values["failed"] == 0
            and record_match.group("topology") in ROOM_TOPOLOGIES
            and values["domains"] > 0
            and values["drivers"] > 0
            and values["drivers_ready"] <= values["drivers"]
            and values["active"] <= values["rings"]
            and values["user"] == 1
            and values["nodebit_risky"] <= values["nodebit_active"]
        )
    if (
        len(family_rows) == 1
        and len(canonical_rows) == 1
        and family_rows[0]["line"] == canonical_rows[0]["line"]
        and semantic_valid
    ):
        return []
    return [
        {
            "line": row["line"],
            "text": row["text"],
            "record": "kernel_room",
        }
        for row in family_rows
    ]


def _duplicate_exact_required_records(
    required_occurrences: Mapping[str, list[dict[str, object]]],
) -> list[dict[str, object]]:
    duplicate_records: list[dict[str, object]] = []
    for pattern, occurrences in required_occurrences.items():
        record_name = next(
            (
                name
                for prefix, name in EXACT_REQUIRED_RECORDS
                if pattern.startswith(prefix)
            ),
            None,
        )
        if record_name is None or len(occurrences) <= 1:
            continue
        duplicate_records.append(
            {
                "record": record_name,
                "lines": [occurrence["line"] for occurrence in occurrences],
                "texts": [occurrence["text"] for occurrence in occurrences],
            }
        )
    return duplicate_records


def _security_checkpoint_chain(
    required_occurrences: Mapping[str, list[dict[str, object]]],
    terminal_occurrences: Mapping[str, list[dict[str, object]]],
) -> dict[str, object]:
    cpu_patterns = [
        pattern
        for pattern in required_occurrences
        if pattern.startswith("[SEC] nx=")
    ]
    entry_patterns = [
        pattern
        for pattern in required_occurrences
        if pattern.startswith("[SEC] ring3 entry AC hardening PASS ")
    ]
    enabled = bool(cpu_patterns or entry_patterns)
    occurrences: dict[str, list[dict[str, object]]] = {
        "cpu_security": [
            occurrence
            for pattern in cpu_patterns
            for occurrence in required_occurrences[pattern]
        ],
        "ring3_entry_ac_hardening": [
            occurrence
            for pattern in entry_patterns
            for occurrence in required_occurrences[pattern]
        ],
        "kernel_room": list(terminal_occurrences["kernel_room"]),
    }
    missing = []
    duplicates = []
    order_violations: list[dict[str, object]] = []
    if enabled:
        if len(cpu_patterns) != 1:
            missing.append("cpu_security")
        if len(entry_patterns) != 1:
            missing.append("ring3_entry_ac_hardening")
        for name in SECURITY_CHECKPOINT_ORDER:
            matches = occurrences[name]
            if not matches and name not in missing:
                missing.append(name)
            if len(matches) > 1:
                duplicates.append(
                    {
                        "checkpoint": name,
                        "lines": [match["line"] for match in matches],
                    }
                )

        previous_name = None
        previous_line = None
        for name in SECURITY_CHECKPOINT_ORDER:
            matches = occurrences[name]
            if not matches:
                continue
            current_line = int(matches[0]["line"])
            if previous_line is not None and current_line <= previous_line:
                order_violations.append(
                    {
                        "checkpoint": name,
                        "line": current_line,
                        "expected_after": previous_name,
                        "expected_after_line": previous_line,
                    }
                )
            previous_name = name
            previous_line = current_line

    return {
        "enabled": enabled,
        "expected_order": list(SECURITY_CHECKPOINT_ORDER),
        "occurrences": occurrences,
        "missing": missing,
        "duplicates": duplicates,
        "order_violations": order_violations,
        "passed": (
            not enabled
            or (not missing and not duplicates and not order_violations)
        ),
    }


def _fatal_events(lines: list[str]) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    for index, line in enumerate(lines, start=1):
        kind = None
        if "*** KERNEL PANIC ***" in line:
            kind = "PANIC"
        elif "!!! EXCEPTION" in line:
            kind = "EXCEPTION"
        elif FAIL_RE.search(line):
            kind = "FAIL"
        elif FATAL_RE.search(line):
            kind = "FATAL"
        if kind is not None:
            events.append({"kind": kind, "line": index, "text": line})
    return events


def _parse_health(occurrences: list[dict[str, object]]) -> dict[str, object]:
    health: dict[str, object] = {
        "parsed": False,
        "line": None,
        "text": None,
        "stability": None,
        "degraded": None,
        "failed": None,
        "field_counts": {"stability": 0, "degraded": 0, "failed": 0},
        "passed": False,
    }
    if not occurrences:
        return health

    first = occurrences[0]
    line_text = str(first["text"])
    field_values: dict[str, list[str]] = {}
    for match in HEALTH_FIELD_RE.finditer(line_text):
        field_values.setdefault(match.group("key"), []).append(match.group("value"))
    field_counts = {
        key: len(field_values.get(key, []))
        for key in ("stability", "degraded", "failed")
    }

    degraded_text = (
        field_values["degraded"][0] if field_counts["degraded"] == 1 else None
    )
    failed_text = field_values["failed"][0] if field_counts["failed"] == 1 else None
    degraded = int(degraded_text) if degraded_text and degraded_text.isascii() and degraded_text.isdecimal() else None
    failed = int(failed_text) if failed_text and failed_text.isascii() and failed_text.isdecimal() else None

    stability = field_values["stability"][0] if field_counts["stability"] == 1 else None
    parsed = (
        all(count == 1 for count in field_counts.values())
        and stability is not None
        and degraded is not None
        and failed is not None
    )
    health.update(
        {
            "parsed": parsed,
            "line": first["line"],
            "text": line_text,
            "stability": stability,
            "degraded": degraded,
            "failed": failed,
            "field_counts": field_counts,
            "passed": (
                parsed
                and stability == "stable"
                and degraded_text == "0"
                and failed_text == "0"
            ),
        }
    )
    return health


def _termination_payload(termination: Mapping[str, object] | None) -> dict[str, object]:
    payload: dict[str, object] = {
        "reason": "not-evaluated",
        "exit_code": None,
        "timed_out": None,
        "duration_ms": None,
    }
    if termination is not None:
        for key in payload:
            if key in termination:
                payload[key] = termination[key]
    return payload


def evaluate_normal_boot(
    log_text: str,
    required_patterns: Iterable[str],
    termination: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Evaluate the V0 normal-boot log contract without launching QEMU.

    Termination is carried in the schema for forward compatibility, but V0 does
    not gate on it. V1 will distinguish timeout, guest exit, and host kill.
    """

    lines = _sanitize_lines(log_text)
    required = list(required_patterns)
    required_occurrences = {
        pattern: [
            occurrence
            for occurrence in _line_occurrences(
                lines,
                pattern,
                value_prefix=pattern.endswith("="),
                anchor_start=pattern not in INLINE_REQUIRED_PATTERNS,
            )
            if pattern not in INLINE_REQUIRED_LINE_PREFIXES
            or str(occurrence["text"]).startswith(
                INLINE_REQUIRED_LINE_PREFIXES[pattern]
            )
        ]
        for pattern in required
    }
    missing_patterns = [pattern for pattern in required if not required_occurrences[pattern]]
    fatal_events = _fatal_events(lines)

    occurrences = {
        name: _line_occurrences(
            lines,
            needle,
            value_prefix=name == "health",
        )
        for name, needle in TERMINAL_CHECKPOINTS
    }
    security_checkpoints = _security_checkpoint_chain(
        required_occurrences, occurrences
    )
    duplicate_field_required_occurrences = [
        required_occurrences[pattern]
        for pattern in required
    ]
    duplicate_evidence_fields = _duplicate_evidence_fields(
        [*duplicate_field_required_occurrences, *occurrences.values()]
    )
    invalid_evidence_records = _invalid_required_evidence_records(
        required_occurrences
    )
    invalid_evidence_records.extend(
        _invalid_process_event_journal_family_records(
            lines, required_occurrences
        )
    )
    invalid_evidence_records.extend(
        _invalid_kernel_room_management_family_records(
            lines, required_occurrences
        )
    )
    invalid_evidence_records.extend(
        _invalid_security_family_records(lines, required_occurrences)
    )
    invalid_evidence_records.extend(
        _invalid_room_snapshot_family_records(lines, occurrences)
    )
    duplicate_evidence_records = _duplicate_exact_required_records(
        required_occurrences
    )
    missing_checkpoints = [name for name, _ in TERMINAL_CHECKPOINTS if not occurrences[name]]
    duplicates = [
        {
            "checkpoint": name,
            "lines": [entry["line"] for entry in occurrences[name]],
        }
        for name, _ in TERMINAL_CHECKPOINTS
        if len(occurrences[name]) > 1
    ]

    order_violations: list[dict[str, object]] = []
    previous_name = None
    previous_line = None
    for name, _ in TERMINAL_CHECKPOINTS:
        if not occurrences[name]:
            continue
        current_line = int(occurrences[name][0]["line"])
        if previous_line is not None and current_line <= previous_line:
            order_violations.append(
                {
                    "checkpoint": name,
                    "line": current_line,
                    "expected_after": previous_name,
                    "expected_after_line": previous_line,
                }
            )
        previous_name = name
        previous_line = current_line

    checkpoints_passed = (
        not missing_checkpoints
        and not duplicates
        and not order_violations
        and bool(security_checkpoints["passed"])
    )
    health = _parse_health(occurrences["health"])

    reasons: list[dict[str, object]] = []
    if missing_patterns:
        reasons.append(
            {
                "code": "MISSING_REQUIRED_PATTERNS",
                "patterns": missing_patterns,
            }
        )
    if fatal_events:
        reasons.append(
            {
                "code": "FATAL_EVENTS_PRESENT",
                "count": len(fatal_events),
                "first_event": fatal_events[0],
            }
        )
    if not health["passed"]:
        reasons.append({"code": "HEALTH_INVALID", "health": health})
    if duplicate_evidence_fields:
        reasons.append(
            {
                "code": "EVIDENCE_FIELDS_DUPLICATED",
                "events": duplicate_evidence_fields,
            }
        )
    if invalid_evidence_records:
        reasons.append(
            {
                "code": "EVIDENCE_RECORD_INVALID",
                "events": invalid_evidence_records,
            }
        )
    if duplicate_evidence_records:
        reasons.append(
            {
                "code": "EVIDENCE_RECORD_DUPLICATED",
                "events": duplicate_evidence_records,
            }
        )
    if missing_checkpoints:
        reasons.append(
            {
                "code": "TERMINAL_CHECKPOINTS_MISSING",
                "checkpoints": missing_checkpoints,
            }
        )
    if duplicates:
        reasons.append(
            {
                "code": "TERMINAL_CHECKPOINTS_DUPLICATED",
                "duplicates": duplicates,
            }
        )
    if order_violations:
        reasons.append(
            {
                "code": "TERMINAL_CHECKPOINT_ORDER_INVALID",
                "violations": order_violations,
            }
        )
    if (
        security_checkpoints["enabled"]
        and not security_checkpoints["passed"]
    ):
        reasons.append(
            {
                "code": "SECURITY_CHECKPOINT_CHAIN_INVALID",
                "missing": security_checkpoints["missing"],
                "duplicates": security_checkpoints["duplicates"],
                "order_violations": security_checkpoints["order_violations"],
            }
        )

    first_failure: dict[str, object] | None = None
    line_failures: list[dict[str, object]] = []
    line_failures.extend(fatal_events)
    if not health["passed"] and health["line"] is not None:
        line_failures.append(
            {
                "kind": "HEALTH_INVALID",
                "line": health["line"],
                "text": health["text"],
            }
        )
    for event in duplicate_evidence_fields:
        line_failures.append(
            {
                "kind": "EVIDENCE_FIELDS_DUPLICATED",
                "line": event["line"],
                "text": event["text"],
                "fields": event["fields"],
            }
        )
    for event in invalid_evidence_records:
        line_failures.append(
            {
                "kind": "EVIDENCE_RECORD_INVALID",
                "line": event["line"],
                "text": event["text"],
                "record": event["record"],
            }
        )
    for event in duplicate_evidence_records:
        duplicate_lines = event["lines"]
        if isinstance(duplicate_lines, list) and len(duplicate_lines) > 1:
            line_failures.append(
                {
                    "kind": "EVIDENCE_RECORD_DUPLICATED",
                    "line": duplicate_lines[1],
                    "record": event["record"],
                }
            )
    for duplicate in duplicates:
        duplicate_lines = duplicate["lines"]
        if isinstance(duplicate_lines, list) and len(duplicate_lines) > 1:
            line_failures.append(
                {
                    "kind": "TERMINAL_CHECKPOINT_DUPLICATED",
                    "line": duplicate_lines[1],
                    "checkpoint": duplicate["checkpoint"],
                }
            )
    for violation in order_violations:
        line_failures.append({"kind": "TERMINAL_CHECKPOINT_ORDER_INVALID", **violation})
    for violation in security_checkpoints["order_violations"]:
        line_failures.append(
            {"kind": "SECURITY_CHECKPOINT_ORDER_INVALID", **violation}
        )

    if line_failures:
        first_failure = min(line_failures, key=lambda item: int(item["line"]))
    elif missing_patterns:
        first_failure = {
            "kind": "MISSING_REQUIRED_PATTERN",
            "line": None,
            "pattern": missing_patterns[0],
        }
    elif missing_checkpoints:
        first_failure = {
            "kind": "TERMINAL_CHECKPOINT_MISSING",
            "line": None,
            "checkpoint": missing_checkpoints[0],
        }

    passed = not reasons
    return {
        "schema_version": SCHEMA_VERSION,
        "outcome": "PASS" if passed else "FAIL",
        "passed": passed,
        "reasons": reasons,
        "first_failure": first_failure,
        "missing_patterns": missing_patterns,
        "fatal_events": fatal_events,
        "duplicate_evidence_fields": duplicate_evidence_fields,
        "invalid_evidence_records": invalid_evidence_records,
        "duplicate_evidence_records": duplicate_evidence_records,
        "health": health,
        "checkpoints": {
            "expected_order": [name for name, _ in TERMINAL_CHECKPOINTS],
            "occurrences": occurrences,
            "missing": missing_checkpoints,
            "duplicates": duplicates,
            "order_violations": order_violations,
            "passed": checkpoints_passed,
        },
        "security_checkpoints": security_checkpoints,
        "termination": _termination_payload(termination),
    }
