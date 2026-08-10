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
import re
import subprocess
import threading
import time

from lib.boot_verdict import evaluate_normal_boot
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
    ensure_cpu_profile,
    required_smoke_patterns,
    run_kernel_make,
    run_windows_kernel,
)

SHELL_SMOKE_DIR = BUILD_DIR / "shell-smoke"
SHELL_READY_MARKER = "[SHELL] Interactive shell started"
SHELL_REBOOT_MARKER = "[SHELL] reboot requested"
SHELL_PROMPT = "aios# "

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
EXACT_ONE_RESPONSE_FAMILIES = ("[STATE] room", "[STATE] sec ")
EXACT_CANONICAL_RESPONSE_FAMILIES = {
    "[STATE] room schema=1": "[STATE] room",
    "[STATE] sec schema=1": "[STATE] sec ",
}


def shell_result_passed(
    ready: bool,
    failures: list[str],
    reboot_ack: bool,
    clean_exit: bool,
    boot_verdict_passed: bool = True,
) -> bool:
    return ready and not failures and reboot_ack and clean_exit and boot_verdict_passed


def expectation_matches(text: str, expectation: str) -> bool:
    """Match one shell expectation at whitespace-delimited token boundaries."""

    if not expectation:
        return False
    if expectation.startswith("[SHELL]"):
        pattern = r"(?m)^" + re.escape(expectation) + r"\r?$"
        return re.search(pattern, text) is not None

    if expectation.startswith("[STATE]"):
        pattern = r"(?m)^" + re.escape(expectation)
    else:
        pattern = r"(?<!\S)" + re.escape(expectation)
    if expectation.endswith("="):
        pattern += r"\S+"
    else:
        pattern += r"(?=\s|$)"
    return re.search(pattern, text) is not None


def expectations_match(text: str, expectations: list[str]) -> bool:
    lines = text.splitlines()
    canonical_family = (
        EXACT_CANONICAL_RESPONSE_FAMILIES.get(expectations[0])
        if expectations
        else None
    )
    if canonical_family is not None:
        canonical_records = [
            line for line in lines if line.startswith(canonical_family)
        ]
        if (
            len(canonical_records) != 1
            or canonical_records[0] != " ".join(expectations)
        ):
            return False

    for family_prefix in EXACT_ONE_RESPONSE_FAMILIES:
        if not any(
            expectation.startswith(family_prefix)
            for expectation in expectations
        ):
            continue
        family_records = [
            line for line in lines if line.startswith(family_prefix)
        ]
        if (
            len(family_records) != 1
            or _record_has_duplicate_fields(family_records[0])
        ):
            return False

    record_lines: list[str] | None = None
    for expectation in expectations:
        if expectation.startswith(("[STATE]", "[SHELL]")):
            matches = [
                line for line in lines
                if expectation_matches(line, expectation)
            ]
            if len(matches) != 1 or _record_has_duplicate_fields(matches[0]):
                return False
            record_lines = matches
            continue

        candidates = record_lines if record_lines is not None else lines
        matches = [
            line for line in candidates
            if expectation_matches(line, expectation)
        ]
        if not matches:
            return False
        record_lines = matches
    return True


def _record_has_duplicate_fields(record: str) -> bool:
    field_names = re.findall(
        r"(?<!\S)([A-Za-z_][A-Za-z0-9_]*)=\S+",
        record,
    )
    return len(field_names) != len(set(field_names))


# Each exchange: send `command`, then require every expectation to match its
# whitespace-delimited token on the anchored response record.
DEFAULT_EXCHANGES: list[dict[str, object]] = [
    {"command": "ping", "expect": ["[STATE] pong ticks="]},
    {"command": "state list", "expect": ["[STATE] topics list=health,room,mem,sched,nodes,pipeline,resource,pressure,slm,autonomy,user,sec,time,version"]},
    {"command": "state health", "expect": ["[STATE] health stability=stable", "degraded=0", "failed=0", "io_degraded=0", "autonomy="]},
    {"command": "state room", "expect": ["[STATE] room schema=1", "struct_size=1024", "ready=1", "generation=1", "cells=1", "cell_capacity=2", "nodes=1", "node_capacity=4", "bound_nodes=1", "nodebits=2", "nodebit_capacity=8", "bound_nodebits=2", "cell_id=1", "node_id=101", "node_parent=1", "nodebit_ids=1001,1002", "nodebit_parents=101,101", "source_valid=1", "generation_valid=1", "duplicate=0", "orphan=0", "unknown=0", "stale=0", "overflow=0", "observation_only=1", "management_only=1"]},
    {"command": "state mem", "expect": ["[STATE] mem heap_total=", "heap_free=", "lock_acquires="]},
    {"command": "state sched", "expect": ["[STATE] sched kthread_switches=", "preempt_ticks=", "address_space_switches=", "address_space_ready=1", "user_leaf_slots=2", "user_leaf_isolated=1", "bootstrap_process_ready=1", "bootstrap_owned_processes=2", "completed_process_runs=2", "current_pid=0", "last_pid=2", "tss_rsp0_publishes=2", "tss_rsp0_restores=2", "tss_rsp0_baseline=1", "total_tasks="]},
    {"command": "state pipeline", "expect": ["[STATE] pipeline active=", "executions="]},
    {"command": "state resource", "expect": ["[STATE] resource schema=1", "observation_only=1", "kinds=5", "units=2", "entries=5", "capacity=8", "source_flags=0x1f", "sample=", "sampled_ns=", "owner_rows=0", "unattributed_rows=5", "heap_used=", "heap_limit=", "tensor_used=", "tensor_limit=", "tensor_high_water=", "fabric_used=", "fabric_limit=64", "rings_used=", "rings_limit=16", "sched_used=", "sched_limit=256", "high_water_kinds=1", "denied_kinds=0"]},
    {"command": "state pressure", "expect": ["[STATE] pressure schema=1", "observation_only=1", "gate_filter_separate=1", "max_levels=4", "active_levels=2", "planes=3", "source_flags=", "sample=", "sched_q10=", "memory_q10=", "policy_q10=", "hotspot=", "hotspot_valid=", "concentration_q10=", "queued=", "runnable=", "active_domains=", "shared_windows=", "writer_pairs=", "gate_evals="]},
    {"command": "state nodes", "expect": ["[STATE] nodes active=", "[STATE] node id=40"]},
    {"command": "state slm", "expect": ["[STATE] slm apply_ok=1", "tsc_khz="]},
    {"command": "state autonomy", "expect": ["[STATE] autonomy schema=1", "observation_only=1", "safe_mode=0", "support_mem=observe-only", "support_sched=apply", "support_accel=observe-only", "support_infer=observe-only", "telemetry=", "proposed=0", "approved=0", "committed=0", "rejected=0", "rollbacks=0", "queue_depth=0", "event_depth=0", "last_valid=0", "last_action=0", "last_target=none", "last_state=none", "last_reason=none"]},
    {"command": "state user", "expect": ["[STATE] user attempted=1", "elf_loaded=1", "entered=1 returned=1 syscall_ok=1", "boundary_ok=1", "exit_code=42", "private_cr3=1", "slot=0", "cr3_restored=1", "if_restored=1", "leaf_sealed=1", "nx_enforced=1", "tensor_excluded=1", "pid=1", "process_bound=1", "kstack_bytes=16384", "rsp0_changed=1", "rsp0_published=1", "int80_entries=3", "all_int80_entries_in_stack=1", "rsp0_restored=1", "kstack_floor_canary=1", "pair_attempted=1", "pair_ready=1", "pair_runs=2", "second_pid=2", "second_slot=1", "second_exit_code=42", "second_syscall_ok=1", "second_boundary_ok=1", "second_int80_entries=3", "distinct_pid=1", "distinct_slot=1", "distinct_cr3=1", "distinct_backing=1", "distinct_stack=1", "between_clean=1", "pair_current_pid=0", "pair_last_pid=2", "pair_rsp0_publishes=2", "pair_rsp0_restores=2", "pair_tss_baseline=1", "both_restored=1", "trap_captured=1", "trap_from_user=1", "trap_rsp_user=1", "trap_rip_user=1", "trap_canary=1", "trap_frame_in_kstack=1", "trap_addr_exact=1", "trap_contract=1", "saved_captures=2", "saved_pid_a=1", "saved_slot_a=0", "saved_seq_a=1", "saved_valid_a=1", "saved_owner_a=1", "saved_frame_a=1", "saved_cr3_a=1", "saved_rsp0_a=1", "saved_pid_b=2", "saved_slot_b=1", "saved_seq_b=2", "saved_valid_b=1", "saved_owner_b=1", "saved_frame_b=1", "saved_cr3_b=1", "saved_rsp0_b=1", "saved_distinct_storage=1", "saved_current_pid=0", "saved_stale_owner=0", "saved_resume_ready=0", "event_schema=1", "event_count=6", "event_lifecycle=4", "event_captures=2", "event_first_seq=1", "event_last_seq=6", "event_ordered=1", "event_owner_ok=1", "event_cr3_ok=1", "event_rsp0_ok=1", "event_if0=1", "event_snapshot_refs=1", "event_outcomes_ok=1", "event_capture_seq_separate=1", "event_current_pid=0", "event_stale_owner=0", "event_dropped=0", "event_overflow=0", "event_evidence_only=1", "event_switches=0", "event_resume_ready=0"]},
    {"command": "state sec", "expect": ["[STATE] sec nx=", "canary=1"]},
    {"command": "state time", "expect": ["[STATE] time ticks=", "hz="]},
    {"command": "state version", "expect": ["[STATE] version release="]},
    {"command": "state bogus", "expect": ["[STATE] error reason=unknown-topic"]},
]


def state_sec_expectations(cpu_profile: str) -> list[str]:
    cpu_profile = ensure_cpu_profile(cpu_profile)
    if cpu_profile == "max-smap":
        smap_supported, smap, gate_active = 1, 1, 1
        common_clac, common_fallback = 2, 0
        int80_clac, int80_fallback, gate_skips = 6, 0, 0
    else:
        smap_supported, smap, gate_active = 0, 0, 0
        common_clac, common_fallback = 0, 2
        int80_clac, int80_fallback, gate_skips = 0, 6, 8
    return [
        "[STATE] sec schema=1",
        "nx=1",
        f"smep={1 if cpu_profile == 'max-smap' else 0}",
        f"umip={1 if cpu_profile == 'max-smap' else 0}",
        f"smap_supported={smap_supported}",
        f"smap={smap}",
        "canary=1",
        "entry_schema=1",
        "entry_ready=1",
        f"entry_gate_active={gate_active}",
        "entry_common=2",
        "entry_common_saved_ac=2",
        f"entry_common_clac={common_clac}",
        f"entry_common_fallback={common_fallback}",
        "entry_common_post_ac0=2",
        "entry_int80=6",
        "entry_int80_saved_ac=4",
        f"entry_int80_clac={int80_clac}",
        f"entry_int80_fallback={int80_fallback}",
        "entry_int80_post_ac0=6",
        f"entry_gate_skips={gate_skips}",
        "entry_gate_mismatch=0",
    ]


def shell_exchanges(cpu_profile: str) -> list[dict[str, object]]:
    cpu_profile = ensure_cpu_profile(cpu_profile)
    return [
        {
            "command": exchange["command"],
            "expect": (
                state_sec_expectations(cpu_profile)
                if exchange["command"] == "state sec"
                else list(exchange["expect"])  # type: ignore[arg-type]
            ),
        }
        for exchange in DEFAULT_EXCHANGES
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
            if expectations_match(window, needles):
                return True
            if self.proc.poll() is not None or time.time() >= deadline:
                return False
            time.sleep(0.05)

    def wait_for_prompt(self, timeout_sec: float, start_at: int = 0) -> bool:
        deadline = time.time() + timeout_sec
        while True:
            if SHELL_PROMPT in self.text()[start_at:]:
                return True
            if self.proc.poll() is not None or time.time() >= deadline:
                return False
            time.sleep(0.05)

    def wait_for_response(
        self,
        expectations: list[str],
        timeout_sec: float,
        start_at: int,
    ) -> bool:
        """Wait for the next prompt, then judge the complete response."""

        deadline = time.time() + timeout_sec
        while True:
            window = self.text()[start_at:]
            prompt_index = window.find(SHELL_PROMPT)
            if prompt_index >= 0:
                response = window[:prompt_index]
                return expectations_match(response, expectations)
            if self.proc.poll() is not None or time.time() >= deadline:
                return False
            time.sleep(0.05)

    def drain(self, timeout_sec: float = 1.0) -> bool:
        self._reader.join(timeout=timeout_sec)
        return not self._reader.is_alive()

    def send_line(self, line: str) -> None:
        # Pace bytes out one at a time: the guest drains its 16-byte UART
        # FIFO on a 100 Hz polling loop, so a long burst can overflow the
        # FIFO and silently drop the trailing newline.
        for byte in (line + "\n").encode("ascii"):
            self.proc.stdin.write(bytes([byte]))
            self.proc.stdin.flush()
            time.sleep(0.002)


def build_kernel_iso(timeout_sec: int, cpu_profile: str = "default") -> None:
    if host_name() == "windows":
        run_windows_kernel("iso", "minimal", timeout_sec, cpu_profile)
        return
    run_kernel_make("all")
    run_kernel_make("iso")


def run_shell_lane(
    timeout_sec: int = DEFAULT_QEMU_TIMEOUT,
    strict: bool = False,
    skip_build: bool = False,
    cpu_profile: str = "default",
) -> dict[str, object]:
    cpu_profile = ensure_cpu_profile(cpu_profile)
    artifact_dir = (
        SHELL_SMOKE_DIR
        if cpu_profile == "default"
        else SHELL_SMOKE_DIR / cpu_profile
    )
    ensure_dir(artifact_dir)
    transcript_path = artifact_dir / "transcript.log"
    summary_path = artifact_dir / "summary.json"
    transcript_path.unlink(missing_ok=True)
    summary_path.unlink(missing_ok=True)

    started_at = time.monotonic()
    termination: dict[str, object] = {
        "reason": "not-started",
        "exit_code": None,
        "timed_out": False,
        "duration_ms": None,
        "host_killed": False,
    }
    summary: dict[str, object] = {
        "lane": "shell",
        "cpu_profile": cpu_profile,
        "qemu_cpu_args": ["-cpu", "max"] if cpu_profile == "max-smap" else [],
        "qemu": None,
        "ready": False,
        "exchanges": [],
        "reboot_ack": False,
        "reader_drained": False,
        "clean_exit": False,
        "exit_code": None,
        "termination": termination,
        "boot_verdict": None,
        "failures": [],
        "passed": False,
    }
    failures: list[str] = []

    def persist_artifacts(transcript: str) -> None:
        termination["duration_ms"] = int((time.monotonic() - started_at) * 1000)
        summary["exit_code"] = termination["exit_code"]
        summary["failures"] = list(failures)
        boot_verdict = evaluate_normal_boot(
            transcript,
            required_smoke_patterns("minimal", cpu_profile),
            termination=termination,
        )
        summary["boot_verdict"] = boot_verdict
        summary["passed"] = shell_result_passed(
            bool(summary["ready"]),
            failures,
            bool(summary["reboot_ack"]),
            bool(summary["clean_exit"]),
            bool(boot_verdict["passed"]),
        )
        transcript_path.write_text(transcript, encoding="utf-8")
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    # Establish current-run artifacts before any external operation so an
    # early failure cannot leave a stale PASS summary from a prior run.
    transcript_path.write_text("", encoding="utf-8")
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    qemu = find_qemu()
    summary["qemu"] = qemu
    if not qemu:
        termination["reason"] = "qemu-not-found"
        summary["skipped"] = not strict
        persist_artifacts("")
        if strict:
            raise ToolError("`qemu-system-x86_64` is required for the shell lane.")
        print_step("SKIP shell lane: qemu-system-x86_64 not found")
        return summary

    try:
        if not skip_build:
            build_kernel_iso(timeout_sec, cpu_profile)
    except Exception as exc:
        termination["reason"] = "kernel-build-error"
        summary["error"] = {"type": type(exc).__name__, "message": str(exc)}
        persist_artifacts("")
        raise

    iso = BUILD_DIR / "aios-kernel.iso"
    if not iso.exists():
        termination["reason"] = "kernel-iso-missing"
        error = ToolError(f"Kernel ISO not found: {iso}")
        summary["error"] = {"type": type(error).__name__, "message": str(error)}
        persist_artifacts("")
        raise error

    # Minimal hardware profile: the lane exercises the shell, not drivers.
    # Drop -no-shutdown so the final `reboot` (with -no-reboot) makes QEMU
    # exit instead of pausing, giving the lane a clean teardown.
    cmd = [arg for arg in build_qemu_smoke_command(
        qemu, str(iso), "stdio", "minimal", cpu_profile
    )
           if arg != "-no-shutdown"]
    summary["qemu_command"] = cmd
    print_step(f"RUN {shell_join(cmd)}")

    proc: subprocess.Popen | None = None
    session: SerialSession | None = None
    reboot_cursor = 0
    reboot_sent = False
    stage = "qemu-launch"

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(REPO_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        termination["reason"] = "running"
        session = SerialSession(proc)
        stage = "shell-ready"

        if not session.wait_for([SHELL_READY_MARKER], timeout_sec):
            exit_code = proc.poll()
            if exit_code is None:
                termination["reason"] = "shell-ready-timeout"
                termination["timed_out"] = True
            else:
                termination["reason"] = "qemu-exit-before-shell-ready"
                termination["exit_code"] = exit_code
            raise ToolError(
                f"Kernel shell did not become ready within {timeout_sec}s "
                f"(missing marker: {SHELL_READY_MARKER})"
            )
        if not session.wait_for_prompt(timeout_sec):
            raise ToolError(
                f"Kernel shell did not emit its initial prompt within "
                f"{timeout_sec}s (missing prompt: {SHELL_PROMPT!r})"
            )
        summary["ready"] = True

        stage = "shell-exchanges"
        for exchange in shell_exchanges(cpu_profile):
            command = str(exchange["command"])
            expect = list(exchange["expect"])  # type: ignore[arg-type]
            cursor = session.cursor()
            session.send_line(command)
            ok = session.wait_for_response(
                expect, COMMAND_TIMEOUT_SEC, start_at=cursor
            )
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
        if proc.poll() is not None:
            termination["reason"] = "qemu-exit-before-reboot"
            termination["exit_code"] = proc.returncode
            raise ToolError("QEMU exited before the shell reboot request.")

        stage = "shell-reboot"
        reboot_cursor = session.cursor()
        session.send_line("reboot")
        reboot_sent = True
        deadline = time.time() + REBOOT_EXIT_TIMEOUT_SEC
        while proc.poll() is None and time.time() < deadline:
            time.sleep(0.1)
        exit_code = proc.poll()
        if exit_code is None:
            termination["reason"] = "reboot-exit-timeout"
            termination["timed_out"] = True
        else:
            termination["reason"] = (
                "guest-reboot-exit" if exit_code == 0 else "qemu-exit-after-reboot"
            )
            termination["exit_code"] = exit_code
    except Exception as exc:
        summary["error"] = {"type": type(exc).__name__, "message": str(exc)}
        if termination["reason"] in {"not-started", "running"}:
            if proc is not None and proc.poll() is not None:
                termination["reason"] = f"qemu-exit-during-{stage}"
                termination["exit_code"] = proc.returncode
            else:
                termination["reason"] = f"{stage}-error"
        raise
    finally:
        if proc is not None and proc.poll() is None:
            termination["host_killed"] = True
            reason = str(termination["reason"])
            if reason == "running":
                termination["reason"] = f"host-kill-during-{stage}"
            elif not reason.endswith("host-kill"):
                termination["reason"] = f"{reason}-host-kill"
            proc.kill()
            proc.wait()
        if proc is not None:
            termination["exit_code"] = proc.returncode

        transcript = ""
        if session is not None:
            summary["reader_drained"] = session.drain()
            transcript = session.text()
            if not summary["reader_drained"]:
                failures.append("reader-drain")

        if reboot_sent:
            summary["reboot_ack"] = expectation_matches(
                transcript[reboot_cursor:],
                SHELL_REBOOT_MARKER,
            )
            if not summary["reboot_ack"]:
                failures.append("reboot-ack")

        summary["clean_exit"] = bool(
            reboot_sent
            and summary["reboot_ack"]
            and summary["reader_drained"]
            and termination["exit_code"] == 0
            and not termination["host_killed"]
        )
        persist_artifacts(transcript)

    print_step(f"Shell lane artifacts -> {artifact_dir}")

    if failures:
        raise ToolError(
            f"Shell lane failed exchanges: {failures} (see {transcript_path})"
        )
    if not summary["clean_exit"]:
        raise ToolError(
            "Shell lane did not complete a clean QEMU exit after reboot "
            f"(exit_code={summary['exit_code']}; see {transcript_path})"
        )
    boot_verdict = summary["boot_verdict"]
    if not isinstance(boot_verdict, dict) or not boot_verdict.get("passed"):
        raise ToolError(
            "Shell lane boot verdict failed "
            f"(reasons={(boot_verdict or {}).get('reasons')}; see {transcript_path})"
        )

    print_step("Shell lane PASSED")
    return summary
