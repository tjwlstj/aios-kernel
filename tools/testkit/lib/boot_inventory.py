from __future__ import annotations

import json
import time
from pathlib import Path

from lib.baseline_guard import (
    require_exact_profile_request,
    require_strict_baseline_write,
    require_trusted_matrix_source,
)
from lib.common import BUILD_DIR, REPO_ROOT, ToolError, ensure_dir, print_step
from lib.boot_matrix_lane import run_boot_matrix
from lib.kernel_lane import ensure_smoke_profile


BOOT_BASELINE_DIR = REPO_ROOT / "tools" / "testkit" / "fixtures" / "boot-baseline"
BOOT_INVENTORY_DIR = BUILD_DIR / "boot-inventory"

EXPECTED_CONTROLLER_STATES: dict[str, dict[str, str]] = {
    "full": {
        "network": "ready",
        "usb": "ready",
        "storage": "ready",
    },
    "minimal": {
        "network": "absent",
        "usb": "absent",
        "storage": "ready",
    },
    "storage-only": {
        "network": "absent",
        "usb": "absent",
        "storage": "ready",
    },
}

EXPECTED_PROCESS_STACK_PROOF: dict[str, int] = {
    "slots": 2,
    "owned_processes": 2,
    "stack_bytes": 16384,
    "unique_cr3": 1,
    "unique_backing": 1,
    "unique_stack": 1,
    "pid": 1,
    "slot": 0,
    "process_bound": 1,
    "kstack_bytes": 16384,
    "rsp0_changed": 1,
    "rsp0_published": 1,
    "int80_entries": 3,
    "all_int80_entries_in_stack": 1,
    "rsp0_restored": 1,
    "kstack_floor_canary": 1,
}


def inventory_baseline_path(profile: str) -> Path:
    return BOOT_BASELINE_DIR / f"{profile}.json"


def inventory_current_path(profile: str) -> Path:
    return BOOT_INVENTORY_DIR / "current" / f"{profile}.json"


def _normalize_profiles(profiles: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for profile in profiles:
        normalized = ensure_smoke_profile(profile)
        if normalized not in seen:
            ordered.append(normalized)
            seen.add(normalized)
    if not ordered:
        raise ToolError("boot-inventory requires at least one smoke profile.")
    return ordered


def build_inventory_record(result: dict[str, object]) -> dict[str, object]:
    return {
        "profile": result.get("profile"),
        "ready": result.get("ready"),
        "stability": result.get("stability"),
        "device_summary": result.get("device_summary", {}),
        "health_summary": result.get("health_summary", {}),
        "controller_states": result.get("controller_states", {}),
        "slm_seeded_plan_count": result.get("slm_seeded_plan_count"),
        "process_stack": result.get("process_stack", {}),
    }


def require_complete_inventory_record(profile: str, record: dict[str, object]) -> None:
    errors: list[str] = []
    if record.get("profile") != profile:
        errors.append("profile mismatch")
    if record.get("ready") is not True:
        errors.append("ready proof missing")
    if record.get("stability") != "stable":
        errors.append("stable health proof missing")

    numeric_sections = {
        "device_summary": ("pci", "matched", "eth", "wifi", "bt", "usb", "storage"),
        "health_summary": ("ok", "degraded", "failed", "unknown", "io_degraded"),
    }
    for section_name, required_keys in numeric_sections.items():
        section = record.get(section_name)
        if not isinstance(section, dict):
            errors.append(f"{section_name} missing")
            continue
        for key in required_keys:
            value = section.get(key)
            if type(value) is not int:
                errors.append(f"{section_name}.{key} missing")
            elif value < 0:
                errors.append(
                    f"{section_name}.{key} must be non-negative, got {value}"
                )

    health_summary = record.get("health_summary")
    if isinstance(health_summary, dict):
        for key in ("degraded", "failed", "io_degraded"):
            value = health_summary.get(key)
            if type(value) is int and value != 0:
                errors.append(f"health_summary.{key} expected 0, got {value}")

    expected_controllers = EXPECTED_CONTROLLER_STATES.get(profile)
    controllers = record.get("controller_states")
    if expected_controllers is None:
        errors.append("controller state contract missing for profile")
    elif not isinstance(controllers, dict):
        errors.append("controller_states missing")
    else:
        expected_names = set(expected_controllers)
        actual_names = set(controllers)
        for name in sorted(expected_names - actual_names):
            errors.append(f"controller_states.{name} missing")
        for name in sorted(actual_names - expected_names):
            errors.append(f"controller_states.{name} unexpected")
        for name, expected_state in expected_controllers.items():
            actual_state = controllers.get(name)
            if actual_state != expected_state:
                errors.append(
                    f"controller_states.{name} expected {expected_state}, "
                    f"got {actual_state!r}"
                )

    slm_seeded_plan_count = record.get("slm_seeded_plan_count")
    if type(slm_seeded_plan_count) is not int or slm_seeded_plan_count <= 0:
        errors.append("slm_seeded_plan_count must be a positive integer")

    process_stack = record.get("process_stack")
    if not isinstance(process_stack, dict):
        errors.append("process_stack missing")
    else:
        if process_stack.get("ready") is not True:
            errors.append("process_stack.ready missing")
        if process_stack.get("ownership_status") != "PASS":
            errors.append("process_stack ownership proof missing")
        if process_stack.get("execution_status") != "PASS":
            errors.append("process_stack execution proof missing")
        for key, expected_value in EXPECTED_PROCESS_STACK_PROOF.items():
            actual_value = process_stack.get(key)
            if type(actual_value) is not int:
                errors.append(f"process_stack.{key} missing")
            elif actual_value != expected_value:
                errors.append(
                    f"process_stack.{key} expected {expected_value}, "
                    f"got {actual_value}"
                )

    if errors:
        raise ToolError(
            f"Boot inventory baseline source for `{profile}` is incomplete: "
            + "; ".join(errors)
        )


def compare_inventory_records(baseline: dict[str, object], current: dict[str, object]) -> dict[str, object]:
    mismatches: dict[str, object] = {}

    validation_errors: dict[str, str] = {}
    profile = current.get("profile")
    if not isinstance(profile, str):
        validation_errors["current"] = "profile missing"
    else:
        for source, record in (("baseline", baseline), ("current", current)):
            try:
                require_complete_inventory_record(profile, record)
            except ToolError as exc:
                validation_errors[source] = str(exc)
    if validation_errors:
        mismatches["record_validation"] = validation_errors

    for scalar_key in ("ready", "stability", "slm_seeded_plan_count"):
        if baseline.get(scalar_key) != current.get(scalar_key):
            mismatches[scalar_key] = {
                "baseline": baseline.get(scalar_key),
                "current": current.get(scalar_key),
            }

    for group_key in ("device_summary", "health_summary", "controller_states", "process_stack"):
        baseline_group = baseline.get(group_key, {})
        current_group = current.get(group_key, {})
        if not isinstance(baseline_group, dict):
            baseline_group = {}
        if not isinstance(current_group, dict):
            current_group = {}

        group_mismatches: dict[str, object] = {}
        for key in sorted(set(baseline_group) | set(current_group)):
            if baseline_group.get(key) != current_group.get(key):
                group_mismatches[key] = {
                    "baseline": baseline_group.get(key),
                    "current": current_group.get(key),
                }
        if group_mismatches:
            mismatches[group_key] = group_mismatches

    return mismatches


def _write_json(path: Path, payload: dict[str, object]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def run_boot_inventory(
    profiles: list[str],
    timeout_sec: int,
    strict: bool,
    write_baseline: bool = False,
) -> dict[str, object]:
    requested_profiles = list(profiles)
    normalized_profiles = _normalize_profiles(profiles)
    if write_baseline:
        require_strict_baseline_write(strict)
        require_exact_profile_request(requested_profiles, normalized_profiles)
    matrix_summary = run_boot_matrix(normalized_profiles, timeout_sec, strict)
    if write_baseline:
        require_trusted_matrix_source(matrix_summary, normalized_profiles)

    matrix_results = matrix_summary.get("results", [])
    prepared_results = [
        (str(result.get("profile")), build_inventory_record(result))
        for result in matrix_results
        if isinstance(result, dict)
    ]
    if write_baseline:
        for profile, current_record in prepared_results:
            require_complete_inventory_record(profile, current_record)

    ensure_dir(BOOT_INVENTORY_DIR / "current")
    if write_baseline:
        ensure_dir(BOOT_BASELINE_DIR)

    profile_results: list[dict[str, object]] = []
    failures: list[str] = []

    for profile, current_record in prepared_results:
        current_path = inventory_current_path(profile)
        _write_json(current_path, current_record)

        baseline_path = inventory_baseline_path(profile)
        baseline_before = None
        baseline_exists_before = baseline_path.exists()
        if baseline_exists_before and not write_baseline:
            baseline_before = json.loads(baseline_path.read_text(encoding="utf-8"))

        if write_baseline:
            _write_json(baseline_path, current_record)
            print_step(f"Boot inventory baseline updated -> {baseline_path}")

        baseline_after = current_record if write_baseline else baseline_before
        missing_baseline = baseline_after is None
        mismatches = {} if missing_baseline else compare_inventory_records(baseline_after, current_record)

        if missing_baseline:
            status = "missing-baseline"
            failures.append(profile)
        elif mismatches:
            status = "mismatch"
            failures.append(profile)
        else:
            status = "ok"

        profile_results.append(
            {
                "profile": profile,
                "status": status,
                "baseline_path": str(baseline_path),
                "current_path": str(current_path),
                "baseline_exists_before": baseline_exists_before,
                "baseline_written": write_baseline,
                "mismatches": mismatches,
                "current": current_record,
            }
        )

    summary = {
        "generated_unix": int(time.time()),
        "profiles_requested": normalized_profiles,
        "profile_count": len(normalized_profiles),
        "strict": strict,
        "write_baseline": write_baseline,
        "passed": len(failures) == 0,
        "results": profile_results,
        "matrix_summary_path": str(BOOT_INVENTORY_DIR.parent / "boot-matrix" / "summary.json"),
    }
    summary_path = BOOT_INVENTORY_DIR / "summary.json"
    _write_json(summary_path, summary)
    print_step(f"Boot inventory summary exported -> {summary_path}")

    if failures and strict and not write_baseline:
        joined = ", ".join(failures)
        raise ToolError(f"Boot inventory baseline mismatch: {joined}")
    return summary
