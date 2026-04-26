from __future__ import annotations

import json
import time
from pathlib import Path

from lib.boot_log import boot_summary_path
from lib.common import BUILD_DIR, ToolError, ensure_dir, print_step
from lib.kernel_lane import ensure_smoke_profile, run_kernel_suite


def _normalize_profiles(profiles: list[str]) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()
    for profile in profiles:
        normalized = ensure_smoke_profile(profile)
        if normalized not in seen:
            ordered.append(normalized)
            seen.add(normalized)
    if not ordered:
        raise ToolError("boot-matrix requires at least one smoke profile.")
    return ordered


def _device_delta(base: dict[str, object], current: dict[str, object]) -> dict[str, int]:
    delta: dict[str, int] = {}
    for key in ("pci", "matched", "eth", "wifi", "bt", "usb", "storage"):
        base_value = base.get(key)
        current_value = current.get(key)
        if isinstance(base_value, int) and isinstance(current_value, int):
            difference = current_value - base_value
            if difference != 0:
                delta[key] = difference
    return delta


def _controller_state_delta(base: dict[str, str], current: dict[str, str]) -> dict[str, dict[str, str]]:
    delta: dict[str, dict[str, str]] = {}
    for key in sorted(set(base) | set(current)):
        base_value = base.get(key, "unknown")
        current_value = current.get(key, "unknown")
        if base_value != current_value:
            delta[key] = {"baseline": base_value, "current": current_value}
    return delta


def _compact_result(profile: str, summary: dict[str, object], matrix_summary_path: Path) -> dict[str, object]:
    checkpoints = summary.get("checkpoints", {})
    health = summary.get("health") or {}
    device_summary = summary.get("device_summary") or {}
    controllers = summary.get("controllers") or {}
    slm = summary.get("slm") or {}

    controller_states = {}
    for name, payload in controllers.items():
        if isinstance(payload, dict):
            controller_states[name] = payload.get("state", "unknown")

    shell = summary.get("shell") or {}
    return {
        "profile": profile,
        "ready": bool(checkpoints.get("ready", {}).get("seen")),
        "shell_started": bool(shell.get("started")),
        "stability": health.get("stability"),
        "health_summary": {
            key: health.get(key)
            for key in ("ok", "degraded", "failed", "unknown", "io_degraded", "req_fail", "autonomy", "risky_io")
            if key in health
        },
        "device_summary": {
            key: device_summary.get(key)
            for key in ("pci", "matched", "eth", "wifi", "bt", "usb", "storage")
            if key in device_summary
        },
        "controller_states": controller_states,
        "slm_seeded_plan_count": slm.get("seeded_plan_count"),
        "required_patterns": summary.get("required_patterns", []),
        "missing_patterns": summary.get("missing_patterns", []),
        "boot_summary_path": str(boot_summary_path("test", profile)),
        "matrix_summary_path": str(matrix_summary_path),
    }


def run_boot_matrix(profiles: list[str], timeout_sec: int, strict: bool) -> dict[str, object]:
    normalized_profiles = _normalize_profiles(profiles)
    matrix_dir = BUILD_DIR / "boot-matrix"
    ensure_dir(matrix_dir)

    compact_results: list[dict[str, object]] = []

    for profile in normalized_profiles:
        print_step(f"Boot matrix profile start -> {profile}")
        summary = run_kernel_suite("test", timeout_sec, strict, profile, export_boot_summary=True)
        if summary is None:
            raise ToolError(f"Boot matrix did not receive a summary for profile `{profile}`.")

        matrix_profile_path = matrix_dir / f"{profile}.json"
        matrix_profile_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

        compact_results.append(_compact_result(profile, summary, matrix_profile_path))
        print_step(f"Boot matrix profile done -> {profile}")

    comparisons: list[dict[str, object]] = []
    baseline = compact_results[0]
    for current in compact_results[1:]:
        comparisons.append(
            {
                "profile": current["profile"],
                "baseline_profile": baseline["profile"],
                "device_delta": _device_delta(
                    baseline.get("device_summary", {}),
                    current.get("device_summary", {}),
                ),
                "controller_state_delta": _controller_state_delta(
                    baseline.get("controller_states", {}),
                    current.get("controller_states", {}),
                ),
                "slm_seeded_plan_delta": (
                    (current.get("slm_seeded_plan_count") or 0)
                    - (baseline.get("slm_seeded_plan_count") or 0)
                ),
            }
        )

    summary_payload = {
        "generated_unix": int(time.time()),
        "profiles_requested": normalized_profiles,
        "profile_count": len(normalized_profiles),
        "baseline_profile": baseline["profile"],
        "passed": all(
            result.get("ready") and result.get("shell_started") and not result.get("missing_patterns")
            for result in compact_results
        ),
        "results": compact_results,
        "comparisons_to_baseline": comparisons,
    }
    summary_path = matrix_dir / "summary.json"
    summary_path.write_text(json.dumps(summary_payload, indent=2), encoding="utf-8")
    print_step(f"Boot matrix summary exported -> {summary_path}")
    return summary_payload
