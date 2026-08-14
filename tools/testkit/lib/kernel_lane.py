from __future__ import annotations

import subprocess
import time

from lib.boot_log import (
    boot_summary_path,
    parse_boot_log_file,
    write_boot_summary,
)
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
DEFAULT_CPU_PROFILE = "default"
SUPPORTED_CPU_PROFILES = {"default", "max-smap"}
CPU_SECURITY_PATTERNS = {
    "default": "[SEC] nx=1 smep=0 umip=0 smap_supported=0 smap=0",
    "max-smap": "[SEC] nx=1 smep=1 umip=1 smap_supported=1 smap=1",
}
RING3_ENTRY_AC_HARDENING_PATTERNS = {
    "default": (
        "[SEC] ring3 entry AC hardening PASS schema=1 smap_supported=0 "
        "smap=0 gate_active=0 common_entries=2 common_saved_ac=2 "
        "common_clac=0 common_fallback=2 common_post_ac0=2 "
        "int80_entries=6 int80_saved_ac=4 int80_clac=0 int80_fallback=6 "
        "int80_post_ac0=6 gate_skips=8 gate_mismatch=0"
    ),
    "max-smap": (
        "[SEC] ring3 entry AC hardening PASS schema=1 smap_supported=1 "
        "smap=1 gate_active=1 common_entries=2 common_saved_ac=2 "
        "common_clac=2 common_fallback=0 common_post_ac0=2 "
        "int80_entries=6 int80_saved_ac=4 int80_clac=6 int80_fallback=0 "
        "int80_post_ac0=6 gate_skips=0 gate_mismatch=0"
    ),
}
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
PROCESS_TRAP_SNAPSHOT_PATTERN = (
    "[PROC] trap evidence snapshot PASS schema=1 captures=2 pid_a=1 "
    "slot_a=0 seq_a=1 valid_a=1 owner_a=1 frame_a=1 cr3_a=1 rsp0_a=1 "
    "pid_b=2 slot_b=1 seq_b=2 valid_b=1 owner_b=1 frame_b=1 cr3_b=1 "
    "rsp0_b=1 distinct_storage=1 current_pid=0 stale_owner=0 resume_ready=0"
)
PROCESS_EVENT_JOURNAL_PATTERN = (
    "[PROC] process event journal PASS schema=1 events=6 lifecycle=4 "
    "captures=2 seqs=1,2,3,4,5,6 kinds=1,2,3,1,2,3 "
    "reasons=1,2,3,1,2,3 from_pids=0,1,1,0,2,2 "
    "to_pids=1,1,0,2,2,0 slots=0,0,0,1,1,1 "
    "generations=1,1,1,1,1,1 capture_seqs=0,1,1,0,2,2 "
    "owner_ok=1,1,1,1,1,1 cr3_ok=1,1,1,1,1,1 "
    "rsp0_ok=1,1,1,1,1,1 if0=1,1,1,1,1,1 "
    "snapshot_refs=0,1,1,0,1,1 outcomes=1,1,1,1,1,1 "
    "capture_seq_separate=1 current_pid=0 stale_owner=0 dropped=0 "
    "overflow=0 evidence_only=1 switch_events=0 resume_ready=0"
)
KERNEL_ROOM_MANAGEMENT_PATTERN = (
    "[ROOM] management hierarchy selftest PASS schema=1 struct_size=1024 "
    "generation=1 cells=1 nodes=1 bound_nodes=1 nodebits=2 "
    "bound_nodebits=2 source_valid=1 generation_valid=1 "
    "duplicate_rejected=1 orphan_rejected=1 unknown_rejected=1 "
    "stale_rejected=1 overflow_rejected=1 tail_rejected=1 observation_only=1 "
    "management_only=1"
)
KERNEL_ROOM_BINDING_PATTERN = (
    "[ROOM] source binding selftest PASS schema=1 struct_size=256 "
    "binding_generation=1 bindings=1 capacity=2 canonical_namespace=2 "
    "canonical_id=101 canonical_kind=1 canonical_generation=1 "
    "parent_cell_id=1 parent_generation=1 source_namespace=1 source_id=1 "
    "source_instance=1 source_generation=1 source_kind=1 source_role=1 "
    "kind_match=1 role_match=1 producer_owned=1 copied_read=1 "
    "missing_rejected=1 duplicate_rejected=1 orphan_rejected=1 "
    "namespace_rejected=1 kind_rejected=1 role_rejected=1 "
    "instance_rejected=1 zero_generation_rejected=1 "
    "generation_rollback_rejected=1 stale_rejected=1 "
    "init_order_rejected=1 schema_rejected=1 overflow_rejected=1 "
    "tail_rejected=1 source_valid=1 generation_valid=1 binding_valid=1 "
    "observation_only=1 management_only=1"
)


def ensure_smoke_profile(smoke_profile: str) -> str:
    if smoke_profile not in SUPPORTED_SMOKE_PROFILES:
        supported = ", ".join(sorted(SUPPORTED_SMOKE_PROFILES))
        raise ToolError(f"Unsupported smoke profile: {smoke_profile} (supported: {supported})")
    return smoke_profile


def ensure_cpu_profile(cpu_profile: str) -> str:
    if cpu_profile not in SUPPORTED_CPU_PROFILES:
        supported = ", ".join(sorted(SUPPORTED_CPU_PROFILES))
        raise ToolError(
            f"Unsupported CPU profile: {cpu_profile} (supported: {supported})"
        )
    return cpu_profile


def build_qemu_smoke_command(
    qemu: str,
    iso: str,
    serial_target: str,
    smoke_profile: str,
    cpu_profile: str = DEFAULT_CPU_PROFILE,
) -> list[str]:
    smoke_profile = ensure_smoke_profile(smoke_profile)
    cpu_profile = ensure_cpu_profile(cpu_profile)
    cmd = [
        qemu,
        "-cdrom",
        iso,
        "-boot",
        "d",
        "-m",
        "256M",
    ]
    if cpu_profile == "max-smap":
        cmd += ["-cpu", "max"]
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


def required_smoke_patterns(
    smoke_profile: str,
    cpu_profile: str = DEFAULT_CPU_PROFILE,
) -> list[str]:
    smoke_profile = ensure_smoke_profile(smoke_profile)
    cpu_profile = ensure_cpu_profile(cpu_profile)
    required = [
        "AIOS Kernel Ready",
        CPU_SECURITY_PATTERNS[cpu_profile],
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
        PROCESS_TRAP_SNAPSHOT_PATTERN,
        PROCESS_EVENT_JOURNAL_PATTERN,
        RING3_ENTRY_AC_HARDENING_PATTERNS[cpu_profile],
        KERNEL_ROOM_MANAGEMENT_PATTERN,
        KERNEL_ROOM_BINDING_PATTERN,
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


def collect_smoke_summary(
    smoke_profile: str,
    cpu_profile: str = DEFAULT_CPU_PROFILE,
    qemu_command: list[str] | None = None,
) -> dict[str, object]:
    if not SERIAL_LOG.exists():
        raise ToolError("Smoke test did not produce a serial log.")
    if SERIAL_LOG.stat().st_size == 0:
        raise ToolError("Smoke test produced an empty serial log.")

    cpu_profile = ensure_cpu_profile(cpu_profile)
    summary = parse_boot_log_file(SERIAL_LOG, smoke_profile, cpu_profile)
    log_text = SERIAL_LOG.read_text(encoding="utf-8", errors="replace")
    required_patterns = required_smoke_patterns(smoke_profile, cpu_profile)
    verdict = evaluate_normal_boot(log_text, required_patterns)
    security = summary.get("security")
    security_ready = (
        isinstance(security, dict) and security.get("ready") is True
    )
    security_profile_match = (
        isinstance(security, dict)
        and security.get("profile_match") is True
    )
    security_gate_passed = security_ready and security_profile_match
    summary["security_gate"] = {
        "ready": security_ready,
        "profile_match": security_profile_match,
        "passed": security_gate_passed,
    }
    if not security_gate_passed:
        security_reason = {
            "code": "SECURITY_SUMMARY_INVALID",
            "ready": security_ready,
            "profile_match": security_profile_match,
        }
        verdict["reasons"].append(security_reason)
        verdict["passed"] = False
        verdict["outcome"] = "FAIL"
        if verdict.get("first_failure") is None:
            verdict["first_failure"] = {
                "kind": "SECURITY_SUMMARY_INVALID",
                "line": None,
            }
    summary["required_patterns"] = required_patterns
    summary["missing_patterns"] = verdict["missing_patterns"]
    summary["verdict"] = verdict
    summary["cpu_profile"] = cpu_profile
    summary["qemu_cpu_args"] = ["-cpu", "max"] if cpu_profile == "max-smap" else []
    if qemu_command is not None:
        summary["qemu_command"] = qemu_command
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
    cpu_profile: str = DEFAULT_CPU_PROFILE,
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
            "-CpuProfile",
            ensure_cpu_profile(cpu_profile),
            "-TestTimeoutSec",
            str(timeout_sec),
            "-SkipLock",
        ]
    )
    if target == "test":
        return collect_smoke_summary(smoke_profile, cpu_profile)
    return None


def run_qemu_smoke_test(
    timeout_sec: int = DEFAULT_QEMU_TIMEOUT,
    strict: bool = False,
    smoke_profile: str = DEFAULT_SMOKE_PROFILE,
    cpu_profile: str = DEFAULT_CPU_PROFILE,
) -> dict[str, object]:
    qemu = which_any("qemu-system-x86_64")
    if not qemu:
        if strict:
            raise ToolError("`qemu-system-x86_64` is required for kernel smoke testing.")
        print_step("SKIP kernel smoke: qemu-system-x86_64 not found")
        return {
            "smoke_profile": smoke_profile,
            "cpu_profile": ensure_cpu_profile(cpu_profile),
            "qemu_cpu_args": (
                ["-cpu", "max"] if cpu_profile == "max-smap" else []
            ),
            "serial_log": str(SERIAL_LOG),
            "skipped": True,
            "reason": "qemu-system-x86_64 not found",
        }

    iso = BUILD_DIR / "aios-kernel.iso"
    if not iso.exists():
        raise ToolError(f"Kernel ISO not found: {iso}")

    if SERIAL_LOG.exists():
        SERIAL_LOG.unlink()

    cmd = build_qemu_smoke_command(
        qemu,
        str(iso),
        f"file:{SERIAL_LOG}",
        smoke_profile,
        cpu_profile,
    )

    print_step(f"RUN {shell_join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=str(REPO_ROOT))
    deadline = time.time() + timeout_sec

    while proc.poll() is None and time.time() < deadline:
        time.sleep(0.25)

    if proc.poll() is None:
        proc.kill()
        proc.wait()

    summary = collect_smoke_summary(smoke_profile, cpu_profile, cmd)
    print_step("Kernel smoke test PASSED")
    return summary


def run_kernel_suite(
    target: str,
    timeout_sec: int,
    strict: bool,
    smoke_profile: str = DEFAULT_SMOKE_PROFILE,
    export_boot_summary: bool = False,
    cpu_profile: str = DEFAULT_CPU_PROFILE,
) -> dict[str, object] | None:
    if export_boot_summary and target != "test":
        raise ToolError("`--export-boot-summary` requires `--target test` (or `--kernel-target test`).")

    smoke_profile = ensure_smoke_profile(smoke_profile)
    cpu_profile = ensure_cpu_profile(cpu_profile)
    if export_boot_summary:
        output_path = boot_summary_path(target, smoke_profile, cpu_profile)
        ensure_dir(output_path.parent)
        output_path.unlink(missing_ok=True)
        required_patterns = required_smoke_patterns(
            smoke_profile, cpu_profile
        )
        pending_verdict = evaluate_normal_boot("", required_patterns)
        write_boot_summary(
            {
                "smoke_profile": smoke_profile,
                "cpu_profile": cpu_profile,
                "serial_log": str(SERIAL_LOG),
                "line_count": 0,
                "artifact_state": "initialized-before-run",
                "qemu_cpu_args": (
                    ["-cpu", "max"]
                    if cpu_profile == "max-smap"
                    else []
                ),
                "required_patterns": required_patterns,
                "missing_patterns": pending_verdict["missing_patterns"],
                "verdict": pending_verdict,
            },
            target,
            smoke_profile,
            cpu_profile,
        )

    host = host_name()
    if host == "windows":
        summary = run_windows_kernel(
            target, smoke_profile, timeout_sec, cpu_profile
        )
        if export_boot_summary and summary is not None:
            ensure_dir(BUILD_DIR / "boot-summary")
            output_path = write_boot_summary(
                summary, target, smoke_profile, cpu_profile
            )
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
        summary = run_qemu_smoke_test(
            timeout_sec, strict, smoke_profile, cpu_profile
        )
        if export_boot_summary:
            ensure_dir(BUILD_DIR / "boot-summary")
            output_path = write_boot_summary(
                summary, target, smoke_profile, cpu_profile
            )
            print_step(f"Boot summary exported -> {output_path}")
        return summary

    raise ToolError(f"Unsupported kernel target: {target}")
