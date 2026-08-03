from __future__ import annotations

import subprocess
import time

from lib.boot_log import parse_boot_log_file, write_boot_summary
from lib.boot_verdict import evaluate_normal_boot
from lib.common import (
    BUILD_DIR,
    DEFAULT_QEMU_TIMEOUT,
    REPO_ROOT,
    SERIAL_LOG,
    ToolError,
    ensure_dir,
    host_name,
    print_step,
    run,
    shell_join,
    which_any,
)


DEFAULT_SMOKE_PROFILE = "full"
SUPPORTED_SMOKE_PROFILES = {"full", "minimal", "storage-only"}
RESOURCE_SELFTEST_PATTERN = (
    "[RESOURCE] ledger selftest PASS schema=1 kinds=5 units=2 entries=5 "
    "capacity=8 source_flags=31 limit_kinds=5 used_kinds=5 "
    "high_water_kinds=1 denied_kinds=0 owners_unattributed=1 "
    "observation_only=1"
)
PRESSURE_SELFTEST_PATTERN = (
    "[PRESSURE] tracker selftest PASS schema=1 planes=3 max_levels=4 "
    "active_levels=2 balanced=1 hotspot=1 overlap=1 gate_mask=1 "
    "observation_only=1"
)
TRAPFRAME_CONTRACT_PATTERN = (
    "[TRAP] frame contract selftest PASS size=176 canaries=15 int_no=3 "
    "err=0 cpl0=1 cs_match=1 ss_match=1 rip_exact=1 rsp_exact=1 "
    "frame_addr_exact=1 rflags_bit1=1 df_clear=1"
)
USER_TRAP_CAPTURE_PATTERN = (
    "[TRAP] user frame capture PASS pid_a=1 pid_b=2 captures_a=1 "
    "captures_b=1 from_user=1 cs=0x23 ss=0x1b rsp_user=1 rip_user=1 "
    "canary_ok=1 frame_in_kstack=1 frame_addr_exact=1 contract=1"
)


def ensure_smoke_profile(smoke_profile: str) -> str:
    if smoke_profile not in SUPPORTED_SMOKE_PROFILES:
        supported = ", ".join(sorted(SUPPORTED_SMOKE_PROFILES))
        raise ToolError(f"Unsupported smoke profile: {smoke_profile} (supported: {supported})")
    return smoke_profile


def build_qemu_smoke_command(qemu: str, iso: str, serial_target: str, smoke_profile: str) -> list[str]:
    smoke_profile = ensure_smoke_profile(smoke_profile)
    cmd = [
        qemu,
        "-cdrom",
        iso,
        "-boot",
        "d",
        "-m",
        "256M",
    ]
    if smoke_profile in {"minimal", "storage-only"}:
        cmd += ["-nic", "none"]
    else:
        cmd += ["-nic", "user,model=e1000", "-device", "qemu-xhci"]
    cmd += [
        "-serial",
        serial_target,
        "-display",
        "none",
        "-no-reboot",
        "-no-shutdown",
    ]
    return cmd


def required_smoke_patterns(smoke_profile: str) -> list[str]:
    smoke_profile = ensure_smoke_profile(smoke_profile)
    required = [
        "AIOS Kernel Ready",
        "[BOOT] Multiboot2 handoff PASS",
        "[SELFTEST] Memory microbench PASS",
        "[HEAP] lock selftest PASS",
        "[SCHED] context switch selftest PASS",
        "[SCHED] preempt selftest PASS",
        TRAPFRAME_CONTRACT_PATTERN,
        "[MM] address space selftest PASS",
        "[MM] user leaf isolation selftest PASS",
        "[MM] bootstrap user tensor exclusion PASS base=0x4000000 size=2097152 excluded=2097152 managed=1004535808 configured=1006632960 overflow=1 region=1 align=1 boundary=1 coalesce=1",
        "[PROC] bootstrap ownership selftest PASS slots=2 owned=2 stack_bytes=16384 unique_cr3=1 unique_backing=1 unique_stack=1",
        "[TIMER] PIT IRQ ready",
        "[DEV] Peripheral probe ready",
        "[USER] Ring3 scaffold ready=1",
        "[ROOM] snapshot stability=",
        "[HEALTH] stability=",
        "[NODEBIT] Policy gate ready entries=0",
        "[PIPE] Node pipeline ready",
        "[PIPE] selftest PASS",
        RESOURCE_SELFTEST_PATTERN,
        PRESSURE_SELFTEST_PATTERN,
        "[SLM] plan apply selftest PASS",
        "[SYSCALL] observe dispatch selftest PASS",
        "[USER] ring3 exec PASS",
        "[USER] private address space exec PASS slot=0 cr3_restored=1 if_restored=1 leaf_sealed=1 nx_enforced=1 tensor_excluded=1",
        "[USER] bootstrap process stack PASS pid=1 slot=0 process_bound=1 kstack_bytes=16384 rsp0_changed=1 rsp0_published=1 int80_entries=3 all_int80_entries_in_stack=1 rsp0_restored=1 kstack_floor_canary=1",
        "[USER] bootstrap process pair PASS runs=2 order=1,2 pid_a=1 slot_a=0 pid_b=2 slot_b=1 distinct_pid=1 distinct_slot=1 distinct_cr3=1 distinct_backing=1 distinct_stack=1 int80_a=3 int80_b=3 between_clean=1 current_pid=0 last_pid=2 rsp0_publishes=2 rsp0_restores=2 tss_rsp0_baseline=1 both_restored=1",
        USER_TRAP_CAPTURE_PATTERN,
        "[SHELL] Interactive shell started",
    ]
    if smoke_profile == "storage-only":
        required += [
            "[NET] No Intel E1000-compatible controller found",
            "[USB] No USB host controller found",
            "[STO] IDE ready=1",
            "[STO] IDE channels",
            "label=storage-bootstrap",
        ]
    elif smoke_profile == "minimal":
        required += [
            "[NET] No Intel E1000-compatible controller found",
            "[USB] No USB host controller found",
        ]
    else:
        required += [
            "[NET] E1000 ready",
            "[USB] XHCI ready=1",
        ]
    return required


def collect_smoke_summary(smoke_profile: str) -> dict[str, object]:
    if not SERIAL_LOG.exists():
        raise ToolError("Smoke test did not produce a serial log.")
    if SERIAL_LOG.stat().st_size == 0:
        raise ToolError("Smoke test produced an empty serial log.")

    summary = parse_boot_log_file(SERIAL_LOG, smoke_profile)
    log_text = SERIAL_LOG.read_text(encoding="utf-8", errors="replace")
    required_patterns = required_smoke_patterns(smoke_profile)
    verdict = evaluate_normal_boot(log_text, required_patterns)
    summary["required_patterns"] = required_patterns
    summary["missing_patterns"] = verdict["missing_patterns"]
    summary["verdict"] = verdict
    if not verdict["passed"]:
        tail = "\n".join(log_text.splitlines()[-40:])
        reason_codes = [reason["code"] for reason in verdict["reasons"]]
        raise ToolError(
            "Kernel smoke verdict failed. "
            f"Reasons={reason_codes} FirstFailure={verdict['first_failure']}\n"
            f"Last log lines:\n{tail}"
        )
    return summary


def run_kernel_make(target: str) -> None:
    make = which_any("make")
    if not make:
        raise ToolError("`make` not found on PATH for kernel build.")
    run([make, target])


def run_windows_kernel(
    target: str,
    smoke_profile: str = DEFAULT_SMOKE_PROFILE,
    timeout_sec: int = DEFAULT_QEMU_TIMEOUT,
) -> dict[str, object] | None:
    powershell = which_any("pwsh", "powershell")
    if not powershell:
        raise ToolError("PowerShell (`pwsh` or `powershell`) not found.")
    script = REPO_ROOT / "tools" / "testkit" / "kernel" / "build-windows.ps1"
    run(
        [
            powershell,
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
            "-Target",
            target,
            "-SmokeProfile",
            ensure_smoke_profile(smoke_profile),
            "-TestTimeoutSec",
            str(timeout_sec),
            "-SkipLock",
        ]
    )
    if target == "test":
        return collect_smoke_summary(smoke_profile)
    return None


def run_qemu_smoke_test(
    timeout_sec: int = DEFAULT_QEMU_TIMEOUT,
    strict: bool = False,
    smoke_profile: str = DEFAULT_SMOKE_PROFILE,
) -> dict[str, object]:
    qemu = which_any("qemu-system-x86_64")
    if not qemu:
        if strict:
            raise ToolError("`qemu-system-x86_64` is required for kernel smoke testing.")
        print_step("SKIP kernel smoke: qemu-system-x86_64 not found")
        return {
            "smoke_profile": smoke_profile,
            "serial_log": str(SERIAL_LOG),
            "skipped": True,
            "reason": "qemu-system-x86_64 not found",
        }

    iso = BUILD_DIR / "aios-kernel.iso"
    if not iso.exists():
        raise ToolError(f"Kernel ISO not found: {iso}")

    if SERIAL_LOG.exists():
        SERIAL_LOG.unlink()

    cmd = build_qemu_smoke_command(qemu, str(iso), f"file:{SERIAL_LOG}", smoke_profile)

    print_step(f"RUN {shell_join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT))
    deadline = time.time() + timeout_sec

    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.25)

    if proc.poll() is None:
        proc.kill()
        proc.wait()

    summary = collect_smoke_summary(smoke_profile)
    print_step("Kernel smoke test PASSED")
    return summary


def run_kernel_suite(
    target: str,
    timeout_sec: int,
    strict: bool,
    smoke_profile: str = DEFAULT_SMOKE_PROFILE,
    export_boot_summary: bool = False,
) -> dict[str, object] | None:
    if export_boot_summary and target != "test":
        raise ToolError("`--export-boot-summary` requires `--target test` (or `--kernel-target test`).")

    host = host_name()
    if host == "windows":
        summary = run_windows_kernel(target, smoke_profile, timeout_sec)
        if export_boot_summary and summary is not None:
            ensure_dir(BUILD_DIR / "boot-summary")
            output_path = write_boot_summary(summary, target, smoke_profile)
            print_step(f"Boot summary exported -> {output_path}")
        return summary

    if target == "clean":
        run_kernel_make("clean")
        return None
    if target == "info":
        run_kernel_make("info")
        return None
    if target == "all":
        run_kernel_make("all")
        return None
    if target == "iso":
        run_kernel_make("all")
        run_kernel_make("iso")
        return None
    if target == "test":
        run_kernel_make("all")
        run_kernel_make("iso")
        summary = run_qemu_smoke_test(timeout_sec, strict, smoke_profile)
        if export_boot_summary:
            ensure_dir(BUILD_DIR / "boot-summary")
            output_path = write_boot_summary(summary, target, smoke_profile)
            print_step(f"Boot summary exported -> {output_path}")
        return summary

    raise ToolError(f"Unsupported kernel target: {target}")
