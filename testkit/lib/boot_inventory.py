from __future__ import annotations

import json
import time
from pathlib import Path

from lib.common import BUILD_DIR, REPO_ROOT, ToolError, ensure_dir, print_step
from lib.boot_matrix_lane import run_boot_matrix
from lib.kernel_lane import ensure_smoke_profile


BOOT_BASELINE_DIR = REPO_ROOT / "testkit" / "fixtures" / "boot-baseline"
BOOT_INVENTORY_DIR = BUILD_DIR / "boot-inventory"


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
    }


def compare_inventory_records(baseline: dict[str, object], current: dict[str, object]) -> dict[str, object]:
    mismatches: dict[str, object] = {}

    for scalar_key in ("ready", "stability", "slm_seeded_plan_count"):
        if baseline.get(scalar_key) != current.get(scalar_key):
            mismatches[scalar_key] = {
                "baseline": baseline.get(scalar_key),
                "current": current.get(scalar_key),
            }

    for group_key in ("device_summary", "health_summary", "controller_states"):
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
    normalized_profiles = _normalize_profiles(profiles)
    matrix_summary = run_boot_matrix(normalized_profiles, timeout_sec, strict)

    ensure_dir(BOOT_INVENTORY_DIR / "current")
    if write_baseline:
        ensure_dir(BOOT_BASELINE_DIR)

    profile_results: list[dict[str, object]] = []
    failures: list[str] = []

    matrix_results = matrix_summary.get("results", [])
    for result in matrix_results:
        if not isinstance(result, dict):
            continue

        profile = str(result.get("profile"))
        current_record = build_inventory_record(result)
        current_path = inventory_current_path(profile)
        _write_json(current_path, current_record)

        baseline_path = inventory_baseline_path(profile)
        baseline_before = None
        baseline_exists_before = baseline_path.exists()
        if baseline_exists_before:
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
    if failures and strict and write_baseline:
        # strict + write-baseline is allowed to bootstrap or refresh baselines in one run.
        print_step("Boot inventory strict check accepted because baselines were refreshed in this run")

    return summary
