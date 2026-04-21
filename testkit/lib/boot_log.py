from __future__ import annotations

import json
import re
from pathlib import Path

from lib.common import BUILD_DIR, ensure_dir


ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

CHECKPOINT_PATTERNS = {
    "multiboot": "[BOOT] Multiboot2 verified.",
    "idt": "[INIT] Interrupt Descriptor Table (IDT)... OK",
    "time_source": "[INIT] Kernel Time Source... OK",
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
    "ring3_scaffold": "[USER] Ring3 scaffold ready=1",
    "kernel_room": "[ROOM] snapshot stability=",
    "health": "[HEALTH] stability=",
    "ready": "AIOS Kernel Ready",
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
ROOM_SNAPSHOT_RE = re.compile(
    r"\[ROOM\] snapshot stability=(?P<stability>\w+) ok=(?P<ok>\d+) degraded=(?P<degraded>\d+) failed=(?P<failed>\d+) unknown=(?P<unknown>\d+) topology=(?P<topology>[\w\-]+) domains=(?P<domains>\d+) windows=(?P<windows>\d+) drivers=(?P<drivers_ready>\d+)/(?P<drivers>\d+) plans=(?P<plans>\d+) nodes=(?P<nodes>\d+) rings=(?P<rings>\d+) active=(?P<active>\d+) user=(?P<user>\d+)"
)
ROOM_GATES_RE = re.compile(
    r"\[ROOM\] gates total=(?P<total>\d+) stable_only=(?P<stable_only>\d+) completion=(?P<completion>\d+) shared=(?P<shared>\d+) risky_io=(?P<risky_io>\d+) observe=(?P<observe>\d+) control=(?P<control>\d+) data=(?P<data>\d+)"
)


def _sanitize_lines(log_text: str) -> list[str]:
    lines = []
    for raw_line in log_text.splitlines():
        clean = ANSI_ESCAPE_RE.sub("", raw_line).strip()
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
        "kernel_room": kernel_room,
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
