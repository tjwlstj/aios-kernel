#!/usr/bin/env python3
"""
AIOS test toolkit entrypoint.

The test toolkit is intentionally split into:
- kernel lane
- os lane
- shared host/tool/lock helpers

It also guards the shared build directory so parallel invocations do not
fight over the same object files or ISO outputs.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from contextlib import nullcontext

from lib.common import (
    BUILD_DIR,
    DEFAULT_QEMU_TIMEOUT,
    LOCK_DIR,
    REPO_ROOT,
    BuildLock,
    ToolError,
    host_name,
    which_any,
)
from lib.kernel_lane import run_kernel_suite
from lib.os_lane import run_os_tool_suite


def print_info() -> None:
    info = {
        "repo_root": str(REPO_ROOT),
        "host": host_name(),
        "python": __import__("sys").executable,
        "make": which_any("make"),
        "powershell": which_any("pwsh", "powershell"),
        "qemu": which_any("qemu-system-x86_64"),
        "build_dir": str(BUILD_DIR),
        "lock_dir": str(LOCK_DIR),
        "entrypoints": {
            "python": str(REPO_ROOT / "testkit" / "aios-testkit.py"),
            "windows_kernel": str(REPO_ROOT / "testkit" / "kernel" / "build-windows.ps1"),
            "compat_python": str(REPO_ROOT / "scripts" / "aios-allinone.py"),
            "compat_windows": str(REPO_ROOT / "scripts" / "build-windows.ps1"),
        },
    }
    print(json.dumps(info, indent=2))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AIOS modular test toolkit.")
    sub = parser.add_subparsers(dest="command", required=True)

    all_cmd = sub.add_parser("all", help="Run kernel lane and OS lane sequentially.")
    all_cmd.add_argument(
        "--kernel-target",
        choices=["all", "iso", "test"],
        default="test",
        help="Kernel target used by `all`.",
    )
    all_cmd.add_argument(
        "--smoke-profile",
        choices=["full", "minimal"],
        default="full",
        help="QEMU optional-hardware profile used when the kernel lane boots a smoke VM.",
    )
    all_cmd.add_argument("--timeout", type=int, default=DEFAULT_QEMU_TIMEOUT)
    all_cmd.add_argument("--strict", action="store_true")

    kernel_cmd = sub.add_parser("kernel", help="Run kernel-only build/test flow.")
    kernel_cmd.add_argument(
        "--target",
        choices=["all", "iso", "test", "clean", "info"],
        default="test",
    )
    kernel_cmd.add_argument(
        "--smoke-profile",
        choices=["full", "minimal"],
        default="full",
        help="QEMU optional-hardware profile used when the kernel lane boots a smoke VM.",
    )
    kernel_cmd.add_argument("--timeout", type=int, default=DEFAULT_QEMU_TIMEOUT)
    kernel_cmd.add_argument("--strict", action="store_true")

    sub.add_parser("os", help="Run OS-layer tool smoke tests.")
    sub.add_parser("info", help="Print environment/toolkit info.")
    return parser.parse_args()


def lock_label(args: argparse.Namespace) -> str:
    if args.command == "kernel":
        return f"kernel:{args.target}:{getattr(args, 'smoke_profile', 'full')}"
    if args.command == "all":
        return f"all:{args.kernel_target}:{getattr(args, 'smoke_profile', 'full')}"
    return args.command


def main() -> int:
    args = parse_args()
    try:
        lock = nullcontext() if args.command == "info" else BuildLock(lock_label(args))
        with lock:
            if args.command == "info":
                print_info()
                return 0
            if args.command == "os":
                run_os_tool_suite()
                return 0
            if args.command == "kernel":
                run_kernel_suite(args.target, args.timeout, args.strict, args.smoke_profile)
                return 0
            if args.command == "all":
                run_kernel_suite(args.kernel_target, args.timeout, args.strict, args.smoke_profile)
                run_os_tool_suite()
                return 0
            raise ToolError(f"Unsupported command: {args.command}")
    except (ToolError, subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"[AIOS] ERROR {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
