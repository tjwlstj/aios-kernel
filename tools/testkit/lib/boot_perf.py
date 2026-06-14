from __future__ import annotations

import json
import time
from pathlib import Path

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


def _write_json(path: Path, payload: dict[str, object]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _compare_metric(
    name: str,
    rule: dict[str, object],
    baseline_value: object,
    current_value: object,
) -> dict[str, object] | None:
    if not isinstance(baseline_value, (int, float)) or not isinstance(current_value, (int, float)):
        return {
            "label": rule.get("label", name),
            "baseline": baseline_value,
            "current": current_value,
            "reason": "missing-or-non-numeric",
        }

    max_regression_pct = float(rule.get("max_regression_pct", 0))
    kind = str(rule.get("kind", "max"))

    if baseline_value == 0:
        breached = current_value != 0
        threshold = 0
        delta_pct = None
    elif kind == "min":
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

    if baseline.get("selftest_status") != current.get("selftest_status"):
        mismatches["selftest_status"] = {
            "baseline": baseline.get("selftest_status"),
            "current": current.get("selftest_status"),
        }

    baseline_metrics = baseline.get("metrics", {})
    current_metrics = current.get("metrics", {})
    if not isinstance(baseline_metrics, dict):
        baseline_metrics = {}
    if not isinstance(current_metrics, dict):
        current_metrics = {}

    metric_breaches: dict[str, object] = {}
    for name, rule in PERF_RULES.items():
        breach = _compare_metric(name, rule, baseline_metrics.get(name), current_metrics.get(name))
        if breach is not None:
            metric_breaches[name] = breach
    if metric_breaches:
        mismatches["metrics"] = metric_breaches

    return mismatches


def run_boot_perf(
    profiles: list[str],
    timeout_sec: int,
    strict: bool,
    write_baseline: bool = False,
) -> dict[str, object]:
    normalized_profiles = _normalize_profiles(profiles)
    run_boot_matrix(normalized_profiles, timeout_sec, strict)

    ensure_dir(BOOT_PERF_CURRENT_DIR)
    if write_baseline:
        ensure_dir(BOOT_PERF_BASELINE_DIR)

    profile_results: list[dict[str, object]] = []
    failures: list[str] = []

    for profile in normalized_profiles:
        summary = _load_boot_summary(profile)
        current_record = build_perf_record(profile, summary)
        current_path = perf_current_path(profile)
        _write_json(current_path, current_record)

        baseline_path = perf_baseline_path(profile)
        baseline_before = None
        baseline_exists_before = baseline_path.exists()
        if baseline_exists_before:
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
    if failures and strict and write_baseline:
        print_step("Boot perf strict check accepted because baselines were refreshed in this run")

    return summary_payload
