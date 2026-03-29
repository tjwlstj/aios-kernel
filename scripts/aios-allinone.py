#!/usr/bin/env python3
"""
AIOS all-in-one build and test tool.

Unifies:
- kernel build / ISO / smoke test
- OS-layer sample tool validation
- host compatibility handling for Windows/Linux/macOS
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Iterable


# Preserve the user-visible drive path on Windows instead of resolving to UNC.
REPO_ROOT = Path(os.path.abspath(__file__)).parents[1]
BUILD_DIR = REPO_ROOT / "build"
SERIAL_LOG = BUILD_DIR / "serial_output.log"
TOOL_SMOKE_DIR = BUILD_DIR / "tool-smoke"
DEFAULT_QEMU_TIMEOUT = 20


class ToolError(RuntimeError):
    pass


def host_name() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def shell_join(cmd: Iterable[str]) -> str:
    return subprocess.list2cmdline(list(cmd))


def print_step(message: str) -> None:
    print(f"[AIOS] {message}")


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def which_any(*names: str) -> str | None:
    for name in names:
        resolved = shutil.which(name)
        if resolved:
            return resolved
    return None


def run(
    cmd: list[str],
    *,
    cwd: Path = REPO_ROOT,
    env: dict[str, str] | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    print_step(f"RUN {shell_join(cmd)}")
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        check=True,
        text=True,
        capture_output=capture,
    )


def run_kernel_make(target: str) -> None:
    make = which_any("make")
    if not make:
        raise ToolError("`make` not found on PATH for kernel build.")
    run([make, target])


def run_windows_kernel(target: str) -> None:
    powershell = which_any("pwsh", "powershell")
    if not powershell:
        raise ToolError("PowerShell (`pwsh` or `powershell`) not found.")
    script = REPO_ROOT / "scripts" / "build-windows.ps1"
    run([powershell, "-ExecutionPolicy", "Bypass", "-File", str(script), "-Target", target])


def run_qemu_smoke_test(timeout_sec: int, strict: bool) -> None:
    qemu = which_any("qemu-system-x86_64")
    if not qemu:
        if strict:
            raise ToolError("`qemu-system-x86_64` is required for kernel smoke testing.")
        print_step("SKIP kernel smoke: qemu-system-x86_64 not found")
        return

    iso = REPO_ROOT / "build" / "aios-kernel.iso"
    if not iso.exists():
        raise ToolError(f"Kernel ISO not found: {iso}")

    if SERIAL_LOG.exists():
        SERIAL_LOG.unlink()

    cmd = [
        qemu,
        "-cdrom",
        str(iso),
        "-boot",
        "d",
        "-m",
        "256M",
        "-nic",
        "user,model=e1000",
        "-device",
        "qemu-xhci",
        "-serial",
        f"file:{SERIAL_LOG}",
        "-display",
        "none",
        "-no-reboot",
        "-no-shutdown",
    ]

    print_step(f"RUN {shell_join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT))
    deadline = time.time() + timeout_sec

    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.25)

    if proc.poll() is None:
        proc.kill()
        proc.wait()

    if not SERIAL_LOG.exists():
        raise ToolError("Smoke test did not produce a serial log.")
    if SERIAL_LOG.stat().st_size == 0:
        raise ToolError("Smoke test produced an empty serial log.")

    log_text = SERIAL_LOG.read_text(encoding="utf-8", errors="replace")
    required_patterns = [
        "AIOS Kernel Ready",
        "[SELFTEST] Memory microbench PASS",
        "[DEV] Peripheral probe ready",
        "[HEALTH] stability=",
    ]
    missing = [pattern for pattern in required_patterns if pattern not in log_text]
    if missing:
        tail = "\n".join(log_text.splitlines()[-40:])
        raise ToolError(
            "Kernel smoke test did not reach expected state. "
            f"Missing={missing}\nLast log lines:\n{tail}"
        )

    print_step("Kernel smoke test PASSED")


def run_kernel_suite(target: str, timeout_sec: int, strict: bool) -> None:
    host = host_name()
    if host == "windows":
        run_windows_kernel(target)
        return

    if target == "clean":
        run_kernel_make("clean")
        return
    if target == "info":
        run_kernel_make("info")
        return
    if target == "all":
        run_kernel_make("all")
        return
    if target == "iso":
        run_kernel_make("all")
        run_kernel_make("iso")
        return
    if target == "test":
        run_kernel_make("all")
        run_kernel_make("iso")
        run_qemu_smoke_test(timeout_sec, strict)
        return

    raise ToolError(f"Unsupported kernel target: {target}")


def run_os_tool_suite() -> dict[str, object]:
    ensure_dir(TOOL_SMOKE_DIR)

    score_json = TOOL_SMOKE_DIR / "static-chaos-score.json"
    memory_out = TOOL_SMOKE_DIR / "memory_journal.jsonl"
    adapter_out = TOOL_SMOKE_DIR / "adapter_candidates.jsonl"

    score_script = REPO_ROOT / "os" / "tools" / "score_static_chaos.py"
    dataset_script = REPO_ROOT / "os" / "tools" / "build_learning_dataset.py"
    summary_script = REPO_ROOT / "os" / "tools" / "summarize_learning_corpus.py"

    metrics = REPO_ROOT / "os" / "examples" / "static_chaos_metrics.sample.json"
    profile = REPO_ROOT / "os" / "main_ai" / "config" / "main_ai_profile.example.json"
    trace = REPO_ROOT / "os" / "examples" / "learning_trace.sample.jsonl"
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
        "wit_scaffold": True,
    }

    summary_json = TOOL_SMOKE_DIR / "summary.json"
    summary_json.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print_step(f"OS tool smoke PASSED -> {summary_json}")
    return result


def print_info() -> None:
    info = {
        "repo_root": str(REPO_ROOT),
        "host": host_name(),
        "python": sys.executable,
        "make": which_any("make"),
        "powershell": which_any("pwsh", "powershell"),
        "qemu": which_any("qemu-system-x86_64"),
    }
    print(json.dumps(info, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="AIOS all-in-one build and test tool."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    all_cmd = sub.add_parser("all", help="Run kernel suite and OS tool suite.")
    all_cmd.add_argument(
        "--kernel-target",
        choices=["all", "iso", "test"],
        default="test",
        help="Kernel target used by `all`.",
    )
    all_cmd.add_argument("--timeout", type=int, default=DEFAULT_QEMU_TIMEOUT)
    all_cmd.add_argument("--strict", action="store_true")

    kernel_cmd = sub.add_parser("kernel", help="Run kernel-only build/test flow.")
    kernel_cmd.add_argument(
        "--target",
        choices=["all", "iso", "test", "clean", "info"],
        default="test",
    )
    kernel_cmd.add_argument("--timeout", type=int, default=DEFAULT_QEMU_TIMEOUT)
    kernel_cmd.add_argument("--strict", action="store_true")

    sub.add_parser("os", help="Run OS-layer tool smoke tests.")
    sub.add_parser("info", help="Print environment/tool info.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "info":
            print_info()
            return 0
        if args.command == "os":
            run_os_tool_suite()
            return 0
        if args.command == "kernel":
            run_kernel_suite(args.target, args.timeout, args.strict)
            return 0
        if args.command == "all":
            run_kernel_suite(args.kernel_target, args.timeout, args.strict)
            run_os_tool_suite()
            return 0
        raise ToolError(f"Unsupported command: {args.command}")
    except (ToolError, subprocess.CalledProcessError, FileNotFoundError) as exc:
        print_step(f"ERROR {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
