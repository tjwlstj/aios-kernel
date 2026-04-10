from __future__ import annotations

import subprocess
import time

from lib.common import (
    DEFAULT_QEMU_TIMEOUT,
    REPO_ROOT,
    SERIAL_LOG,
    ToolError,
    host_name,
    print_step,
    run,
    shell_join,
    which_any,
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
    script = REPO_ROOT / "testkit" / "kernel" / "build-windows.ps1"
    run([powershell, "-ExecutionPolicy", "Bypass", "-File", str(script), "-Target", target, "-SkipLock"])


def run_qemu_smoke_test(timeout_sec: int = DEFAULT_QEMU_TIMEOUT, strict: bool = False) -> None:
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
