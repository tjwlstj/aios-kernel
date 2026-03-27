#!/usr/bin/env python3
"""Compute the static-chaos operator for the main AI."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def load_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def weighted_average(values: dict[str, float], weights: dict[str, float]) -> float:
    total_weight = 0.0
    total_value = 0.0
    for key, weight in weights.items():
        total_weight += weight
        total_value += float(values.get(key, 0.0)) * weight
    return 0.0 if total_weight == 0.0 else total_value / total_weight


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--metrics", required=True, type=Path, help="Metrics JSON path")
    parser.add_argument("--profile", required=True, type=Path, help="Main AI profile JSON path")
    parser.add_argument("--output", type=Path, help="Optional output JSON path")
    args = parser.parse_args()

    metrics = load_json(args.metrics)
    profile = load_json(args.profile)

    operator = profile["operator"]
    static_score = weighted_average(metrics, operator["static_weights"])
    chaos_score = weighted_average(metrics, operator["chaos_weights"])
    sco = chaos_score - static_score

    thresholds = operator["mode_thresholds"]
    if sco <= thresholds["stabilize_max_sco"]:
        mode = "stabilize"
    elif sco >= thresholds["explore_min_sco"]:
        mode = "explore"
    else:
        mode = "balance"

    result = {
        "profile_name": profile.get("profile_name", "unknown"),
        "static_score": round(static_score, 4),
        "chaos_score": round(chaos_score, 4),
        "sco": round(sco, 4),
        "mode": mode,
        "budget": profile.get("budgets", {}).get(mode, {}),
    }

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
