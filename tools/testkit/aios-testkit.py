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
from lib.boot_inventory import run_boot_inventory
from lib.boot_matrix_lane import run_boot_matrix
from lib.boot_perf import run_boot_perf
from lib.kernel_lane import run_kernel_suite
from lib.os_lane import run_os_tool_suite
from lib.qemu_mcp_diagnostic import QemuMcpDiagnosticError, run_qemu_mcp_diagnostic
from lib.shell_lane import run_shell_lane


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
            "python": str(REPO_ROOT / "tools" / "testkit" / "aios-testkit.py"),
            "windows_kernel": str(REPO_ROOT / "tools" / "testkit" / "kernel" / "build-windows.ps1"),
            "make_root": str(REPO_ROOT / "Makefile"),
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
        choices=["full", "minimal", "storage-only"],
        default="full",
        help="QEMU optional-hardware profile used when the kernel lane boots a smoke VM.",
    )
    all_cmd.add_argument(
        "--cpu-profile",
        choices=["default", "max-smap"],
        default="default",
        help="QEMU CPU/security profile; max-smap maps to `-cpu max`.",
    )
    all_cmd.add_argument(
        "--export-boot-summary",
        action="store_true",
        help="When the kernel lane boots a smoke VM, export a parsed boot summary JSON under build/boot-summary/.",
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
        choices=["full", "minimal", "storage-only"],
        default="full",
        help="QEMU optional-hardware profile used when the kernel lane boots a smoke VM.",
    )
    kernel_cmd.add_argument(
        "--cpu-profile",
        choices=["default", "max-smap"],
        default="default",
        help="QEMU CPU/security profile; max-smap maps to `-cpu max`.",
    )
    kernel_cmd.add_argument(
        "--export-boot-summary",
        action="store_true",
        help="Export a parsed boot summary JSON under build/boot-summary/ after a successful smoke boot.",
    )
    kernel_cmd.add_argument("--timeout", type=int, default=DEFAULT_QEMU_TIMEOUT)
    kernel_cmd.add_argument("--strict", action="store_true")

    matrix_cmd = sub.add_parser(
        "boot-matrix",
        help="Run multiple kernel smoke profiles sequentially and export a matrix summary.",
    )
    matrix_cmd.add_argument(
        "--profiles",
        nargs="+",
        choices=["full", "minimal", "storage-only"],
        default=["full", "minimal"],
        help="Ordered smoke profiles to execute in the boot matrix.",
    )
    matrix_cmd.add_argument("--timeout", type=int, default=DEFAULT_QEMU_TIMEOUT)
    matrix_cmd.add_argument("--strict", action="store_true")

    inventory_cmd = sub.add_parser(
        "boot-inventory",
        help="Refresh boot summaries and compare compact inventory records against checked-in baselines.",
    )
    inventory_cmd.add_argument(
        "--profiles",
        nargs="+",
        choices=["full", "minimal", "storage-only"],
        default=["full", "minimal"],
        help="Ordered smoke profiles to verify against inventory baselines.",
    )
    inventory_cmd.add_argument(
        "--write-baseline",
        action="store_true",
        help="Write or refresh testkit/fixtures/boot-baseline/<profile>.json from a complete strict run (requires --strict).",
    )
    inventory_cmd.add_argument("--timeout", type=int, default=DEFAULT_QEMU_TIMEOUT)
    inventory_cmd.add_argument("--strict", action="store_true")

    perf_cmd = sub.add_parser(
        "boot-perf",
        help="Refresh boot summaries and compare host-local performance baselines with tolerance thresholds.",
    )
    perf_cmd.add_argument(
        "--profiles",
        nargs="+",
        choices=["full", "minimal", "storage-only"],
        default=["full", "minimal"],
        help="Ordered smoke profiles to verify against local boot-perf baselines.",
    )
    perf_cmd.add_argument(
        "--write-baseline",
        action="store_true",
        help="Write or refresh build/boot-perf/baseline/<profile>.json from a complete strict run (requires --strict).",
    )
    perf_cmd.add_argument("--timeout", type=int, default=DEFAULT_QEMU_TIMEOUT)
    perf_cmd.add_argument("--strict", action="store_true")

    shell_cmd = sub.add_parser(
        "shell",
        help="Boot QEMU with serial-on-stdio and drive the in-kernel shell's machine-readable state commands.",
    )
    shell_cmd.add_argument(
        "--skip-build",
        action="store_true",
        help="Reuse the existing kernel ISO instead of rebuilding first.",
    )
    shell_cmd.add_argument(
        "--cpu-profile",
        choices=["default", "max-smap"],
        default="default",
        help="QEMU CPU/security profile; max-smap maps to `-cpu max`.",
    )
    shell_cmd.add_argument("--timeout", type=int, default=DEFAULT_QEMU_TIMEOUT)
    shell_cmd.add_argument("--strict", action="store_true")

    diagnostic_cmd = sub.add_parser(
        "qemu-mcp-diagnostic",
        help="Observe one isolated qemu-mcp session; never produce a kernel PASS verdict.",
    )
    diagnostic_cmd.add_argument(
        "--mcp-server", required=True,
        help="Absolute path to a single qemu-mcp executable (no shell arguments).",
    )
    diagnostic_cmd.add_argument(
        "--skip-build", action="store_true",
        help="Reuse the existing ISO; its freshness is recorded as unknown.",
    )
    diagnostic_cmd.add_argument("--timeout", type=int, default=DEFAULT_QEMU_TIMEOUT)

    sub.add_parser("os", help="Run OS-layer tool smoke tests.")
    sub.add_parser("info", help="Print environment/toolkit info.")
    return parser.parse_args()


def lock_label(args: argparse.Namespace) -> str:
    if args.command == "kernel":
        return (
            f"kernel:{args.target}:{getattr(args, 'smoke_profile', 'full')}:"
            f"{getattr(args, 'cpu_profile', 'default')}"
        )
    if args.command == "all":
        return (
            f"all:{args.kernel_target}:{getattr(args, 'smoke_profile', 'full')}:"
            f"{getattr(args, 'cpu_profile', 'default')}"
        )
    if args.command == "boot-matrix":
        profiles = ",".join(getattr(args, "profiles", []))
        return f"boot-matrix:{profiles}"
    if args.command == "boot-inventory":
        profiles = ",".join(getattr(args, "profiles", []))
        mode = "write" if getattr(args, "write_baseline", False) else "check"
        return f"boot-inventory:{mode}:{profiles}"
    if args.command == "boot-perf":
        profiles = ",".join(getattr(args, "profiles", []))
        mode = "write" if getattr(args, "write_baseline", False) else "check"
        return f"boot-perf:{mode}:{profiles}"
    if args.command == "shell":
        mode = "reuse" if getattr(args, "skip_build", False) else "build"
        return f"shell:{mode}:{getattr(args, 'cpu_profile', 'default')}"
    if args.command == "qemu-mcp-diagnostic":
        mode = "reuse" if args.skip_build else "build"
        return f"qemu-mcp-diagnostic:{mode}"
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
                run_kernel_suite(
                    args.target,
                    args.timeout,
                    args.strict,
                    args.smoke_profile,
                    args.export_boot_summary,
                    args.cpu_profile,
                )
                return 0
            if args.command == "all":
                run_kernel_suite(
                    args.kernel_target,
                    args.timeout,
                    args.strict,
                    args.smoke_profile,
                    args.export_boot_summary,
                    args.cpu_profile,
                )
                run_os_tool_suite()
                return 0
            if args.command == "boot-matrix":
                run_boot_matrix(args.profiles, args.timeout, args.strict)
                return 0
            if args.command == "boot-inventory":
                run_boot_inventory(args.profiles, args.timeout, args.strict, args.write_baseline)
                return 0
            if args.command == "boot-perf":
                run_boot_perf(args.profiles, args.timeout, args.strict, args.write_baseline)
                return 0
            if args.command == "shell":
                run_shell_lane(
                    args.timeout,
                    args.strict,
                    args.skip_build,
                    args.cpu_profile,
                )
                return 0
            if args.command == "qemu-mcp-diagnostic":
                run_qemu_mcp_diagnostic(args.mcp_server, args.timeout, args.skip_build)
                return 0
            raise ToolError(f"Unsupported command: {args.command}")
    except QemuMcpDiagnosticError as exc:
        print(f"[AIOS] ERROR {exc}")
        return exc.exit_code
    except (ToolError, subprocess.CalledProcessError, FileNotFoundError) as exc:
        print(f"[AIOS] ERROR {exc}")
        return 2 if args.command == "qemu-mcp-diagnostic" else 1
    except (OSError, ValueError) as exc:
        if args.command != "qemu-mcp-diagnostic":
            raise
        print(f"[AIOS] ERROR diagnostic infrastructure failure: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
