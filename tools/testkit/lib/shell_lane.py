"""Interactive kernel-shell smoke lane.

Boots the kernel in QEMU with the serial port bound to stdio, waits for the
in-kernel shell to come up, then drives the machine-oriented shell commands
(`ping`, `state <topic>`) and asserts on their single-line `[STATE]`
responses. This gives the host-side harness (or an AI agent) a structured,
scriptable observation channel into the *running* kernel, not just a boot
log to grep after the fact.

Artifacts (under kernel/build/shell-smoke/):
- transcript.log — full serial conversation including the boot log
- summary.json   — per-exchange pass/fail plus overall verdict
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time

from lib.common import (
    BUILD_DIR,
    DEFAULT_QEMU_TIMEOUT,
    REPO_ROOT,
    ToolError,
    ensure_dir,
    host_name,
    print_step,
    shell_join,
    which_any,
)
from lib.kernel_lane import (
    build_qemu_smoke_command,
    run_kernel_make,
    run_windows_kernel,
)

SHELL_SMOKE_DIR = BUILD_DIR / "shell-smoke"
SHELL_READY_MARKER = "[SHELL] Interactive shell started"

# Well-known Windows install locations (mirrors build-windows.ps1).
QEMU_FALLBACK_PATHS = [
    r"C:\Program Files\qemu\qemu-system-x86_64.exe",
    r"C:\Program Files\QEMU\qemu-system-x86_64.exe",
]


def find_qemu() -> str | None:
    resolved = which_any("qemu-system-x86_64")
    if resolved:
        return resolved
    for candidate in QEMU_FALLBACK_PATHS:
        if os.path.exists(candidate):
            return candidate
    return None
COMMAND_TIMEOUT_SEC = 15
REBOOT_EXIT_TIMEOUT_SEC = 10

# Each exchange: send `command`, then require every `expect` substring to
# appear in output produced *after* the command was sent.
DEFAULT_EXCHANGES: list[dict[str, object]] = [
    {"command": "ping", "expect": ["[STATE] pong ticks="]},
    {"command": "state list", "expect": ["[STATE] topics list="]},
    {"command": "state health", "expect": ["[STATE] health stability=", "autonomy="]},
    {"command": "state mem", "expect": ["[STATE] mem heap_total=", "heap_free=", "lock_acquires="]},
    {"command": "state sched", "expect": ["[STATE] sched kthread_switches=", "preempt_ticks=", "address_space_switches=", "address_space_ready=1", "user_leaf_slots=2", "user_leaf_isolated=1", "total_tasks="]},
    {"command": "state pipeline", "expect": ["[STATE] pipeline active=", "executions="]},
    {"command": "state nodes", "expect": ["[STATE] nodes active=", "[STATE] node id=40"]},
    {"command": "state slm", "expect": ["[STATE] slm apply_ok=1", "tsc_khz="]},
    {"command": "state user", "expect": ["[STATE] user attempted=1", "elf_loaded=1", "entered=1 returned=1 syscall_ok=1", "boundary_ok=1", "exit_code=42"]},
    {"command": "state sec", "expect": ["[STATE] sec nx=", "canary=1"]},
    {"command": "state time", "expect": ["[STATE] time ticks=", "hz="]},
    {"command": "state version", "expect": ["[STATE] version release="]},
    {"command": "state bogus", "expect": ["[STATE] error reason=unknown-topic"]},
]


class SerialSession:
    """Line-agnostic pump over a QEMU `-serial stdio` process."""

    def __init__(self, proc: subprocess.Popen) -> None:
        self.proc = proc
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        fd = self.proc.stdout.fileno()
        while True:
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                return
            if not chunk:
                return
            with self._lock:
                self._buf.extend(chunk)

    def text(self) -> str:
        with self._lock:
            return self._buf.decode("utf-8", errors="replace")

    def cursor(self) -> int:
        with self._lock:
            return len(self._buf.decode("utf-8", errors="replace"))

    def wait_for(self, needles: list[str], timeout_sec: float, start_at: int = 0) -> bool:
        deadline = time.time() + timeout_sec
        while True:
            window = self.text()[start_at:]
            if all(needle in window for needle in needles):
                return True
            if self.proc.poll() is not None or time.time() >= deadline:
                return False
            time.sleep(0.05)

    def send_line(self, line: str) -> None:
        # Pace bytes out one at a time: the guest drains its 16-byte UART
        # FIFO on a 100 Hz polling loop, so a long burst can overflow the
        # FIFO and silently drop the trailing newline.
        for byte in (line + "\n").encode("ascii"):
            self.proc.stdin.write(bytes([byte]))
            self.proc.stdin.flush()
            time.sleep(0.002)


def build_kernel_iso(timeout_sec: int) -> None:
    if host_name() == "windows":
        run_windows_kernel("iso", "minimal", timeout_sec)
        return
    run_kernel_make("all")
    run_kernel_make("iso")


def run_shell_lane(
    timeout_sec: int = DEFAULT_QEMU_TIMEOUT,
    strict: bool = False,
    skip_build: bool = False,
) -> dict[str, object]:
    qemu = find_qemu()
    if not qemu:
        if strict:
            raise ToolError("`qemu-system-x86_64` is required for the shell lane.")
        print_step("SKIP shell lane: qemu-system-x86_64 not found")
        return {"skipped": True, "reason": "qemu-system-x86_64 not found"}

    if not skip_build:
        build_kernel_iso(timeout_sec)

    iso = BUILD_DIR / "aios-kernel.iso"
    if not iso.exists():
        raise ToolError(f"Kernel ISO not found: {iso}")

    ensure_dir(SHELL_SMOKE_DIR)
    transcript_path = SHELL_SMOKE_DIR / "transcript.log"
    summary_path = SHELL_SMOKE_DIR / "summary.json"

    # Minimal hardware profile: the lane exercises the shell, not drivers.
    # Drop -no-shutdown so the final `reboot` (with -no-reboot) makes QEMU
    # exit instead of pausing, giving the lane a clean teardown.
    cmd = [arg for arg in build_qemu_smoke_command(qemu, str(iso), "stdio", "minimal")
           if arg != "-no-shutdown"]
    print_step(f"RUN {shell_join(cmd)}")

    proc = subprocess.Popen(
        cmd,
        cwd=str(REPO_ROOT),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    session = SerialSession(proc)

    summary: dict[str, object] = {
        "lane": "shell",
        "qemu": qemu,
        "ready": False,
        "exchanges": [],
        "clean_exit": False,
        "passed": False,
    }
    failures: list[str] = []

    try:
        if not session.wait_for([SHELL_READY_MARKER], timeout_sec):
            raise ToolError(
                f"Kernel shell did not come up within {timeout_sec}s "
                f"(missing marker: {SHELL_READY_MARKER})"
            )
        summary["ready"] = True

        for exchange in DEFAULT_EXCHANGES:
            command = str(exchange["command"])
            expect = list(exchange["expect"])  # type: ignore[arg-type]
            cursor = session.cursor()
            session.send_line(command)
            ok = session.wait_for(expect, COMMAND_TIMEOUT_SEC, start_at=cursor)
            response = session.text()[cursor:]
            summary["exchanges"].append(
                {
                    "command": command,
                    "expect": expect,
                    "ok": ok,
                    "response_excerpt": response.strip().splitlines()[-3:],
                }
            )
            if ok:
                print_step(f"shell exchange OK   `{command}`")
            else:
                failures.append(command)
                print_step(f"shell exchange FAIL `{command}` (missing {expect})")

        # `reboot` + QEMU -no-reboot => guest reset exits QEMU: clean teardown.
        session.send_line("reboot")
        deadline = time.time() + REBOOT_EXIT_TIMEOUT_SEC
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        summary["clean_exit"] = proc.poll() is not None
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        transcript_path.write_text(session.text(), encoding="utf-8")

    summary["passed"] = summary["ready"] and not failures
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print_step(f"Shell lane artifacts -> {SHELL_SMOKE_DIR}")

    if failures:
        raise ToolError(
            f"Shell lane failed exchanges: {failures} (see {transcript_path})"
        )
    if not summary["clean_exit"]:
        print_step("WARN shell lane: QEMU did not exit on reboot; killed instead")

    print_step("Shell lane PASSED")
    return summary
