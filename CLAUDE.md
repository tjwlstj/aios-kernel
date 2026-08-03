# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

> Repository layout: this is a **monorepo with 6 domains** (`kernel/`, `os/`, `models/`, `store/`,
> `tools/`, `docs/`). Read [PROJECT.md](PROJECT.md) for the domain map, dependency-direction rules,
> and the "where do I put X?" guide before adding code.
>
> Repository-local AI workflows live in [AGENTS.md](AGENTS.md) and
> [.agents/README.md](.agents/README.md). Their `aios-*` skills capture kernel,
> ABI, driver, SLM, verification, documentation, workspace recovery, and
> beta-first checkpoint rules. Publish to `beta` first; move `main` only by
> fast-forward to the verified same SHA when explicitly authorized.

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
python -m unittest discover -s tools/testkit/tests -t tools/testkit -p "test_*.py" -v
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
`ping`, `state list|health|mem|sched|nodes|pipeline|resource|pressure|slm|autonomy|user|sec|time|version`. List-shaped topics
(`state nodes`) emit one summary line plus one `[STATE] node id=...` line per item;
every line still follows the key=value convention. Core pipeline/node/SLM/resource
observation surfaces are mirrored as userspace syscalls (`SYS_PIPE_STATS`,
`SYS_NODEBIT_STATS`, `SYS_SLM_PLAN_OBSERVE`, `SYS_INFO_RESOURCE`), and a post-init
selftest drives them through the real dispatcher (`[SYSCALL] observe dispatch selftest PASS`).
The autonomy support matrix and last event do not yet have one versioned snapshot
syscall. The `shell` testkit lane boots
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
- `heap.c` — general kernel heap; kmalloc/kfree/get_stats run under an IRQ-saving spinlock (`heap_lock_selftest` checks the locking invariants at boot). Ready for preemption/IRQ-context allocation.

**Interrupt (`kernel/interrupt/`)**
- `idt.c` — 32 CPU exceptions + legacy PIC IRQ0 (PIT timer at 100 Hz for scheduler bootstrap). The `#BP` gate is DPL=3: a ring3 `int3` is the CPL3 trapframe evidence path and `#BP` is the only survivable exception.
- `isr_stub.asm` — assembly stubs that push the full 176-byte trapframe (15 GPRs + vector/error + CPU iretq tail) and call C handlers.
- `trapframe.c` / `include/interrupt/trapframe.h` — M3-b-3b2c entry-gate contract: every `interrupt_frame_t` offset and the 176-byte size are static-asserted and mirrored as NASM byte counts; `interrupt_frame_from_user` discriminates CPL0/CPL3 by CS RPL. A boot selftest fires a canary-loaded `int3` through the real stub and proves all 15 GPR offsets, exact RIP/RSP, and the exact frame address (`[TRAP] frame contract selftest PASS`). The armed one-shot capture consumes expected breakpoints quietly (the verdict scans the whole log for exception dumps); it is a single-BSP best-effort contract. Long mode aligns RSP down to 16 bytes before pushing a same-CPL frame — the CPL0 expected frame address is `(rsp & ~15) - 176`, while a CPL3 entry lands exactly at `stack_top - 176`.

**Scheduler (`kernel/sched/ai_sched.c`)**
- MLFQ + CFS-inspired fairness, 256-task slots. This is a **workload accounting** model (vruntime bookkeeping); it does not switch CPU context.
- Metadata for deadline-aware inference tasks and accelerator affinity.

**Kernel Threads (`kernel/sched/kthread.c`, `kthread_switch.asm`)**
- Real CPU context switching (M3-b), distinct from the workload scheduler. `kthread_switch` swaps callee-saved registers + rsp between threads; `kthread_init` builds an initial frame that returns straight to the thread entry.
- `kthread_selftest` runs a cooperative ping-pong (two threads, each with its own stack) whose per-stack loop counters prove correct save/restore — `[SCHED] context switch selftest PASS`.
- **Preemption**: the timer IRQ handler calls `kthread_preempt_tick` (after EOI) which round-robins between runnable kernel threads via `kthread_switch`. `kthread_preempt_selftest` proves it: two workers that never yield both make progress — `[SCHED] preempt selftest PASS`. Observable via `state sched` (`kthread_switches`, `preempt_ticks`).
- Invariants for this path: send the timer EOI **before** any preemptive switch; a freshly-switched thread inherits IF=0, so worker/thread entries must `sti` themselves to be preemptible; never `sti` before the PIC is remapped (subsystem 7) — IRQ0 would arrive as vector 8 (#DF).
- A bounded static bootstrap process layer binds each of two private-CR3 slots to process-local run state and a unique 16KiB ring0 entry stack. PID 1 / slot 0 and PID 2 / slot 1 now complete separate synchronous runs in order, with clean ownership/CR3/TSS restoration between them. The trapframe C/NASM contract and `from_user` discrimination are proven (M3-b-3b2c entry gate, 2026-08-02); trapframe-based saved-state switching between two processes remains the next M3-b sub-slice.

**Hardware Abstraction (`kernel/hal/accel_hal.c`)**
- PCI enumeration + accelerator abstraction (GPU/TPU/NPU/FPGA/CPU-SIMD).
- 16-device slots; CPU SIMD fallback via SSE/AVX.

**Ring3 Execution (`kernel/core/user_exec.c`, `kernel/core/user_entry.asm`, `kernel/core/elf_loader.c`)**
- First real userspace slice: activates each static private address-space slot in turn for the same fixed 2MiB user VA region at 64MiB (U/S bit set at private PML4/PDPT/PDE — user access is the AND across all levels), loads a static ELF64 image into the slot via `elf_load` (validates the header, maps PT_LOAD segments to their `p_vaddr`, zeroes the `.bss` tail), enters CPL3 via `iretq` at `e_entry`, and runs a tiny program that calls back through `int 0x80`.
- The demo user program is a real ELF64 image hand-assembled in `user_entry.asm` (`user_elf_image_start/end`) — no second link step, works identically under `make` and the Windows build.
- The synchronous pair runner executes PID 1 / slot 0 and then PID 2 / slot 1. Each descriptor binds its own private CR3, process-local run state, backing, and 16KiB ring0 entry stack. Every teardown restores BSP boot-TSS `rsp0`, exact boot CR3, sealed/scrubbed leaf state, and clears current ownership with IF=0 before caller IF is restored. The inter-run checkpoint must be clean before PID 2 starts. This is not yet a concurrent or generally schedulable process model. Per-segment 4KiB W^X is a later step (the active user region is temporarily one W^X+U huge page).
- `int 0x80` gate is DPL=3 (`idt.c`, vector 0x80) routed to `isr_syscall`, which re-maps ring3 args (rax=num, rdi/rsi/rdx/r10/r8) to `ai_syscall_dispatch`; `rax==0` restores the launcher resume RSP stored in process-owned run state. C/NASM run-state offsets in `process.h` / `user_entry.asm` are an append-only internal ABI.
- `syscall_stack_top` in `boot.asm` remains the BSP boot-TSS baseline/fallback. Before each runner enters CPL3, it publishes that process's stack top to BSP `rsp0` with IF=0; all three demo `int 0x80` entries per process are proven to begin at exactly `stack_top-40`, then the baseline `rsp0` is restored. This is one BSP boot TSS, not SMP/per-CPU TSS support; the stack floor check is an 8-byte canary, not a guard page.
- The demo program calls `SYS_PIPE_STATS` into a user buffer, issues one canary-loaded `int3` (the CPL3 trapframe evidence — it does not touch the int80 counters, so `int80_entries` stays 3), attempts a hostile kernel-range pointer, then `exit(42)`; the kernel verifies the buffer holds the real registry capacity — proof of a full round trip. Per run the armed capture must show a ring3 frame (`cs=0x23 ss=0x1b`, user RSP/RIP, canaries intact) landing exactly at that process's `stack_top - 176` (`[TRAP] user frame capture PASS`). Results are observable via `state user` (`trap_*` fields).
- The active user page is the one intentional W^X+U exception; keep it temporary and single-region, then reset the executable policy, scrub it, and enforce NX when the CPU supports it.

**Runtime & Syscall Interface (`kernel/runtime/`)**
- `ai_syscall.c` — syscall dispatcher; syscall number ranges are ABI-stable, do not renumber.
- Groups: Model, Tensor, Inference, Training, Accelerator, Pipeline, Info, Autonomy, SLM/NodeBit.
- `autonomy.c` — bounded autonomy control plane. The shell's read-only `state autonomy`
  schema 1 exposes the current mode, target support matrix, counters, and last decision/reason;
  it does not add a new action or bypass the default observation-only gate. The agent-facing
  contract and intentional M6 authorization gap are in
  `docs/autonomy/agent_operating_contract_ko.md`.
- `slm_orchestrator.c` — 84 KB hardware + SLM snapshot, plan submit/validate/rollback. Plan *apply* is TSC-timed into a high-precision observation rollup (apply ok/failed/rejected, last/min/avg/max latency ns); read via `slm_plan_observation_read` or the shell's `state slm`. A boot selftest applies one read-only CORE_AUDIT plan — the only automated coverage of the apply path.
- `nodebit.c` — fast per-node policy bitmap lookup (`SYS_SLM_NODEBIT_LOOKUP`). Every gate decision is timed with the TSC-backed monotonic clock into per-node stats (permits/denies/health blocks, gate latency min/avg/max ns, attributed work via `nodebit_observe_work`); read them with `SYS_NODEBIT_STATS` or the shell's `state nodes`.
- `node_pipeline.c` — node-owned pipeline registry backing `SYS_PIPE_*` (0x600-0x603); every create/add-stage/execute/destroy needs a NodeBit PERMIT with `NODEBIT_CAP_PIPELINE`, and execute/destroy require the caller's node to own the pipeline. Stage execution is a control-plane accounting walk until the model runtime lands.
- `ai_resource.c` — schema 1 observation-only aggregate ledger. It exposes five append-only rows (heap bytes, tensor bytes, active Memory Fabric windows, registered inference rings, runnable scheduler tasks) through an internal fixed snapshot. Owners remain `NONE/UNATTRIBUTED`; only tensor has a source-native high-water value. Read it via the read-only `SYS_INFO_RESOURCE` (0x706) syscall or the shell's `state resource`; owner attribution and any quota, denial accounting, reserve, or apply edge still do not exist.
- `ai_pressure.c` — schema 1 observation-only pressure tracker. It reads exact workload queue occupancy, exact Memory Fabric reader/writer overlap, and cumulative NodeBit denial counters into a fixed-point system→plane snapshot. `max_levels=4` is expansion capacity; only `active_levels=2` is current. Gate bitmap eligibility remains a separate intersection, and no scheduler apply/migration edge consumes this snapshot yet. Read it with `state pressure`.

**Kernel Room (`kernel/core/kernel_room.c`)**
- 9 gate descriptors mapping syscall ranges to risk classifications (OBSERVE / BOUNDED_CONTROL / BOUNDED_DATA / IO_PATH).
- Gate count must match the enum exactly, and gate ranges must cover every defined syscall number — extend the covering gate's `syscall_end` when adding syscalls.
- The gate table is **classification metadata** consumed by the ROOM snapshot; the dispatcher does not check it per call. Runtime enforcement lives in the NodeBit capability gate (`nodebit_evaluate`), the autonomy safe-mode, and the health flags (`autonomy_allowed` / `risky_io_allowed`). Per-syscall gate enforcement is future work — do not describe it as existing.

**Health (`kernel/core/health.c`)**
- Produces stability snapshots: HEALTHY / DEGRADED / CRITICAL.
- Exposed via `SYS_SLM_HW_SNAPSHOT` and the bootstrap ABI.

**Drivers (`kernel/drivers/`)**
- VGA (80×25 text), serial COM1 115200 8N1, PS/2 keyboard, PCI core, e1000 NIC, xHCI USB, storage host, generic driver model.

### Smoke Test Checkpoints (CI-verified)
A successful boot must emit all of:
```
[BOOT] Multiboot2 handoff PASS
[TRAP] frame contract selftest PASS size=176 canaries=15 int_no=3 err=0 cpl0=1 cs_match=1 ss_match=1 rip_exact=1 rsp_exact=1 frame_addr_exact=1 rflags_bit1=1 df_clear=1
[TIMER] PIT IRQ ready
[SELFTEST] Memory microbench PASS
[HEAP] lock selftest PASS
[SCHED] context switch selftest PASS
[SCHED] preempt selftest PASS
[MM] address space selftest PASS
[MM] user leaf isolation selftest PASS
[MM] bootstrap user tensor exclusion PASS
[PROC] bootstrap ownership selftest PASS slots=2 owned=2 stack_bytes=16384 unique_cr3=1 unique_backing=1 unique_stack=1
[DEV] Peripheral probe ready
[HEALTH] stability=...
[PIPE] Node pipeline ready
[PIPE] selftest PASS
[RESOURCE] ledger selftest PASS schema=1 kinds=5 units=2 entries=5 capacity=8 source_flags=31 limit_kinds=5 used_kinds=5 high_water_kinds=1 denied_kinds=0 owners_unattributed=1 observation_only=1
[PRESSURE] tracker selftest PASS schema=1 planes=3 max_levels=4 active_levels=2 balanced=1 hotspot=1 overlap=1 gate_mask=1 observation_only=1
[SLM] plan apply selftest PASS
[SYSCALL] observe dispatch selftest PASS
[USER] ring3 exec PASS
[USER] private address space exec PASS slot=0 cr3_restored=1 if_restored=1 leaf_sealed=1 nx_enforced=1 tensor_excluded=1
[USER] bootstrap process stack PASS pid=1 slot=0 process_bound=1 kstack_bytes=16384 rsp0_changed=1 rsp0_published=1 int80_entries=3 all_int80_entries_in_stack=1 rsp0_restored=1 kstack_floor_canary=1
[USER] secondary process exec PASS pid=2 slot=1
[USER] bootstrap process pair PASS runs=2 order=1,2 pid_a=1 slot_a=0 pid_b=2 slot_b=1 ... between_clean=1 current_pid=0 last_pid=2 ... both_restored=1
[TRAP] user frame capture PASS pid_a=1 pid_b=2 captures_a=1 captures_b=1 from_user=1 cs=0x23 ss=0x1b rsp_user=1 rip_user=1 canary_ok=1 frame_in_kstack=1 frame_addr_exact=1 contract=1
[SHELL] Interactive shell started
[USER] Ring3 scaffold ready=1
[ROOM] snapshot stability=...
```

Required-marker presence alone is not a PASS. Normal verdict v1 scans the whole
serial log for panic/exception/uppercase `FAIL` or `FATAL`, requires stable health,
anchors evidence to line/token boundaries, rejects duplicate keys, and enforces
the terminal checkpoint chain exactly once and in order. The shell lane also
requires same-record state evidence, a drained reader, reboot acknowledgement,
the whole-transcript boot verdict, and QEMU exit code 0. Run the host unit tests
before QEMU; the authoritative contract and remaining limitations are in
`docs/tools/verification_tooling_evolution_design_ko.md`.

### Compiler Flags (non-obvious)
C sources are compiled with `-ffreestanding -nostdlib -mno-sse -mno-mmx -mno-red-zone -mcmodel=kernel -fno-pic -fno-pie`. Do not add `-msse` or enable SSE implicitly — the kernel manually enables SIMD after CPU init.

Stack protector is ON (`-fstack-protector-strong -mstack-protector-guard=global`); the runtime lives in `kernel/core/stack_guard.c`. Functions that swap the live canary must carry `__attribute__((no_stack_protector))`.

### Hardening Baseline (do not regress)
- NX/W^X: boot.asm marks every 2MiB identity-map page outside kernel `.text` as NX (EFER.NXE). New executable regions require explicit page-table changes. `leaf_sealed=1` means the bootstrap slot policy was reset and its backing scrubbed; `nx_enforced` separately reports hardware enforcement. Runtime remains honest on a CPU without active NX (`nx_enforced=0`), while the supported QEMU smoke baseline requires `nx_enforced=1`.
- SMEP/UMIP/SMAP enabled when the CPU supports them (`kernel/core/cpu_sec.c`, `[SEC]` boot lines). SMAP is backed by STAC/CLAC in the uaccess copies and `user_access_fence_begin/end` for deliberate user-page staging; QEMU's default CPU has no SMAP (`smap=0`), use `-cpu max` to exercise it (`smap=1`).
- uaccess user window: during a ring3 run, `access_ok` requires buffers to lie inside the registered user window, so a ring3-supplied pointer into kernel memory is denied (`[USER] ... boundary_ok=1`). Kernel-internal uaccess runs with no window and is unaffected. Any direct kernel touch of a user page outside `copy_*_user` must be wrapped in `user_access_fence_begin/end`.
- All CPU exceptions except `#BP` panic; `#PF` dumps CR2; `#DF` runs on TSS IST1. Interrupt gates preserve DF, so both the common ISR C boundary and the `int 0x80` C boundary must retain their explicit `cld`.
- CI runs cppcheck (`--enable=warning,performance,portability --error-exitcode=1`); keep it clean. Local: `cppcheck --std=c11 --platform=unix64 --enable=warning,performance,portability --inline-suppr --suppress=missingIncludeSystem --error-exitcode=1 -Ikernel/include kernel/`
- Windows local fallback when Cppcheck is installed outside `PATH`: `& 'C:\Program Files\Cppcheck\cppcheck.exe' --std=c11 --platform=unix64 --enable=warning,performance,portability --inline-suppr --suppress=missingIncludeSystem --error-exitcode=1 -Ikernel/include kernel/`
- Details and remaining roadmap: `docs/meta/hardening_baseline_2026_07_02_ko.md`.

### Handoff Tips (landmines)
Hard-won invariants and debugging notes for this kernel (sti/PIC ordering, IF
inheritance on context switch, SMAP fences, 4-level user page bits, #DF frames,
the two-scheduler and two-NodeBit splits) are collected in
`docs/meta/codex_handoff_tips_ko.md`. Read it before touching the ring3 /
scheduler / paging / syscall paths.

### Maturity Levers Backlog
Cross-cutting quality levers (versioned machine-readable events, structural
subsystem-count guard, fault-injection gate, UBSan lane, 4K W^X) with priority,
owner, and Claude/Codex alignment points are in
`docs/meta/maturity_levers_backlog_ko.md`. Check it before proposing new
verification or hardening work so it stays deduplicated against the verdict
design doc (V0-V5) and the workflow guide (M-levels).

### Current Workflow Plan
The maturity-first roadmap (M1 uaccess/SMAP → M2 ELF loader → M3 preemption →
M4 virtio-blk storage read → M5 load programs from disk) and the per-step
working conventions (selftest marker + smoke pattern + `state` topic + shell
lane exchange + cppcheck clean) are pinned in
`docs/meta/minimal_io_and_maturity_workflow_ko.md`. Follow it when picking up
the next task. Before M3-b-3b2c or another high-risk kernel slice, also apply the
entry gate in `docs/tools/verification_tooling_evolution_design_ko.md`.

### Browser / Runtime Engine Roadmap
The browser-facing W1-W5 axis is defined in
`docs/os/browser_console_and_runtime_engine_roadmap_ko.md`. W1 is a planned
host-side COM1/WebSocket console and does not imply a kernel TCP/IP or HTTP
server. The long-term native runtime engine belongs in AIOS userspace after the
M3-M6 process, storage, disk-ELF, identity, and authorization foundations.
Browser-local x86 execution remains an optional research track, not a claimed
replacement for QEMU or the normal verification path.

## Directory Map (domains)

| Path | Purpose |
|---|---|
| `kernel/boot/` | Multiboot2 entry, GDT/IDT/paging bootstrap, long mode |
| `kernel/core/` | main, health, ACPI, time, shell, user_mode, bounded bootstrap process ownership, kernel_room, linker.ld |
| `kernel/interrupt/` | IDT + ISR stubs |
| `kernel/mm/` | tensor_mm, memory_fabric, heap |
| `kernel/sched/` | AI workload scheduler |
| `kernel/hal/` | Accelerator HAL |
| `kernel/runtime/` | ai_syscall, autonomy, ai_resource, ai_pressure, slm_orchestrator, nodebit |
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
- Kernel Room gate count must equal the gate enum size, and gate syscall ranges must cover the full syscall surface (`kernel/core/kernel_room.c`).
- AI syscall number ranges are ABI-stable — do not renumber or overlap them. This is the only
  contract between `kernel/` and `os/`.
- Health snapshot ABI must remain stable across builds (consumed by SLM orchestrator).
- AI resource kind/unit IDs are append-only. Keep aggregate owner IDs at `NONE/UNATTRIBUTED` until attribution exists, honor validity flags, and preserve `observation_only=1` until a separately authorized resource-control UAPI is verified.
- AI pressure schema/plane IDs are append-only; keep pressure ranking separate from gate eligibility and preserve `observation_only=1` until a separately verified apply path exists.
- `store/` downloads and autonomy actions must pass the NodeBit + Kernel Room gates.
- GPU/NPU driver code is scaffolding only; no real hardware interaction yet.
- Ring3 execution and a bounded static ELF64 loader exist as a first slice (fixed in-kernel demo image). Two static bootstrap descriptors own distinct private 2MiB slots and 16KiB ring0 entry stacks, and PID 1 / slot 0 then PID 2 / slot 1 run synchronously with exact cleanup between runs. The 176-byte trapframe C/NASM contract and CPL0/CPL3 `from_user` discrimination are proven on the real ISR path. General/dynamic process address spaces, trapframe-based process switching, two-process timer preemption, filesystem/disk-backed loading, and the learning promotion loop remain planned.
