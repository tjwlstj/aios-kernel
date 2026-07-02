# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> Repository layout: this is a **monorepo with 6 domains** (`kernel/`, `os/`, `models/`, `store/`,
> `tools/`, `docs/`). Read [PROJECT.md](PROJECT.md) for the domain map, dependency-direction rules,
> and the "where do I put X?" guide before adding code.

## Build & Test Commands

Builds run from the **repository root**; the root `Makefile` delegates to `kernel/Makefile`.

### Linux / MSYS2
```bash
make all                  # Build kernel binary (kernel/build/aios-kernel.bin)
make iso                  # Build bootable ISO (kernel/build/aios-kernel.iso)
make test                 # Build + QEMU smoke test
make run                  # Run in QEMU with VGA + serial
make run-headless         # Run in QEMU with serial only
make debug                # Run with GDB stub (-s -S flags)
make os-smoke             # OS tool smoke (tools/testkit)
make help                 # List all targets
```

Use `CC=gcc-12 make all` if the default gcc is too old (the override propagates to the sub-make).

### Windows (PowerShell)
```powershell
pwsh -File .\tools\testkit\kernel\build-windows.ps1 -Target all
pwsh -File .\tools\testkit\kernel\build-windows.ps1 -Target test
pwsh -File .\tools\testkit\kernel\build-windows.ps1 -Target test -SmokeProfile minimal
```

The Windows build uses the cross-compiler at `../tools/x86_64-elf/bin/x86_64-elf-gcc`.

### Python Testkit (tools/)
```bash
python tools/testkit/aios-testkit.py all --strict
python tools/testkit/aios-testkit.py kernel --target test --strict
python tools/testkit/aios-testkit.py boot-matrix --profiles full minimal storage-only --strict
python tools/testkit/aios-testkit.py boot-inventory --profiles full minimal storage-only --strict
python tools/testkit/aios-testkit.py boot-perf --profiles full minimal storage-only --strict
python tools/testkit/aios-testkit.py shell --strict              # interactive shell lane
python tools/testkit/aios-testkit.py shell --strict --skip-build # reuse existing ISO
python tools/testkit/aios-testkit.py os   # OS tool smoke test
```

### Interactive Shell Lane (runtime observation channel)
The kernel shell reads from both the PS/2 keyboard and COM1 serial, so QEMU
`-serial stdio` gives a scriptable REPL into the running kernel. Machine-oriented
commands answer with single-line `[STATE] <topic> key=value...` responses:
`ping`, `state list|health|mem|nodes|pipeline|sec|time|version`. List-shaped topics
(`state nodes`) emit one summary line plus one `[STATE] node id=...` line per item;
every line still follows the key=value convention. The `shell` testkit lane boots
QEMU, drives these commands, asserts on the responses, and stores
`kernel/build/shell-smoke/{transcript.log,summary.json}`. When adding a new
`state` topic keep the response a single line with no spaces inside values, and
add an exchange to `DEFAULT_EXCHANGES` in `tools/testkit/lib/shell_lane.py`.

### Toolchain Requirements
- `nasm`, `gcc` (or `gcc-12`), `ld`, `qemu-system-x86_64`
- For ISO: `grub-pc-bin`, `xorriso`, `mtools`
- Build artifacts land in `kernel/build/`; serial log at `kernel/build/serial_output.log`

## Architecture Overview

**AIOS** is a bare-metal x86_64 kernel designed for AI/LLM workloads as first-class citizens. Current version: v0.2.0-beta.6 "Genesis".

### Boot → Kernel Flow
```
kernel/boot/boot.asm  (Multiboot2 entry, GDT, paging, SSE/AVX setup, long mode)
       │
       └─► kernel/core/main.c :: kernel_main()
               serial/console → ACPI → memory fabric → IDT → scheduler
               → HAL/PCI → platform probe → health snapshot
               → kernel room → user mode scaffold → syscall dispatcher
               → autonomy/SLM runtime → self-tests → interactive shell
```

### Key Subsystems (all under `kernel/`)

**Memory (`kernel/mm/`)**
- `tensor_mm.c` — 64-byte aligned tensor allocator with named pools (Tensor, Model, Inference, KV-Cache, etc.), best-fit + buddy, lifetime tagging (SHORT_TERM / LONG_TERM / REALTIME). The 64-byte alignment is a hard invariant for AVX-512 correctness.
- `memory_fabric.c` — per-agent memory domains (seeds), zero-copy shared windows, NUMA-ready.
- `heap.c` — general kernel heap.

**Interrupt (`kernel/interrupt/`)**
- `idt.c` — 32 CPU exceptions + legacy PIC IRQ0 (PIT timer at 100 Hz for scheduler bootstrap).
- `isr_stub.asm` — assembly stubs that push context and call C handlers.

**Scheduler (`kernel/sched/ai_sched.c`)**
- MLFQ + CFS-inspired fairness, 256-task slots.
- Metadata for deadline-aware inference tasks and accelerator affinity.

**Hardware Abstraction (`kernel/hal/accel_hal.c`)**
- PCI enumeration + accelerator abstraction (GPU/TPU/NPU/FPGA/CPU-SIMD).
- 16-device slots; CPU SIMD fallback via SSE/AVX.

**Runtime & Syscall Interface (`kernel/runtime/`)**
- `ai_syscall.c` — syscall dispatcher; syscall number ranges are ABI-stable, do not renumber.
- Groups: Model, Tensor, Inference, Training, Accelerator, Pipeline, Info, Autonomy, SLM/NodeBit.
- `autonomy.c` — autonomy levels L0 (observe) → L3 (learning).
- `slm_orchestrator.c` — 84 KB hardware + SLM snapshot, plan submit/validate/rollback.
- `nodebit.c` — fast per-node policy bitmap lookup (`SYS_SLM_NODEBIT_LOOKUP`). Every gate decision is timed with the TSC-backed monotonic clock into per-node stats (permits/denies/health blocks, gate latency min/avg/max ns, attributed work via `nodebit_observe_work`); read them with `SYS_NODEBIT_STATS` or the shell's `state nodes`.
- `node_pipeline.c` — node-owned pipeline registry backing `SYS_PIPE_*` (0x600-0x603); every create/add-stage/execute/destroy needs a NodeBit PERMIT with `NODEBIT_CAP_PIPELINE`, and execute/destroy require the caller's node to own the pipeline. Stage execution is a control-plane accounting walk until the model runtime lands.

**Kernel Room (`kernel/core/kernel_room.c`)**
- 9 gate descriptors mapping syscall ranges to risk classifications (OBSERVE / BOUNDED_CONTROL / BOUNDED_DATA / IO_PATH).
- Gate count must match the enum exactly; stability check runs before each syscall.

**Health (`kernel/core/health.c`)**
- Produces stability snapshots: HEALTHY / DEGRADED / CRITICAL.
- Exposed via `SYS_SLM_HW_SNAPSHOT` and the bootstrap ABI.

**Drivers (`kernel/drivers/`)**
- VGA (80×25 text), serial COM1 115200 8N1, PS/2 keyboard, PCI core, e1000 NIC, xHCI USB, storage host, generic driver model.

### Smoke Test Checkpoints (CI-verified)
A successful boot must emit all of:
```
[TIMER] PIT IRQ ready
[SELFTEST] Memory microbench PASS
[DEV] Peripheral probe ready
[HEALTH] stability=...
[PIPE] Node pipeline ready
[PIPE] selftest PASS
[SHELL] Interactive shell started
[USER] Ring3 scaffold ready=1
[ROOM] snapshot stability=...
```

### Compiler Flags (non-obvious)
C sources are compiled with `-ffreestanding -nostdlib -mno-sse -mno-mmx -mno-red-zone -mcmodel=kernel -fno-pic -fno-pie`. Do not add `-msse` or enable SSE implicitly — the kernel manually enables SIMD after CPU init.

Stack protector is ON (`-fstack-protector-strong -mstack-protector-guard=global`); the runtime lives in `kernel/core/stack_guard.c`. Functions that swap the live canary must carry `__attribute__((no_stack_protector))`.

### Hardening Baseline (do not regress)
- NX/W^X: boot.asm marks every 2MB identity-map page outside kernel `.text` as NX (EFER.NXE). New executable regions require explicit page-table changes.
- SMEP/UMIP enabled when the CPU supports them (`kernel/core/cpu_sec.c`, `[SEC]` boot lines). SMAP stays off until uaccess has stac/clac.
- All CPU exceptions except `#BP` panic; `#PF` dumps CR2; `#DF` runs on TSS IST1.
- CI runs cppcheck (`--enable=warning,performance,portability --error-exitcode=1`); keep it clean. Local: `cppcheck --std=c11 --platform=unix64 --enable=warning,performance,portability --inline-suppr --suppress=missingIncludeSystem --error-exitcode=1 -Ikernel/include kernel/`
- Details and remaining roadmap: `docs/meta/hardening_baseline_2026_07_02_ko.md`.

## Directory Map (domains)

| Path | Purpose |
|---|---|
| `kernel/boot/` | Multiboot2 entry, GDT/IDT/paging bootstrap, long mode |
| `kernel/core/` | main, health, ACPI, time, shell, user_mode, kernel_room, linker.ld |
| `kernel/interrupt/` | IDT + ISR stubs |
| `kernel/mm/` | tensor_mm, memory_fabric, heap |
| `kernel/sched/` | AI workload scheduler |
| `kernel/hal/` | Accelerator HAL |
| `kernel/runtime/` | ai_syscall, autonomy, slm_orchestrator, nodebit |
| `kernel/drivers/` | All device drivers |
| `kernel/lib/` | Freestanding string utilities |
| `kernel/include/` | Public headers, organized by subsystem |
| `kernel/Makefile` | Kernel build system (root Makefile delegates here) |
| `os/` | Userspace layer (main_ai, compat, runtime, tools) + `os/apps/` programs |
| `models/` | AI/SLM model manifests (weights are gitignored) |
| `store/` | Post-boot online driver/program/model download catalog |
| `tools/testkit/` | Python test orchestration + Windows PS1 build helper |
| `docs/` | Design docs, grouped by domain (`docs/<domain>/`) |
| `.github/workflows/` | GitHub Actions CI (linux-boot-check.yml) |

## Key Invariants

- Tensor allocations must remain 64-byte aligned (AVX-512 requirement).
- Kernel Room gate count must equal the gate enum size (`kernel/core/kernel_room.c`).
- AI syscall number ranges are ABI-stable — do not renumber or overlap them. This is the only
  contract between `kernel/` and `os/`.
- Health snapshot ABI must remain stable across builds (consumed by SLM orchestrator).
- `store/` downloads and autonomy actions must pass the NodeBit + Kernel Room gates.
- GPU/NPU driver code is scaffolding only; no real hardware interaction yet.
- Full userspace ELF loader and learning promotion loop are planned, not implemented.
