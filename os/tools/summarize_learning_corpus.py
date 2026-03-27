#!/usr/bin/env python3
"""Summarize the distribution of a learning trace corpus."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                yield json.loads(line)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Trace JSONL path")
    args = parser.parse_args()

    outcomes = Counter()
    memory_types = Counter()
    accepted = Counter()
    total = 0
    importance_sum = 0

    for record in iter_jsonl(args.input):
        total += 1
        outcomes[str(record.get("outcome", "unknown"))] += 1
        memory_types[str(record.get("memory_type", "unknown"))] += 1
        accepted["accepted" if record.get("accepted", False) else "rejected"] += 1
        importance_sum += int(record.get("importance", 0))

    result = {
        "total_records": total,
        "average_importance": 0 if total == 0 else round(importance_sum / total, 2),
        "outcomes": outcomes,
        "memory_types": memory_types,
        "accepted": accepted,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
