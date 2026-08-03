from __future__ import annotations

import json
import re
from pathlib import Path

from lib.common import BUILD_DIR, ensure_dir


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

CHECKPOINT_PATTERNS = {
    "multiboot": "[BOOT] Multiboot2 verified.",
    "heap": "[INIT] Kernel Heap (kmalloc/kfree)... OK",
    "idt": "[INIT] Interrupt Descriptor Table (IDT)... OK",
    "trapframe_contract": "[TRAP] frame contract selftest PASS",
    "time_source": "[INIT] Kernel Time Source... OK",
    "timer_irq": "[TIMER] PIT IRQ ready",
    "acpi": "[INIT] ACPI Fabric Parser... OK",
    "pci_core": "[INIT] PCI Core... OK",
    "tensor_mm": "[INIT] Tensor Memory Manager... OK",
    "scheduler": "[INIT] AI Workload Scheduler... OK",
    "selftest": "[SELFTEST] Memory microbench PASS",
    "accel_hal": "[INIT] Accelerator HAL... OK",
    "peripheral_probe": "[INIT] Peripheral Probe Layer... OK",
    "memory_fabric": "[INIT] Memory Fabric Foundation... OK",
    "network_bootstrap": "[INIT] Intel E1000 Ethernet... OK",
    "usb_bootstrap": "[INIT] USB Host Bootstrap... OK",
    "storage_bootstrap": "[INIT] Storage Host Bootstrap... OK",
    "user_access": "[UACCESS] selftest PASS",
    "syscall": "[INIT] AI System Call Interface... OK",
    "autonomy": "[INIT] Autonomy Control Plane... OK",
    "slm_orchestrator": "[INIT] SLM Hardware Orchestrator... OK",
    "nodebit": "[INIT] NodeBit Policy Gate... OK",
    "resource_ledger": "[RESOURCE] ledger selftest PASS",
    "pressure_tracker": "[PRESSURE] tracker selftest PASS",
    "keyboard": "[INIT] PS/2 Keyboard... OK",
    "ring3_scaffold": "[USER] Ring3 scaffold ready=1",
    "bootstrap_process": "[PROC] bootstrap ownership selftest PASS",
    "bootstrap_process_stack": "[USER] bootstrap process stack PASS",
    "bootstrap_process_pair": "[USER] bootstrap process pair PASS",
    "user_trap_capture": "[TRAP] user frame capture PASS",
    "process_trap_snapshot": "[PROC] trap evidence snapshot PASS",
    "process_event_journal": "[PROC] process event journal PASS",
    "kernel_room": "[ROOM] snapshot stability=",
    "health": "[HEALTH] stability=",
    "ready": "AIOS Kernel Ready",
    "boot_complete": "[KERNEL] Boot complete. Launching interactive shell...",
    "shell": "[SHELL] Interactive shell started",
}

SELFTEST_RESULT_RE = re.compile(
    r"\[SELFTEST\] Memory microbench (?P<status>\w+) \((?P<size_kib>\d+) KiB x (?P<iterations>\d+)\)"
)
SELFTEST_METRIC_RE = re.compile(
    r"\[SELFTEST\] (?P<name>memset|memcpy|memmove)=(?P<cycles>\d+) cyc \((?P<cyc_per_kib>\d+) cyc/KiB\)"
)
PROFILE_MAIN_RE = re.compile(
    r"\[PROFILE\] TSC=(?P<tsc_khz>\d+) kHz invariant=(?P<invariant>\d+) memcpy=(?P<memcpy_mib_s>\d+) MiB/s tier=(?P<tier>\w+)"
)
PROFILE_CACHE_RE = re.compile(
    r"\[PROFILE\] Cache KiB L1=(?P<l1_kib>\d+) L2=(?P<l2_kib>\d+) L3=(?P<l3_kib>\d+) \| latency x100 cyc L1=(?P<l1_latency_x100>\d+) L2=(?P<l2_latency_x100>\d+) L3=(?P<l3_latency_x100>\d+) DRAM=(?P<dram_latency_x100>\d+)"
)
DEVICE_SUMMARY_RE = re.compile(
    r"\[DEV\] Summary: pci=(?P<pci>\d+) matched=(?P<matched>\d+) eth=(?P<eth>\d+) wifi=(?P<wifi>\d+) bt=(?P<bt>\d+) usb=(?P<usb>\d+) storage=(?P<storage>\d+)"
)
HEALTH_RE = re.compile(
    r"\[HEALTH\] stability=(?P<stability>\w+) ok=(?P<ok>\d+) degraded=(?P<degraded>\d+) failed=(?P<failed>\d+) unknown=(?P<unknown>\d+)(?: io_degraded=(?P<io_degraded>\d+)| req_fail=(?P<req_fail>\d+) autonomy=(?P<autonomy>\d+) risky_io=(?P<risky_io>\d+))"
)
NETWORK_READY_RE = re.compile(
    r"\[NET\] E1000 ready mmio=(?P<mmio>\S+) io=(?P<io>\S+) status=(?P<status>\S+) link=(?P<link>\w+) eeprom=(?P<eeprom>\d+)"
)
NETWORK_SELECTION_RE = re.compile(
    r"\[NET\] Selected e1000 candidate score=(?P<score>-?\d+) candidates=(?P<candidates>\d+) pci=(?P<pci>\S+) device=(?P<device>\S+) mmio_bars=(?P<mmio_bars>\d+) io_bars=(?P<io_bars>\d+) pcie=(?P<pcie>\d+)"
)
USB_SELECTION_RE = re.compile(
    r"\[USB\] Selected bootstrap candidate=(?P<controller>\w+) score=(?P<score>-?\d+) candidates=(?P<candidates>\d+) pci=(?P<pci>\S+) mmio_bars=(?P<mmio_bars>\d+) io_bars=(?P<io_bars>\d+) pcie=(?P<pcie>\d+)"
)
USB_READY_RE = re.compile(
    r"\[USB\] (?P<controller>\w+) ready=(?P<ready>\d+) vendor=(?P<vendor>\S+) device=(?P<device>\S+) pci=(?P<pci>\S+) cmd=(?P<cmd>\S+) mmio=(?P<mmio>\S+) io=(?P<io>\S+)"
)
STORAGE_SELECTION_RE = re.compile(
    r"\[STO\] Selected bootstrap candidate=(?P<controller>\w+) score=(?P<score>-?\d+) candidates=(?P<candidates>\d+) pci=(?P<pci>\S+) mmio_bars=(?P<mmio_bars>\d+) io_bars=(?P<io_bars>\d+) pcie=(?P<pcie>\d+)"
)
STORAGE_READY_RE = re.compile(
    r"\[STO\] (?P<controller>\w+) ready=(?P<ready>\d+) vendor=(?P<vendor>\S+) device=(?P<device>\S+) pci=(?P<pci>\S+) cmd=(?P<cmd>\S+) mmio=(?P<mmio>\S+) io=(?P<io>\S+)"
)
STORAGE_CHANNEL_RE = re.compile(
    r"\[STO\] IDE channels primary=(?P<primary_cmd>\S+)/(?P<primary_ctl>\S+) status=(?P<primary_status>\S+) live=(?P<primary_live>\d+) secondary=(?P<secondary_cmd>\S+)/(?P<secondary_ctl>\S+) status=(?P<secondary_status>\S+) live=(?P<secondary_live>\d+)"
)
SLM_MAIN_RE = re.compile(
    r"\[SLM\] MainAI mode=(?P<mode>\w+) sco=(?P<sco>-?\d+) workers=(?P<workers>\d+) pipeline_qd=(?P<pipeline_qd>\d+) depth=(?P<depth>\d+) ring=(?P<ring_used>\d+)/(?P<ring_total>\d+)"
)
SLM_RUNTIME_RE = re.compile(
    r"\[SLM\] Runtime state=(?P<state>[\w\-]+) status=(?P<status>-?\d+) snapshot_abi=(?P<snapshot_abi>\d+) nodebits=(?P<nodebits>\d+) generation=(?P<generation>\d+)"
)
SLM_USER_AI_RE = re.compile(
    r"\[SLM\] UserAI access score=(?P<score>\d+) flags=(?P<flags>\S+) direct_mmio=(?P<direct_mmio>\d+) mediated=(?P<mediated>\d+) clock=(?P<clock_main>\d+)/(?P<clock_worker>\d+)/(?P<clock_io>\d+)/(?P<clock_memory>\d+)/(?P<clock_guardian>\d+)/(?P<clock_reserve>\d+) slice=(?P<slice_us>\d+)us poll=(?P<poll_us>\d+)us"
)
SLM_SEEDED_RE = re.compile(r"\[SLM\] Seeded plan (?P<plan_id>\d+) label=(?P<label>[a-z0-9\-]+)")
USER_SCAFFOLD_RE = re.compile(
    r"\[USER\] Ring3 scaffold ready=(?P<ready>\d+) tr=(?P<tr>\S+) user_cs=(?P<user_cs>\S+) user_ds=(?P<user_ds>\S+) rsp0=(?P<rsp0>\S+) gdt_base=(?P<gdt_base>\S+) gdt_limit=(?P<gdt_limit>\d+)"
)
USER_ACCESS_RE = re.compile(
    r"\[UACCESS\] selftest (?P<status>\w+) structural=(?P<structural>\d+) copy=(?P<copy>\d+) zero_copy=(?P<zero_copy>\d+)(?: string=(?P<string>\d+))?"
)
PROCESS_OWNERSHIP_RE = re.compile(
    r"\[PROC\] bootstrap ownership selftest (?P<status>\w+) slots=(?P<slots>\d+) owned=(?P<owned>\d+) stack_bytes=(?P<stack_bytes>\d+) unique_cr3=(?P<unique_cr3>\d+) unique_backing=(?P<unique_backing>\d+) unique_stack=(?P<unique_stack>\d+)"
)
PROCESS_STACK_RE = re.compile(
    r"\[USER\] bootstrap process stack (?P<status>\w+) pid=(?P<pid>\d+) slot=(?P<slot>\d+) process_bound=(?P<process_bound>\d+) kstack_bytes=(?P<kstack_bytes>\d+) rsp0_changed=(?P<rsp0_changed>\d+) rsp0_published=(?P<rsp0_published>\d+) int80_entries=(?P<int80_entries>\d+) all_int80_entries_in_stack=(?P<all_int80_entries_in_stack>\d+) rsp0_restored=(?P<rsp0_restored>\d+) kstack_floor_canary=(?P<kstack_floor_canary>\d+)"
)
PROCESS_PAIR_RE = re.compile(
    r"\[USER\] bootstrap process pair (?P<status>\w+) "
    r"runs=(?P<runs>\d+) order=(?P<order>\S+) "
    r"pid_a=(?P<pid_a>\d+) slot_a=(?P<slot_a>\d+) "
    r"pid_b=(?P<pid_b>\d+) slot_b=(?P<slot_b>\d+) "
    r"distinct_pid=(?P<distinct_pid>\d+) "
    r"distinct_slot=(?P<distinct_slot>\d+) "
    r"distinct_cr3=(?P<distinct_cr3>\d+) "
    r"distinct_backing=(?P<distinct_backing>\d+) "
    r"distinct_stack=(?P<distinct_stack>\d+) "
    r"int80_a=(?P<int80_a>\d+) int80_b=(?P<int80_b>\d+) "
    r"between_clean=(?P<between_clean>\d+) "
    r"current_pid=(?P<current_pid>\d+) last_pid=(?P<last_pid>\d+) "
    r"rsp0_publishes=(?P<rsp0_publishes>\d+) "
    r"rsp0_restores=(?P<rsp0_restores>\d+) "
    r"tss_rsp0_baseline=(?P<tss_rsp0_baseline>\d+) "
    r"both_restored=(?P<both_restored>\d+)"
)
PROCESS_TRAP_SNAPSHOT_PREFIX = "[PROC] trap evidence snapshot "
PROCESS_TRAP_SNAPSHOT_RE = re.compile(
    r"^\[PROC\] trap evidence snapshot (?P<status>\w+) "
    r"schema=(?P<schema>\d+) captures=(?P<captures>\d+) "
    r"pid_a=(?P<pid_a>\d+) slot_a=(?P<slot_a>\d+) "
    r"seq_a=(?P<seq_a>\d+) valid_a=(?P<valid_a>\d+) "
    r"owner_a=(?P<owner_a>\d+) frame_a=(?P<frame_a>\d+) "
    r"cr3_a=(?P<cr3_a>\d+) rsp0_a=(?P<rsp0_a>\d+) "
    r"pid_b=(?P<pid_b>\d+) slot_b=(?P<slot_b>\d+) "
    r"seq_b=(?P<seq_b>\d+) valid_b=(?P<valid_b>\d+) "
    r"owner_b=(?P<owner_b>\d+) frame_b=(?P<frame_b>\d+) "
    r"cr3_b=(?P<cr3_b>\d+) rsp0_b=(?P<rsp0_b>\d+) "
    r"distinct_storage=(?P<distinct_storage>\d+) "
    r"current_pid=(?P<current_pid>\d+) "
    r"stale_owner=(?P<stale_owner>\d+) "
    r"resume_ready=(?P<resume_ready>\d+)$"
)
PROCESS_EVENT_JOURNAL_PREFIX = "[PROC] process event journal "
PROCESS_EVENT_JOURNAL_RE = re.compile(
    r"^\[PROC\] process event journal (?P<status>\w+) "
    r"schema=(?P<schema>\d+) events=(?P<event_count>\d+) "
    r"lifecycle=(?P<lifecycle>\d+) captures=(?P<captures>\d+) "
    r"seqs=(?P<seqs>\d+(?:,\d+)*) "
    r"kinds=(?P<kinds>\d+(?:,\d+)*) "
    r"reasons=(?P<reasons>\d+(?:,\d+)*) "
    r"from_pids=(?P<from_pids>\d+(?:,\d+)*) "
    r"to_pids=(?P<to_pids>\d+(?:,\d+)*) "
    r"slots=(?P<slots>\d+(?:,\d+)*) "
    r"generations=(?P<generations>\d+(?:,\d+)*) "
    r"capture_seqs=(?P<capture_seqs>\d+(?:,\d+)*) "
    r"owner_ok=(?P<owner_ok>\d+(?:,\d+)*) "
    r"cr3_ok=(?P<cr3_ok>\d+(?:,\d+)*) "
    r"rsp0_ok=(?P<rsp0_ok>\d+(?:,\d+)*) "
    r"if0=(?P<if0>\d+(?:,\d+)*) "
    r"snapshot_refs=(?P<snapshot_refs>\d+(?:,\d+)*) "
    r"outcomes=(?P<outcomes>\d+(?:,\d+)*) "
    r"capture_seq_separate=(?P<capture_seq_separate>\d+) "
    r"current_pid=(?P<current_pid>\d+) "
    r"stale_owner=(?P<stale_owner>\d+) "
    r"dropped=(?P<dropped>\d+) overflow=(?P<overflow>\d+) "
    r"evidence_only=(?P<evidence_only>\d+) "
    r"switch_events=(?P<switch_events>\d+) "
    r"resume_ready=(?P<resume_ready>\d+)$"
)
ROOM_SNAPSHOT_RE = re.compile(
    r"\[ROOM\] snapshot stability=(?P<stability>\w+) ok=(?P<ok>\d+) degraded=(?P<degraded>\d+) failed=(?P<failed>\d+) unknown=(?P<unknown>\d+) topology=(?P<topology>[\w\-]+) domains=(?P<domains>\d+) windows=(?P<windows>\d+) drivers=(?P<drivers_ready>\d+)/(?P<drivers>\d+) plans=(?P<plans>\d+) nodes=(?P<nodes>\d+) rings=(?P<rings>\d+) active=(?P<active>\d+) user=(?P<user>\d+)"
    r"(?: nodebit_active=(?P<nodebit_active>\d+) nodebit_risky=(?P<nodebit_risky>\d+))?"
)
ROOM_GATES_RE = re.compile(
    r"\[ROOM\] gates total=(?P<total>\d+) stable_only=(?P<stable_only>\d+) completion=(?P<completion>\d+) shared=(?P<shared>\d+) risky_io=(?P<risky_io>\d+) observe=(?P<observe>\d+) control=(?P<control>\d+) data=(?P<data>\d+)"
)
RESOURCE_SELFTEST_RE = re.compile(
    r"^\[RESOURCE\] ledger selftest (?P<status>\w+) "
    r"schema=(?P<schema>\d+) kinds=(?P<kinds>\d+) units=(?P<units>\d+) "
    r"entries=(?P<entries>\d+) capacity=(?P<capacity>\d+) "
    r"source_flags=(?P<source_flags>\d+) "
    r"limit_kinds=(?P<limit_kinds>\d+) used_kinds=(?P<used_kinds>\d+) "
    r"high_water_kinds=(?P<high_water_kinds>\d+) "
    r"denied_kinds=(?P<denied_kinds>\d+) "
    r"owners_unattributed=(?P<owners_unattributed>\d+) "
    r"observation_only=(?P<observation_only>\d+)$"
)
PRESSURE_SELFTEST_RE = re.compile(
    r"^\[PRESSURE\] tracker selftest (?P<status>\w+) "
    r"schema=(?P<schema>\d+) planes=(?P<planes>\d+) "
    r"max_levels=(?P<max_levels>\d+) active_levels=(?P<active_levels>\d+) "
    r"balanced=(?P<balanced>\d+) hotspot=(?P<hotspot>\d+) "
    r"overlap=(?P<overlap>\d+) gate_mask=(?P<gate_mask>\d+) "
    r"observation_only=(?P<observation_only>\d+)$"
)


def _sanitize_lines(log_text: str) -> list[str]:
    lines = []
    for raw_line in log_text.splitlines():
        # Transport whitespace at the end is harmless, but leading whitespace
        # is contract-significant: an indented or quoted diagnostic copy must
        # not become an anchored evidence record during sanitization.
        clean = ANSI_ESCAPE_RE.sub("", raw_line).rstrip()
        if clean:
            lines.append(clean)
    return lines


def _line_info(lines: list[str], predicate) -> dict[str, object]:
    for index, line in enumerate(lines, start=1):
        if predicate(line):
            return {"seen": True, "line": index, "text": line}
    return {"seen": False, "line": None, "text": None}


def _search_match(lines: list[str], pattern: re.Pattern[str]):
    for index, line in enumerate(lines, start=1):
        match = pattern.search(line)
        if match:
            return index, line, match
    return None, None, None


def _int_groupdict(match: re.Match[str], *keys: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for key in keys:
        value = match.group(key)
        if value is not None:
            values[key] = int(value)
    return values


def _csv_int_group(match: re.Match[str], key: str) -> list[int]:
    return [int(value) for value in match.group(key).split(",")]


def _find_all_matches(lines: list[str], pattern: re.Pattern[str]) -> list[tuple[int, str, re.Match[str]]]:
    matches: list[tuple[int, str, re.Match[str]]] = []
    for index, line in enumerate(lines, start=1):
        match = pattern.search(line)
        if match:
            matches.append((index, line, match))
    return matches


def _parse_controller_states(lines: list[str]) -> dict[str, object]:
    controllers: dict[str, object] = {
        "network": {"state": "unknown"},
        "usb": {"state": "unknown"},
        "storage": {"state": "unknown"},
    }

    selection_index, selection_line, selection_match = _search_match(lines, NETWORK_SELECTION_RE)
    index, line, match = _search_match(lines, NETWORK_READY_RE)
    if match:
        controllers["network"] = {
            "state": "ready",
            "line": index,
            "text": line,
            "mmio": match.group("mmio"),
            "io": match.group("io"),
            "status": match.group("status"),
            "link": match.group("link"),
            "eeprom": int(match.group("eeprom")),
        }
    else:
        info = _line_info(lines, lambda candidate: "[NET] No Intel E1000-compatible controller found" in candidate)
        if info["seen"]:
            controllers["network"] = {"state": "absent", **info}
    if selection_match:
        controllers["network"]["selection"] = {
            "line": selection_index,
            "text": selection_line,
            "score": int(selection_match.group("score")),
            "candidates": int(selection_match.group("candidates")),
            "pci": selection_match.group("pci"),
            "device": selection_match.group("device"),
            "mmio_bars": int(selection_match.group("mmio_bars")),
            "io_bars": int(selection_match.group("io_bars")),
            "pcie": int(selection_match.group("pcie")),
        }

    selection_index, selection_line, selection_match = _search_match(lines, USB_SELECTION_RE)
    index, line, match = _search_match(lines, USB_READY_RE)
    if match:
        controllers["usb"] = {
            "state": "ready",
            "line": index,
            "text": line,
            "controller": match.group("controller"),
            "ready": int(match.group("ready")),
            "vendor": match.group("vendor"),
            "device": match.group("device"),
            "pci": match.group("pci"),
            "cmd": match.group("cmd"),
            "mmio": match.group("mmio"),
            "io": match.group("io"),
        }
    else:
        info = _line_info(lines, lambda candidate: "[USB] No USB host controller found" in candidate)
        if info["seen"]:
            controllers["usb"] = {"state": "absent", **info}
    if selection_match:
        controllers["usb"]["selection"] = {
            "line": selection_index,
            "text": selection_line,
            "controller": selection_match.group("controller"),
            "score": int(selection_match.group("score")),
            "candidates": int(selection_match.group("candidates")),
            "pci": selection_match.group("pci"),
            "mmio_bars": int(selection_match.group("mmio_bars")),
            "io_bars": int(selection_match.group("io_bars")),
            "pcie": int(selection_match.group("pcie")),
        }

    selection_index, selection_line, selection_match = _search_match(lines, STORAGE_SELECTION_RE)
    index, line, match = _search_match(lines, STORAGE_READY_RE)
    if match:
        storage = {
            "state": "ready",
            "line": index,
            "text": line,
            "controller": match.group("controller"),
            "ready": int(match.group("ready")),
            "vendor": match.group("vendor"),
            "device": match.group("device"),
            "pci": match.group("pci"),
            "cmd": match.group("cmd"),
            "mmio": match.group("mmio"),
            "io": match.group("io"),
        }
        channel_index, channel_line, channel_match = _search_match(lines, STORAGE_CHANNEL_RE)
        if channel_match:
            storage["channels"] = {
                "line": channel_index,
                "text": channel_line,
                "primary": {
                    "cmd": channel_match.group("primary_cmd"),
                    "ctl": channel_match.group("primary_ctl"),
                    "status": channel_match.group("primary_status"),
                    "live": int(channel_match.group("primary_live")),
                },
                "secondary": {
                    "cmd": channel_match.group("secondary_cmd"),
                    "ctl": channel_match.group("secondary_ctl"),
                    "status": channel_match.group("secondary_status"),
                    "live": int(channel_match.group("secondary_live")),
                },
            }
        controllers["storage"] = storage
    else:
        info = _line_info(lines, lambda candidate: "[STO] No storage controller found" in candidate)
        if info["seen"]:
            controllers["storage"] = {"state": "absent", **info}
    if selection_match:
        controllers["storage"]["selection"] = {
            "line": selection_index,
            "text": selection_line,
            "controller": selection_match.group("controller"),
            "score": int(selection_match.group("score")),
            "candidates": int(selection_match.group("candidates")),
            "pci": selection_match.group("pci"),
            "mmio_bars": int(selection_match.group("mmio_bars")),
            "io_bars": int(selection_match.group("io_bars")),
            "pcie": int(selection_match.group("pcie")),
        }

    return controllers


def parse_boot_log_text(log_text: str, smoke_profile: str, serial_log_path: str | None = None) -> dict[str, object]:
    lines = _sanitize_lines(log_text)
    checkpoints = {
        name: _line_info(lines, lambda candidate, needle=needle: needle in candidate)
        for name, needle in CHECKPOINT_PATTERNS.items()
    }

    selftest: dict[str, object] = {"metrics": {}}
    index, line, match = _search_match(lines, SELFTEST_RESULT_RE)
    if match:
        selftest.update(
            {
                "line": index,
                "text": line,
                "status": match.group("status"),
                "size_kib": int(match.group("size_kib")),
                "iterations": int(match.group("iterations")),
            }
        )
    for metric_index, metric_line, metric_match in _find_all_matches(lines, SELFTEST_METRIC_RE):
        selftest["metrics"][metric_match.group("name")] = {
            "line": metric_index,
            "text": metric_line,
            "cycles": int(metric_match.group("cycles")),
            "cyc_per_kib": int(metric_match.group("cyc_per_kib")),
        }

    profile: dict[str, object] = {}
    index, line, match = _search_match(lines, PROFILE_MAIN_RE)
    if match:
        profile.update(
            {
                "line": index,
                "text": line,
                "tsc_khz": int(match.group("tsc_khz")),
                "invariant": int(match.group("invariant")),
                "memcpy_mib_s": int(match.group("memcpy_mib_s")),
                "tier": match.group("tier"),
            }
        )
    cache_index, cache_line, cache_match = _search_match(lines, PROFILE_CACHE_RE)
    if cache_match:
        profile["cache"] = {
            "line": cache_index,
            "text": cache_line,
            "kib": _int_groupdict(cache_match, "l1_kib", "l2_kib", "l3_kib"),
            "latency_x100": _int_groupdict(
                cache_match,
                "l1_latency_x100",
                "l2_latency_x100",
                "l3_latency_x100",
                "dram_latency_x100",
            ),
        }

    device_summary: dict[str, object] | None = None
    index, line, match = _search_match(lines, DEVICE_SUMMARY_RE)
    if match:
        device_summary = {"line": index, "text": line, **_int_groupdict(match, "pci", "matched", "eth", "wifi", "bt", "usb", "storage")}

    health: dict[str, object] | None = None
    index, line, match = _search_match(lines, HEALTH_RE)
    if match:
        health = {"line": index, "text": line, **_int_groupdict(match, "ok", "degraded", "failed", "unknown", "io_degraded", "req_fail", "autonomy", "risky_io")}
        health["stability"] = match.group("stability")

    slm: dict[str, object] = {
        "ready": checkpoints["slm_orchestrator"]["seen"],
        "seeded_plan_count": 0,
        "seeded_labels": [],
    }
    index, line, match = _search_match(lines, SLM_MAIN_RE)
    if match:
        slm.update(
            {
                "line": index,
                "text": line,
                "mode": match.group("mode"),
                "sco": int(match.group("sco")),
                "workers": int(match.group("workers")),
                "pipeline_qd": int(match.group("pipeline_qd")),
                "depth": int(match.group("depth")),
                "ring_used": int(match.group("ring_used")),
                "ring_total": int(match.group("ring_total")),
            }
        )
    index, line, match = _search_match(lines, SLM_RUNTIME_RE)
    if match:
        slm["runtime"] = {
            "line": index,
            "text": line,
            "state": match.group("state"),
            "status": int(match.group("status")),
            "snapshot_abi": int(match.group("snapshot_abi")),
            "nodebits": int(match.group("nodebits")),
            "generation": int(match.group("generation")),
        }
    index, line, match = _search_match(lines, SLM_USER_AI_RE)
    if match:
        slm["user_ai_access"] = {
            "line": index,
            "text": line,
            "score": int(match.group("score")),
            "flags": match.group("flags"),
            "direct_mmio": int(match.group("direct_mmio")),
            "mediated": int(match.group("mediated")),
            "clock_pct": {
                "main": int(match.group("clock_main")),
                "worker": int(match.group("clock_worker")),
                "io": int(match.group("clock_io")),
                "memory": int(match.group("clock_memory")),
                "guardian": int(match.group("clock_guardian")),
                "reserve": int(match.group("clock_reserve")),
            },
            "slice_us": int(match.group("slice_us")),
            "poll_us": int(match.group("poll_us")),
        }
    seeded_labels: list[str] = []
    for line_text in lines:
        seeded_match = SLM_SEEDED_RE.search(line_text)
        if seeded_match:
            seeded_labels.append(seeded_match.group("label"))
    slm["seeded_plan_count"] = len(seeded_labels)
    slm["seeded_labels"] = seeded_labels

    user_mode: dict[str, object] = {"ready": checkpoints["ring3_scaffold"]["seen"]}
    index, line, match = _search_match(lines, USER_SCAFFOLD_RE)
    if match:
        user_mode.update(
            {
                "line": index,
                "text": line,
                "ready": int(match.group("ready")),
                "tr": match.group("tr"),
                "user_cs": match.group("user_cs"),
                "user_ds": match.group("user_ds"),
                "rsp0": match.group("rsp0"),
                "gdt_base": match.group("gdt_base"),
                "gdt_limit": int(match.group("gdt_limit")),
            }
        )

    user_access: dict[str, object] = {"ready": checkpoints["user_access"]["seen"]}
    index, line, match = _search_match(lines, USER_ACCESS_RE)
    if match:
        user_access.update(
            {
                "line": index,
                "text": line,
                "status": match.group("status"),
                "structural": int(match.group("structural")),
                "copy": int(match.group("copy")),
                "zero_copy": int(match.group("zero_copy")),
                "string": int(match.group("string") or 0),
            }
        )

    process_stack: dict[str, object] = {
        "ready": checkpoints["bootstrap_process"]["seen"] and
                 checkpoints["bootstrap_process_stack"]["seen"]
    }
    index, line, match = _search_match(lines, PROCESS_OWNERSHIP_RE)
    if match:
        process_stack["ownership"] = {
            "line": index,
            "text": line,
            "status": match.group("status"),
            **_int_groupdict(
                match, "slots", "owned", "stack_bytes", "unique_cr3",
                "unique_backing", "unique_stack"
            ),
        }
    index, line, match = _search_match(lines, PROCESS_STACK_RE)
    if match:
        process_stack["execution"] = {
            "line": index,
            "text": line,
            "status": match.group("status"),
            **_int_groupdict(
                match, "pid", "slot", "process_bound", "kstack_bytes",
                "rsp0_changed", "rsp0_published", "int80_entries",
                "all_int80_entries_in_stack", "rsp0_restored",
                "kstack_floor_canary"
            ),
        }

    pair_checkpoint_seen = checkpoints["bootstrap_process_pair"]["seen"]
    process_pair: dict[str, object] = {
        "ready": False,
        "checkpoint_seen": pair_checkpoint_seen,
    }
    index, line, match = _search_match(lines, PROCESS_PAIR_RE)
    if match:
        process_pair.update(
            {
                "ready": (
                    pair_checkpoint_seen and match.group("status") == "PASS"
                ),
                "line": index,
                "text": line,
                "status": match.group("status"),
                "order": match.group("order"),
                **_int_groupdict(
                    match, "runs", "pid_a", "slot_a", "pid_b", "slot_b",
                    "distinct_pid", "distinct_slot", "distinct_cr3",
                    "distinct_backing", "distinct_stack", "int80_a",
                    "int80_b", "between_clean", "current_pid", "last_pid",
                    "rsp0_publishes", "rsp0_restores",
                    "tss_rsp0_baseline", "both_restored"
                ),
            }
        )

    snapshot_checkpoint_seen = checkpoints["process_trap_snapshot"]["seen"]
    snapshot_prefix_records = [
        (index, line)
        for index, line in enumerate(lines, start=1)
        if line.startswith(PROCESS_TRAP_SNAPSHOT_PREFIX)
    ]
    snapshot_matches = _find_all_matches(lines, PROCESS_TRAP_SNAPSHOT_RE)
    process_trap_snapshot: dict[str, object] = {
        "ready": False,
        "checkpoint_seen": snapshot_checkpoint_seen,
        "record_count": len(snapshot_prefix_records),
        "fullmatch_count": len(snapshot_matches),
        "duplicate": len(snapshot_prefix_records) > 1,
    }
    if len(snapshot_matches) == 1:
        index, line, match = snapshot_matches[0]
        values = _int_groupdict(
            match, "schema", "captures", "pid_a", "slot_a", "seq_a",
            "valid_a", "owner_a", "frame_a", "cr3_a", "rsp0_a",
            "pid_b", "slot_b", "seq_b", "valid_b", "owner_b",
            "frame_b", "cr3_b", "rsp0_b", "distinct_storage",
            "current_pid", "stale_owner", "resume_ready"
        )
        expected = {
            "schema": 1, "captures": 2,
            "pid_a": 1, "slot_a": 0, "seq_a": 1,
            "valid_a": 1, "owner_a": 1, "frame_a": 1,
            "cr3_a": 1, "rsp0_a": 1,
            "pid_b": 2, "slot_b": 1, "seq_b": 2,
            "valid_b": 1, "owner_b": 1, "frame_b": 1,
            "cr3_b": 1, "rsp0_b": 1, "distinct_storage": 1,
            "current_pid": 0, "stale_owner": 0, "resume_ready": 0,
        }
        process_trap_snapshot.update(
            {
                "ready": (
                    snapshot_checkpoint_seen and
                    len(snapshot_prefix_records) == 1 and
                    len(snapshot_matches) == 1 and
                    match.group("status") == "PASS" and
                    values == expected
                ),
                "line": index,
                "text": line,
                "status": match.group("status"),
                **values,
            }
        )

    journal_checkpoint_seen = checkpoints["process_event_journal"]["seen"]
    journal_prefix_records = [
        (index, line)
        for index, line in enumerate(lines, start=1)
        if line.startswith(PROCESS_EVENT_JOURNAL_PREFIX)
    ]
    journal_matches = _find_all_matches(lines, PROCESS_EVENT_JOURNAL_RE)
    process_event_journal: dict[str, object] = {
        "ready": False,
        "checkpoint_seen": journal_checkpoint_seen,
        "record_count": len(journal_prefix_records),
        "fullmatch_count": len(journal_matches),
        "duplicate": len(journal_prefix_records) > 1,
        "events": [],
    }
    if len(journal_matches) == 1:
        index, line, match = journal_matches[0]
        scalar_values = _int_groupdict(
            match,
            "schema", "event_count", "lifecycle", "captures",
            "capture_seq_separate", "current_pid", "stale_owner",
            "dropped", "overflow", "evidence_only", "switch_events",
            "resume_ready",
        )
        vector_keys = (
            "seqs", "kinds", "reasons", "from_pids", "to_pids",
            "slots", "generations", "capture_seqs", "owner_ok",
            "cr3_ok", "rsp0_ok", "if0", "snapshot_refs", "outcomes",
        )
        vectors = {
            key: _csv_int_group(match, key)
            for key in vector_keys
        }
        vector_lengths = {
            key: len(values)
            for key, values in vectors.items()
        }
        event_count = scalar_values["event_count"]
        expected_event_count = 6
        lengths_match = event_count == expected_event_count and all(
            length == expected_event_count
            for length in vector_lengths.values()
        )
        ordered = (
            event_count == expected_event_count and
            vectors["seqs"] == [1, 2, 3, 4, 5, 6]
        )
        unknown_kind_ids = sorted(set(vectors["kinds"]) - {1, 2, 3})
        unknown_reason_ids = sorted(set(vectors["reasons"]) - {1, 2, 3})
        unknown_outcome_ids = sorted(set(vectors["outcomes"]) - {1})
        event_rows = [
            {
                "sequence": vectors["seqs"][event_index],
                "kind": vectors["kinds"][event_index],
                "reason": vectors["reasons"][event_index],
                "from_pid": vectors["from_pids"][event_index],
                "to_pid": vectors["to_pids"][event_index],
                "slot": vectors["slots"][event_index],
                "generation": vectors["generations"][event_index],
                "capture_sequence": vectors["capture_seqs"][event_index],
                "owner_ok": vectors["owner_ok"][event_index],
                "cr3_ok": vectors["cr3_ok"][event_index],
                "rsp0_ok": vectors["rsp0_ok"][event_index],
                "if0": vectors["if0"][event_index],
                "snapshot_ref": vectors["snapshot_refs"][event_index],
                "outcome": vectors["outcomes"][event_index],
            }
            for event_index in range(
                min(expected_event_count, min(vector_lengths.values()))
            )
        ]
        expected_scalars = {
            "schema": 1,
            "event_count": 6,
            "lifecycle": 4,
            "captures": 2,
            "capture_seq_separate": 1,
            "current_pid": 0,
            "stale_owner": 0,
            "dropped": 0,
            "overflow": 0,
            "evidence_only": 1,
            "switch_events": 0,
            "resume_ready": 0,
        }
        expected_vectors = {
            "seqs": [1, 2, 3, 4, 5, 6],
            "kinds": [1, 2, 3, 1, 2, 3],
            "reasons": [1, 2, 3, 1, 2, 3],
            "from_pids": [0, 1, 1, 0, 2, 2],
            "to_pids": [1, 1, 0, 2, 2, 0],
            "slots": [0, 0, 0, 1, 1, 1],
            "generations": [1, 1, 1, 1, 1, 1],
            "capture_seqs": [0, 1, 1, 0, 2, 2],
            "owner_ok": [1, 1, 1, 1, 1, 1],
            "cr3_ok": [1, 1, 1, 1, 1, 1],
            "rsp0_ok": [1, 1, 1, 1, 1, 1],
            "if0": [1, 1, 1, 1, 1, 1],
            "snapshot_refs": [0, 1, 1, 0, 1, 1],
            "outcomes": [1, 1, 1, 1, 1, 1],
        }
        process_event_journal.update(
            {
                "ready": (
                    journal_checkpoint_seen and
                    len(journal_prefix_records) == 1 and
                    len(journal_matches) == 1 and
                    match.group("status") == "PASS" and
                    scalar_values == expected_scalars and
                    vectors == expected_vectors and
                    lengths_match and ordered and
                    not unknown_kind_ids and
                    not unknown_reason_ids and
                    not unknown_outcome_ids
                ),
                "line": index,
                "text": line,
                "status": match.group("status"),
                "ordered": ordered,
                "lengths_match": lengths_match,
                "vector_lengths": vector_lengths,
                "unknown_kind_ids": unknown_kind_ids,
                "unknown_reason_ids": unknown_reason_ids,
                "unknown_outcome_ids": unknown_outcome_ids,
                "events": event_rows,
                **scalar_values,
                **vectors,
            }
        )

    kernel_room: dict[str, object] = {"ready": checkpoints["kernel_room"]["seen"]}
    index, line, match = _search_match(lines, ROOM_SNAPSHOT_RE)
    if match:
        kernel_room.update(
            {
                "line": index,
                "text": line,
                "stability": match.group("stability"),
                "topology": match.group("topology"),
                **_int_groupdict(
                    match,
                    "ok",
                    "degraded",
                    "failed",
                    "unknown",
                    "domains",
                    "windows",
                    "drivers_ready",
                    "drivers",
                    "plans",
                    "nodes",
                    "rings",
                    "active",
                    "user",
                    "nodebit_active",
                    "nodebit_risky",
                ),
            }
        )
    gate_index, gate_line, gate_match = _search_match(lines, ROOM_GATES_RE)
    if gate_match:
        kernel_room["gates"] = {
            "line": gate_index,
            "text": gate_line,
            **_int_groupdict(
                gate_match,
                "total",
                "stable_only",
                "completion",
                "shared",
                "risky_io",
                "observe",
                "control",
                "data",
            ),
        }

    shell_info: dict[str, object] = {
        "started": checkpoints["shell"]["seen"],
        "boot_complete": checkpoints["boot_complete"]["seen"],
    }

    nodebit_info: dict[str, object] = {
        "ready": checkpoints["nodebit"]["seen"],
    }

    resource: dict[str, object] = {
        "ready": False,
        "checkpoint_seen": checkpoints["resource_ledger"]["seen"],
    }
    index, line, match = _search_match(lines, RESOURCE_SELFTEST_RE)
    if match:
        fields = _int_groupdict(
            match,
            "schema",
            "kinds",
            "units",
            "entries",
            "capacity",
            "source_flags",
            "limit_kinds",
            "used_kinds",
            "high_water_kinds",
            "denied_kinds",
            "owners_unattributed",
            "observation_only",
        )
        resource.update(
            {
                "line": index,
                "text": line,
                "status": match.group("status"),
                **fields,
            }
        )
        resource["ready"] = (
            resource["checkpoint_seen"]
            and match.group("status") == "PASS"
            and fields == {
                "schema": 1,
                "kinds": 5,
                "units": 2,
                "entries": 5,
                "capacity": 8,
                "source_flags": 31,
                "limit_kinds": 5,
                "used_kinds": 5,
                "high_water_kinds": 1,
                "denied_kinds": 0,
                "owners_unattributed": 1,
                "observation_only": 1,
            }
        )

    pressure: dict[str, object] = {
        "ready": False,
        "checkpoint_seen": checkpoints["pressure_tracker"]["seen"],
    }
    index, line, match = _search_match(lines, PRESSURE_SELFTEST_RE)
    if match:
        fields = _int_groupdict(
            match,
            "schema",
            "planes",
            "max_levels",
            "active_levels",
            "balanced",
            "hotspot",
            "overlap",
            "gate_mask",
            "observation_only",
        )
        pressure.update(
            {
                "line": index,
                "text": line,
                "status": match.group("status"),
                **fields,
            }
        )
        pressure["ready"] = (
            pressure["checkpoint_seen"]
            and match.group("status") == "PASS"
            and fields == {
                "schema": 1,
                "planes": 3,
                "max_levels": 4,
                "active_levels": 2,
                "balanced": 1,
                "hotspot": 1,
                "overlap": 1,
                "gate_mask": 1,
                "observation_only": 1,
            }
        )

    summary = {
        "smoke_profile": smoke_profile,
        "serial_log": serial_log_path,
        "line_count": len(lines),
        "checkpoints": checkpoints,
        "selftest": selftest,
        "profile": profile,
        "device_summary": device_summary,
        "health": health,
        "controllers": _parse_controller_states(lines),
        "slm": slm,
        "user_mode": user_mode,
        "user_access": user_access,
        "process_stack": process_stack,
        "process_pair": process_pair,
        "process_trap_snapshot": process_trap_snapshot,
        "process_event_journal": process_event_journal,
        "kernel_room": kernel_room,
        "shell": shell_info,
        "nodebit": nodebit_info,
        "resource": resource,
        "pressure": pressure,
    }
    return summary


def parse_boot_log_file(path: Path, smoke_profile: str) -> dict[str, object]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return parse_boot_log_text(text, smoke_profile, str(path))


def boot_summary_path(target: str, smoke_profile: str) -> Path:
    return BUILD_DIR / "boot-summary" / f"{target}-{smoke_profile}.json"


def write_boot_summary(summary: dict[str, object], target: str, smoke_profile: str) -> Path:
    output_path = boot_summary_path(target, smoke_profile)
    ensure_dir(output_path.parent)
    output_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return output_path
