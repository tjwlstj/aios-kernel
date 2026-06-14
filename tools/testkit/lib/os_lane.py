from __future__ import annotations

import json
import sys

from lib.common import REPO_ROOT, TOOL_SMOKE_DIR, ToolError, ensure_dir, host_name, print_step, run


def run_os_tool_suite() -> dict[str, object]:
    ensure_dir(TOOL_SMOKE_DIR)

    score_json = TOOL_SMOKE_DIR / "static-chaos-score.json"
    memory_out = TOOL_SMOKE_DIR / "memory_journal.jsonl"
    adapter_out = TOOL_SMOKE_DIR / "adapter_candidates.jsonl"
    nodebit_out = TOOL_SMOKE_DIR / "nodebit-policy.json"

    score_script = REPO_ROOT / "os" / "tools" / "score_static_chaos.py"
    dataset_script = REPO_ROOT / "os" / "tools" / "build_learning_dataset.py"
    summary_script = REPO_ROOT / "os" / "tools" / "summarize_learning_corpus.py"
    nodebit_script = REPO_ROOT / "os" / "tools" / "evaluate_nodebit_policy.py"

    metrics = REPO_ROOT / "os" / "examples" / "static_chaos_metrics.sample.json"
    profile = REPO_ROOT / "os" / "main_ai" / "config" / "main_ai_profile.example.json"
    trace = REPO_ROOT / "os" / "examples" / "learning_trace.sample.jsonl"
    nodebit_catalog = REPO_ROOT / "os" / "examples" / "nodebit_catalog.sample.json"
    wit = REPO_ROOT / "os" / "compat" / "wit" / "aios-agent-host.wit"

    run(
        [
            sys.executable,
            str(score_script),
            "--metrics",
            str(metrics),
            "--profile",
            str(profile),
            "--output",
            str(score_json),
        ]
    )
    score_payload = json.loads(score_json.read_text(encoding="utf-8"))
    if "mode" not in score_payload:
        raise ToolError("static-chaos score output missing `mode`.")

    run(
        [
            sys.executable,
            str(dataset_script),
            "--input",
            str(trace),
            "--memory-out",
            str(memory_out),
            "--adapter-out",
            str(adapter_out),
        ]
    )
    if not memory_out.exists() or memory_out.stat().st_size == 0:
        raise ToolError("memory_journal output was not generated.")
    if not adapter_out.exists() or adapter_out.stat().st_size == 0:
        raise ToolError("adapter_candidates output was not generated.")

    summary_result = run(
        [
            sys.executable,
            str(summary_script),
            "--input",
            str(trace),
        ],
        capture=True,
    )
    summary_payload = json.loads(summary_result.stdout)
    if "total_records" not in summary_payload:
        raise ToolError("learning corpus summary missing `total_records`.")

    run(
        [
            sys.executable,
            str(nodebit_script),
            "--catalog",
            str(nodebit_catalog),
            "--node-id",
            "4",
            "--action",
            "SLM_ACTION_REPROBE_PCI",
            "--mode",
            "observe",
            "--output",
            str(nodebit_out),
        ]
    )
    nodebit_payload = json.loads(nodebit_out.read_text(encoding="utf-8"))
    if nodebit_payload.get("decision") != "allow":
        raise ToolError("NodeBit policy sample did not allow discovery observe action.")
    if nodebit_payload.get("node_id") != 4:
        raise ToolError("NodeBit policy sample resolved an unexpected node.")

    wit_text = wit.read_text(encoding="utf-8")
    if "package aios:agent-host@0.1.0;" not in wit_text or "world main-ai" not in wit_text:
        raise ToolError("WIT compatibility scaffold is missing expected declarations.")

    result = {
        "host": host_name(),
        "score_mode": score_payload["mode"],
        "max_active_workers": score_payload["budget"]["max_active_workers"],
        "memory_rows": sum(1 for _ in memory_out.open("r", encoding="utf-8")),
        "adapter_rows": sum(1 for _ in adapter_out.open("r", encoding="utf-8")),
        "total_records": summary_payload["total_records"],
        "nodebit_decision": nodebit_payload["decision"],
        "nodebit_node": nodebit_payload["node_name"],
        "wit_scaffold": True,
    }

    summary_json = TOOL_SMOKE_DIR / "summary.json"
    summary_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print_step(f"OS tool smoke PASSED -> {summary_json}")
    return result
