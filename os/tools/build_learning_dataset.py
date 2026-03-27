#!/usr/bin/env python3
"""Build memory and adapter datasets from agent trace JSONL."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_no}: invalid JSON: {exc}") from exc


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Trace JSONL path")
    parser.add_argument("--memory-out", required=True, type=Path, help="Memory journal output JSONL")
    parser.add_argument("--adapter-out", required=True, type=Path, help="Adapter candidates output JSONL")
    parser.add_argument("--min-importance", type=int, default=60, help="Minimum importance for adapter candidates")
    args = parser.parse_args()

    memory_rows: list[dict] = []
    adapter_rows: list[dict] = []

    for record in iter_jsonl(args.input):
        memory_rows.append({
            "id": record.get("id"),
            "timestamp": record.get("timestamp"),
            "memory_type": record.get("memory_type", "episodic"),
            "importance": int(record.get("importance", 0)),
            "summary": record.get("summary", ""),
            "goal": record.get("goal", ""),
            "outcome": record.get("outcome", "unknown"),
            "accepted": bool(record.get("accepted", False)),
            "tags": record.get("tags", []),
        })

        if not record.get("accepted", False):
            continue
        if int(record.get("importance", 0)) < args.min_importance:
            continue

        adapter_rows.append({
            "instruction": record.get("instruction", record.get("goal", "")),
            "input": record.get("context", ""),
            "output": record.get("chosen_action", ""),
            "meta": {
                "trace_id": record.get("id"),
                "outcome": record.get("outcome", "unknown"),
                "importance": int(record.get("importance", 0)),
                "memory_type": record.get("memory_type", "episodic"),
            },
        })

    write_jsonl(args.memory_out, memory_rows)
    write_jsonl(args.adapter_out, adapter_rows)

    print(json.dumps({
        "memory_rows": len(memory_rows),
        "adapter_rows": len(adapter_rows),
        "memory_out": str(args.memory_out),
        "adapter_out": str(args.adapter_out),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
