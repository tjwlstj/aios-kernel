from __future__ import annotations

import json
import math
import time
from pathlib import Path

from lib.baseline_guard import (
    require_exact_profile_request,
    require_strict_baseline_write,
    require_trusted_matrix_source,
)
from lib.boot_log import boot_summary_path
from lib.boot_matrix_lane import run_boot_matrix
from lib.common import BUILD_DIR, ToolError, ensure_dir, print_step
from lib.kernel_lane import ensure_smoke_profile


BOOT_PERF_DIR = BUILD_DIR / "boot-perf"
BOOT_PERF_CURRENT_DIR = BOOT_PERF_DIR / "current"
BOOT_PERF_BASELINE_DIR = BOOT_PERF_DIR / "baseline"

PERF_RULES: dict[str, dict[str, object]] = {
    "memcpy_mib_s": {
        "kind": "min",
        "max_regression_pct": 35,
        "label": "profile memcpy throughput",
    },
    "memset_cyc_per_kib": {
        "kind": "max",
        "max_regression_pct": 45,
        "label": "memset cycles per KiB",
    },
    "memcpy_cyc_per_kib": {
        "kind": "max",
        "max_regression_pct": 45,
        "label": "memcpy cycles per KiB",
    },
    "memmove_cyc_per_kib": {
        "kind": "max",
        "max_regression_pct": 45,
        "label": "memmove cycles per KiB",
    },
    "dram_latency_x100": {
        "kind": "max",
        "max_regression_pct": 50,
        "label": "DRAM latency x100 cycles",
    },
}


def perf_current_path(profile: str) -> Path:
    return BOOT_PERF_CURRENT_DIR / f"{profile}.json"


def perf_baseline_path(profile: str) -> Path:
    return BOOT_PERF_BASELINE_DIR / f"{profile}.json"


def _normalize_profiles(profiles: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for profile in profiles:
        normalized = ensure_smoke_profile(profile)
        if normalized not in seen:
            ordered.append(normalized)
            seen.add(normalized)
    if not ordered:
        raise ToolError("boot-perf requires at least one smoke profile.")
    return ordered


def _load_boot_summary(profile: str) -> dict[str, object]:
    path = boot_summary_path("test", profile)
    if not path.exists():
        raise ToolError(f"Boot summary missing for profile `{profile}`: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def build_perf_record(profile: str, summary: dict[str, object]) -> dict[str, object]:
    selftest = summary.get("selftest") or {}
    metrics = selftest.get("metrics") or {}
    profile_summary = summary.get("profile") or {}
    cache = profile_summary.get("cache") or {}
    latency = cache.get("latency_x100") or {}

    record = {
        "profile": profile,
        "generated_unix": int(time.time()),
        "selftest_status": selftest.get("status"),
        "size_kib": selftest.get("size_kib"),
        "iterations": selftest.get("iterations"),
        "tier": profile_summary.get("tier"),
        "metrics": {
            "memcpy_mib_s": profile_summary.get("memcpy_mib_s"),
            "tsc_khz": profile_summary.get("tsc_khz"),
            "memset_cyc_per_kib": (metrics.get("memset") or {}).get("cyc_per_kib"),
            "memcpy_cyc_per_kib": (metrics.get("memcpy") or {}).get("cyc_per_kib"),
            "memmove_cyc_per_kib": (metrics.get("memmove") or {}).get("cyc_per_kib"),
            "l1_latency_x100": latency.get("l1_latency_x100"),
            "l2_latency_x100": latency.get("l2_latency_x100"),
            "l3_latency_x100": latency.get("l3_latency_x100"),
            "dram_latency_x100": latency.get("dram_latency_x100"),
        },
        "rules": PERF_RULES,
        "boot_summary_path": str(boot_summary_path("test", profile)),
    }
    return record


def _is_valid_perf_metric(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value) and value > 0
    except (OverflowError, TypeError, ValueError):
        return False


def require_complete_perf_record(profile: str, record: dict[str, object]) -> None:
    errors: list[str] = []
    if record.get("profile") != profile:
        errors.append("profile mismatch")
    if record.get("selftest_status") != "PASS":
        errors.append("selftest PASS proof missing")
    for key in ("size_kib", "iterations"):
        value = record.get(key)
        if type(value) is not int or value <= 0:
            errors.append(f"{key} missing")
    if not isinstance(record.get("tier"), str) or not record.get("tier"):
        errors.append("tier missing")

    metrics = record.get("metrics")
    if not isinstance(metrics, dict):
        errors.append("metrics missing")
    else:
        for metric_name in PERF_RULES:
            value = metrics.get(metric_name)
            if not _is_valid_perf_metric(value):
                errors.append(
                    f"metrics.{metric_name} must be a finite positive number"
                )

    if errors:
        raise ToolError(
            f"Boot perf baseline source for `{profile}` is incomplete: "
            + "; ".join(errors)
        )


def _write_json(path: Path, payload: dict[str, object]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _metric_validation_mismatch(
    name: str,
    rule: dict[str, object],
    baseline_value: object,
    current_value: object,
) -> dict[str, object] | None:
    invalid_sources: list[str] = []
    if not _is_valid_perf_metric(baseline_value):
        invalid_sources.append("baseline")
    if not _is_valid_perf_metric(current_value):
        invalid_sources.append("current")
    if not invalid_sources:
        return None
    return {
        "label": rule.get("label", name),
        "baseline": baseline_value,
        "current": current_value,
        "reason": "invalid-metric",
        "requirement": "finite-positive-number",
        "invalid_sources": invalid_sources,
    }


def _compare_metric(
    name: str,
    rule: dict[str, object],
    baseline_value: object,
    current_value: object,
) -> dict[str, object] | None:
    validation_mismatch = _metric_validation_mismatch(
        name,
        rule,
        baseline_value,
        current_value,
    )
    if validation_mismatch is not None:
        return validation_mismatch

    max_regression_pct = float(rule.get("max_regression_pct", 0))
    kind = str(rule.get("kind", "max"))

    if kind == "min":
        threshold = baseline_value * (1 - max_regression_pct / 100.0)
        breached = current_value < threshold
        delta_pct = ((baseline_value - current_value) / baseline_value) * 100.0
    else:
        threshold = baseline_value * (1 + max_regression_pct / 100.0)
        breached = current_value > threshold
        delta_pct = ((current_value - baseline_value) / baseline_value) * 100.0

    if not breached:
        return None

    return {
        "label": rule.get("label", name),
        "baseline": baseline_value,
        "current": current_value,
        "threshold": threshold,
        "delta_pct": delta_pct,
        "kind": kind,
        "max_regression_pct": max_regression_pct,
    }


def compare_perf_records(baseline: dict[str, object], current: dict[str, object]) -> dict[str, object]:
    mismatches: dict[str, object] = {}

    comparability_failed = False
    comparability_validators = {
        "profile": lambda value: isinstance(value, str) and bool(value),
        "size_kib": lambda value: type(value) is int and value > 0,
        "iterations": lambda value: type(value) is int and value > 0,
        "tier": lambda value: isinstance(value, str) and bool(value),
    }
    for scalar_key in ("profile", "size_kib", "iterations", "tier"):
        baseline_value = baseline.get(scalar_key)
        current_value = current.get(scalar_key)
        if (
            not comparability_validators[scalar_key](baseline_value)
            or not comparability_validators[scalar_key](current_value)
            or type(baseline_value) is not type(current_value)
            or baseline_value != current_value
        ):
            mismatches[scalar_key] = {
                "baseline": baseline_value,
                "current": current_value,
            }
            comparability_failed = True

    baseline_selftest = baseline.get("selftest_status")
    current_selftest = current.get("selftest_status")
    selftest_failed = baseline_selftest != "PASS" or current_selftest != "PASS"
    if selftest_failed:
        mismatches["selftest_status"] = {
            "baseline": baseline_selftest,
            "current": current_selftest,
            "expected": "PASS",
            "reason": "selftest-not-pass",
        }

    baseline_metrics = baseline.get("metrics", {})
    current_metrics = current.get("metrics", {})
    if not isinstance(baseline_metrics, dict):
        baseline_metrics = {}
    if not isinstance(current_metrics, dict):
        current_metrics = {}

    metric_mismatches: dict[str, object] = {}
    invalid_metrics = False
    for name, rule in PERF_RULES.items():
        validation_mismatch = _metric_validation_mismatch(
            name,
            rule,
            baseline_metrics.get(name),
            current_metrics.get(name),
        )
        if validation_mismatch is not None:
            metric_mismatches[name] = validation_mismatch
            invalid_metrics = True
    if metric_mismatches:
        mismatches["metrics"] = metric_mismatches

    # Thresholds are meaningful only when both records contain valid PASS
    # evidence for the same test shape and classification. TSC frequency stays
    # contextual because host/QEMU calibration varies between otherwise
    # comparable runs, so it is intentionally not an exact gate.
    if comparability_failed or selftest_failed or invalid_metrics:
        return mismatches

    for name, rule in PERF_RULES.items():
        breach = _compare_metric(name, rule, baseline_metrics.get(name), current_metrics.get(name))
        if breach is not None:
            metric_mismatches[name] = breach
    if metric_mismatches:
        mismatches["metrics"] = metric_mismatches

    return mismatches


def run_boot_perf(
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

    boot_summaries = {
        profile: _load_boot_summary(profile)
        for profile in normalized_profiles
    }
    current_records = {
        profile: build_perf_record(profile, boot_summaries[profile])
        for profile in normalized_profiles
    }
    if write_baseline:
        for profile, current_record in current_records.items():
            require_complete_perf_record(profile, current_record)

    ensure_dir(BOOT_PERF_CURRENT_DIR)
    if write_baseline:
        ensure_dir(BOOT_PERF_BASELINE_DIR)

    profile_results: list[dict[str, object]] = []
    failures: list[str] = []

    for profile in normalized_profiles:
        current_record = current_records[profile]
        current_path = perf_current_path(profile)
        _write_json(current_path, current_record)

        baseline_path = perf_baseline_path(profile)
        baseline_before = None
        baseline_exists_before = baseline_path.exists()
        if baseline_exists_before and not write_baseline:
            baseline_before = json.loads(baseline_path.read_text(encoding="utf-8"))

        if write_baseline:
            _write_json(baseline_path, current_record)
            print_step(f"Boot perf baseline updated -> {baseline_path}")

        baseline_after = current_record if write_baseline else baseline_before
        missing_baseline = baseline_after is None
        mismatches = {} if missing_baseline else compare_perf_records(baseline_after, current_record)

        if missing_baseline:
            status = "missing-baseline"
            failures.append(profile)
        elif mismatches:
            status = "regression"
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

    summary_payload = {
        "generated_unix": int(time.time()),
        "profiles_requested": normalized_profiles,
        "profile_count": len(normalized_profiles),
        "strict": strict,
        "write_baseline": write_baseline,
        "passed": len(failures) == 0,
        "baseline_mode": "local-build-dir",
        "results": profile_results,
    }
    summary_path = BOOT_PERF_DIR / "summary.json"
    _write_json(summary_path, summary_payload)
    print_step(f"Boot perf summary exported -> {summary_path}")

    if failures and strict and not write_baseline:
        joined = ", ".join(failures)
        raise ToolError(f"Boot perf regression detected: {joined}")
    return summary_payload
