/*
 * AIOS Kernel - Interactive Kernel Shell
 * AI-Native Operating System
 *
 * A minimal line-oriented REPL that accepts input from both the PS/2
 * keyboard and the COM1 serial port (polled), so a human at the console
 * and an automated host on the other end of the serial line can drive
 * the same shell.  Implements basic line editing (backspace, Ctrl+C).
 *
 * Built-in commands (human-oriented, VGA output)
 * ----------------------------------------------
 *   help     — list available commands
 *   clear    — clear the screen
 *   version  — print kernel version string
 *   info     — print CPU/timer information
 *   mem      — print kernel heap statistics
 *   uptime   — print time since boot (ticks / Hz)
 *   reboot   — reset the machine via the keyboard controller
 *
 * Machine-oriented commands (single-line `[STATE]` responses, emitted to
 * both serial and VGA so a host-side harness can drive the kernel over
 * `-serial stdio` and assert on structured key=value output)
 * ----------------------------------------------
 *   ping             — liveness probe → `[STATE] pong ticks=<n>`
 *   state list       — enumerate available topics
 *   state health     — kernel health summary
 *   state mem        — heap statistics
 *   state sched      — kernel-thread context switches + workload task stats
 *   state nodes      — per-node gate/work observation (summary line +
 *                      one `[STATE] node id=...` line per active node)
 *   state pipeline   — node pipeline registry statistics
 *   state resource   — read-only aggregate AI resource ledger
 *   state pressure   — read-only scheduler/memory/policy pressure snapshot
 *   state slm        — SLM plan apply observation (high-precision timing)
 *   state autonomy   — autonomy mode, support matrix, counters, last decision
 *   state user       — first ring3 execution round-trip result
 *   state sec        — hardening status (nx/smep/umip/smap/canary)
 *   state time       — timer/TSC status
 *   state version    — kernel release
 */

#include <kernel/shell.h>
#include <kernel/time.h>
#include <kernel/health.h>
#include <kernel/cpu_sec.h>
#include <kernel/stack_guard.h>
#include <runtime/node_pipeline.h>
#include <runtime/nodebit.h>
#include <runtime/autonomy.h>
#include <runtime/ai_resource.h>
#include <runtime/ai_pressure.h>
#include <runtime/slm_orchestrator.h>
#include <kernel/user_exec.h>
#include <kernel/process.h>
#include <interrupt/trapframe.h>
#include <sched/kthread.h>
#include <sched/ai_sched.h>
#include <drivers/keyboard.h>
#include <drivers/vga.h>
#include <drivers/serial.h>
#include <mm/heap.h>
#include <mm/address_space.h>
#include <lib/string.h>

/* -------------------------------------------------------------------------
 * Version string (keep in sync with main.c defines)
 * ---------------------------------------------------------------------- */

#define SHELL_RELEASE  "0.2.0-beta.6"
#define SHELL_VERSION  SHELL_RELEASE " \"Genesis\""
#define SHELL_AUTONOMY_STATE_SCHEMA 1

/* -------------------------------------------------------------------------
 * Line-editing state
 * ---------------------------------------------------------------------- */

#define CMD_MAX  256

static char     cmd_buf[CMD_MAX];
static uint32_t cmd_len;

/* -------------------------------------------------------------------------
 * I/O helper
 * ---------------------------------------------------------------------- */

static inline void shell_outb(uint16_t port, uint8_t val) {
    __asm__ volatile ("outb %0, %1" : : "a"(val), "Nd"(port));
}

/* -------------------------------------------------------------------------
 * Prompt
 * ---------------------------------------------------------------------- */

static void shell_prompt(void) {
    console_write_color("aios", VGA_LIGHT_GREEN,  VGA_BLACK);
    console_write_color("# ",   VGA_LIGHT_GREY,   VGA_BLACK);
    serial_write("aios# ");
}

/* -------------------------------------------------------------------------
 * Machine-oriented `[STATE]` responses
 *
 * Every response is a single line, prefixed `[STATE]`, using key=value
 * fields with no embedded spaces in values, and is emitted to both the
 * serial port (for host-side harnesses) and the VGA console.
 * ---------------------------------------------------------------------- */

#define STATE_EMIT(fmt, ...) do {                  \
    serial_printf(fmt, ##__VA_ARGS__);             \
    kprintf(fmt, ##__VA_ARGS__);                   \
} while (0)

static void cmd_ping(void) {
    STATE_EMIT("[STATE] pong ticks=%u\n", kernel_timer_irq_ticks());
}

static void state_list(void) {
    STATE_EMIT("[STATE] topics list=health,mem,sched,nodes,pipeline,resource,pressure,slm,autonomy,user,sec,time,version\n");
}

static void state_sched(void) {
    sched_stats_t s;
    address_space_stats_t as;
    bootstrap_process_stats_t process;
    ai_sched_stats(&s);
    address_space_get_stats(&as);
    bootstrap_process_get_stats(&process);
    STATE_EMIT("[STATE] sched kthread_switches=%u preempt_ticks=%u address_space_switches=%u address_space_ready=%u user_leaf_slots=%u user_leaf_isolated=%u bootstrap_process_ready=%u bootstrap_owned_processes=%u completed_process_runs=%u current_pid=%u last_pid=%u tss_rsp0_publishes=%u tss_rsp0_restores=%u tss_rsp0_baseline=%u total_tasks=%u active_tasks=%u workload_ctx_switches=%u preemptions=%u\n",
        kthread_switch_count(),
        kthread_preempt_tick_count(),
        as.switches,
        as.selftest_passed ? 1ULL : 0ULL,
        (uint64_t)as.user_leaf_slots,
        as.user_leaf_isolation_passed ? 1ULL : 0ULL,
        process.ownership_selftest_passed ? 1ULL : 0ULL,
        (uint64_t)process.owned_processes,
        process.completed_runs,
        (uint64_t)process.current_pid,
        (uint64_t)process.last_pid,
        process.rsp0_publishes,
        process.rsp0_restores,
        process.tss_rsp0_baseline ? 1ULL : 0ULL,
        (uint64_t)s.total_tasks,
        (uint64_t)s.active_tasks,
        (uint64_t)s.context_switches,
        (uint64_t)s.preemptions);
}

static void state_user(void) {
    user_exec_info_t u;
    user_exec_pair_info_t pair;
    user_exec_get_info(&u);
    user_exec_get_pair_info(&pair);
    STATE_EMIT("[STATE] user attempted=%u elf_loaded=%u elf_entry=%x segments=%u entered=%u returned=%u syscall_ok=%u boundary_ok=%u syscalls=%u exit_code=%u pipe_max=%u dur_ns=%u private_cr3=%u slot=%u cr3_restored=%u if_restored=%u leaf_sealed=%u nx_enforced=%u tensor_excluded=%u pid=%u process_bound=%u kstack_bytes=%u rsp0_changed=%u rsp0_published=%u int80_entries=%u all_int80_entries_in_stack=%u rsp0_restored=%u kstack_floor_canary=%u pair_attempted=%u pair_ready=%u pair_runs=%u second_pid=%u second_slot=%u second_exit_code=%u second_syscall_ok=%u second_boundary_ok=%u second_int80_entries=%u distinct_pid=%u distinct_slot=%u distinct_cr3=%u distinct_backing=%u distinct_stack=%u between_clean=%u pair_current_pid=%u pair_last_pid=%u pair_rsp0_publishes=%u pair_rsp0_restores=%u pair_tss_baseline=%u both_restored=%u trap_captured=%u trap_from_user=%u trap_rsp_user=%u trap_rip_user=%u trap_canary=%u trap_frame_in_kstack=%u trap_addr_exact=%u trap_contract=%u\n",
        (uint64_t)u.attempted,
        (uint64_t)u.elf_loaded,
        u.elf_entry,
        (uint64_t)u.elf_segments,
        (uint64_t)u.entered,
        (uint64_t)u.returned,
        (uint64_t)u.syscall_ok,
        (uint64_t)u.boundary_ok,
        (uint64_t)u.user_syscalls,
        u.exit_code,
        (uint64_t)u.observed_pipeline_max,
        u.duration_ns,
        (uint64_t)u.private_cr3,
        (uint64_t)u.address_space_slot,
        (uint64_t)u.cr3_restored,
        (uint64_t)u.if_restored,
        (uint64_t)u.leaf_sealed,
        (uint64_t)u.nx_enforced,
        (uint64_t)u.tensor_range_excluded,
        (uint64_t)u.process_id,
        (uint64_t)u.process_bound,
        (uint64_t)u.kernel_stack_bytes,
        (uint64_t)u.rsp0_changed,
        (uint64_t)u.rsp0_published,
        (uint64_t)u.int80_entries,
        (uint64_t)u.all_int80_entries_in_stack,
        (uint64_t)u.rsp0_restored,
        (uint64_t)u.kernel_stack_floor_canary_ok,
        (uint64_t)pair.attempted,
        (uint64_t)pair.passed,
        pair.completed_runs,
        (uint64_t)pair.second_process_id,
        (uint64_t)pair.second_address_space_slot,
        pair.second_exit_code,
        (uint64_t)pair.second_syscall_ok,
        (uint64_t)pair.second_boundary_ok,
        (uint64_t)pair.second_int80_entries,
        (uint64_t)pair.distinct_pid,
        (uint64_t)pair.distinct_slot,
        (uint64_t)pair.distinct_cr3,
        (uint64_t)pair.distinct_backing,
        (uint64_t)pair.distinct_kernel_stack,
        (uint64_t)pair.between_runs_clean,
        (uint64_t)pair.current_pid,
        (uint64_t)pair.last_pid,
        pair.rsp0_publishes,
        pair.rsp0_restores,
        (uint64_t)pair.tss_rsp0_baseline,
        (uint64_t)pair.both_restored,
        (uint64_t)u.trap_captured,
        (uint64_t)u.trap_from_user,
        (uint64_t)u.trap_rsp_in_user,
        (uint64_t)u.trap_rip_in_user,
        (uint64_t)u.trap_canary_ok,
        (uint64_t)u.trap_frame_in_kstack,
        (uint64_t)u.trap_frame_addr_exact,
        (uint64_t)trapframe_contract_passed());
}

static void state_slm(void) {
    slm_plan_observation_t o;
    slm_plan_observation_read(&o);
    uint64_t avg_ns = o.apply_ok ? o.total_latency_ns / o.apply_ok : 0;
    STATE_EMIT("[STATE] slm apply_ok=%u apply_failed=%u apply_rejected=%u last_ns=%u min_ns=%u avg_ns=%u max_ns=%u tsc_khz=%u\n",
        (uint64_t)o.apply_ok,
        (uint64_t)o.apply_failed,
        (uint64_t)o.apply_rejected,
        o.last_latency_ns,
        o.min_latency_ns,
        avg_ns,
        o.max_latency_ns,
        o.tsc_khz);
}

static void state_autonomy(void) {
    autonomy_stats_t stats;
    autonomy_event_t last_event = {0};
    bool has_last = (autonomy_get_last_event(&last_event) == AIOS_OK);
    const char *last_target = has_last
        ? autonomy_target_name(last_event.target_subsys) : "none";
    const char *last_state = has_last
        ? autonomy_action_state_name(last_event.state) : "none";
    const char *last_reason = has_last
        ? autonomy_reason_name(last_event.reason) : "none";

    autonomy_stats(&stats);
    STATE_EMIT("[STATE] autonomy schema=%u observation_only=%u safe_mode=%u support_mem=%s support_sched=%s support_accel=%s support_infer=%s telemetry=%u proposed=%u approved=%u committed=%u rejected=%u rollbacks=%u queue_depth=%u event_depth=%u last_valid=%u last_action=%u last_target=%s last_state=%s last_reason=%s\n",
        (uint64_t)SHELL_AUTONOMY_STATE_SCHEMA,
        (uint64_t)stats.observation_only,
        (uint64_t)stats.safe_mode,
        autonomy_target_support_name(autonomy_target_support(AUTONOMY_TARGET_MEM)),
        autonomy_target_support_name(autonomy_target_support(AUTONOMY_TARGET_SCHED)),
        autonomy_target_support_name(autonomy_target_support(AUTONOMY_TARGET_ACCEL)),
        autonomy_target_support_name(autonomy_target_support(AUTONOMY_TARGET_INFER)),
        stats.telemetry_samples,
        stats.actions_proposed,
        stats.actions_approved,
        stats.actions_committed,
        stats.actions_rejected,
        stats.rollbacks,
        (uint64_t)stats.action_queue_depth,
        (uint64_t)stats.event_log_depth,
        has_last ? 1ULL : 0ULL,
        has_last ? (uint64_t)last_event.action_id : 0ULL,
        last_target,
        last_state,
        last_reason);
}

static void state_nodes(void) {
    nodebit_stats_summary_t sum;
    nodebit_node_stats_t ns;

    nodebit_stats_summary(&sum);
    STATE_EMIT("[STATE] nodes active=%u evals=%u permits=%u denies=%u health_blocks=%u eval_max_ns=%u work_ns=%u work_count=%u\n",
        (uint64_t)sum.active_nodes,
        (uint64_t)sum.evaluations,
        (uint64_t)sum.permits,
        (uint64_t)sum.denies,
        (uint64_t)sum.health_blocks,
        sum.eval_max_ns,
        sum.work_total_ns,
        (uint64_t)sum.work_count);

    for (uint32_t i = 0; i < NODEBIT_MAX_ENTRIES; i++) {
        if (nodebit_stats_at(i, &ns) != AIOS_OK) {
            continue;
        }
        uint64_t avg_ns = ns.evaluations ? ns.eval_total_ns / ns.evaluations
                                         : 0;
        STATE_EMIT("[STATE] node id=%u evals=%u permits=%u denies=%u eval_avg_ns=%u eval_max_ns=%u work_ns=%u work_count=%u last_ns=%u\n",
            (uint64_t)ns.node_id,
            (uint64_t)ns.evaluations,
            (uint64_t)ns.permits,
            (uint64_t)ns.denies,
            avg_ns,
            ns.eval_max_ns,
            ns.work_total_ns,
            (uint64_t)ns.work_count,
            ns.last_decision_ns);
    }
}

static void state_pipeline(void) {
    node_pipeline_snapshot_t s;
    node_pipeline_get_snapshot(&s);
    STATE_EMIT("[STATE] pipeline active=%u max=%u executions=%u stage_runs=%u denied=%u last_status=%d\n",
        (uint64_t)s.active_count,
        (uint64_t)s.max_pipelines,
        s.total_executions,
        s.total_stage_runs,
        (uint64_t)s.denied_count,
        (int64_t)s.last_status);
}

static void state_resource(void) {
    ai_resource_snapshot_t r;
    uint32_t owner_rows = 0;
    uint32_t unattributed_rows = 0;
    uint32_t high_water_kinds = 0;
    uint32_t denied_kinds = 0;
    aios_status_t status = ai_resource_read(&r);

    if (status != AIOS_OK || !ai_resource_snapshot_valid(&r)) {
        if (status == AIOS_OK) {
            status = AIOS_ERR_IO;
        }
        STATE_EMIT("[STATE] resource ready=0 status=%d\n", (int64_t)status);
        return;
    }

    for (uint32_t i = 0; i < r.entry_count; i++) {
        uint32_t flags = r.entries[i].valid_flags;
        if ((flags & AI_RESOURCE_ENTRY_OWNER_VALID_MASK) != 0U) {
            owner_rows++;
        }
        if ((flags & AI_RESOURCE_ENTRY_OWNER_UNATTRIBUTED) != 0U) {
            unattributed_rows++;
        }
        if ((flags & AI_RESOURCE_ENTRY_HIGH_WATER_VALID) != 0U) {
            high_water_kinds++;
        }
        if ((flags & AI_RESOURCE_ENTRY_DENIED_VALID) != 0U) {
            denied_kinds++;
        }
    }

    STATE_EMIT("[STATE] resource schema=%u observation_only=%u kinds=%u units=%u entries=%u capacity=%u source_flags=%x sample=%u sampled_ns=%u owner_rows=%u unattributed_rows=%u heap_used=%u heap_limit=%u tensor_used=%u tensor_limit=%u tensor_high_water=%u fabric_used=%u fabric_limit=%u rings_used=%u rings_limit=%u sched_used=%u sched_limit=%u high_water_kinds=%u denied_kinds=%u\n",
        (uint64_t)r.schema_version,
        (uint64_t)r.observation_only,
        (uint64_t)r.kind_count,
        (uint64_t)r.unit_count,
        (uint64_t)r.entry_count,
        (uint64_t)r.entry_capacity,
        (uint64_t)r.source_flags,
        r.sample_sequence,
        r.sampled_at_ns,
        (uint64_t)owner_rows,
        (uint64_t)unattributed_rows,
        r.entries[AI_RESOURCE_KIND_KERNEL_HEAP].used,
        r.entries[AI_RESOURCE_KIND_KERNEL_HEAP].limit,
        r.entries[AI_RESOURCE_KIND_TENSOR_POOL].used,
        r.entries[AI_RESOURCE_KIND_TENSOR_POOL].limit,
        r.entries[AI_RESOURCE_KIND_TENSOR_POOL].high_water,
        r.entries[AI_RESOURCE_KIND_MEMORY_FABRIC_WINDOWS].used,
        r.entries[AI_RESOURCE_KIND_MEMORY_FABRIC_WINDOWS].limit,
        r.entries[AI_RESOURCE_KIND_INFERENCE_RING_REGISTRATIONS].used,
        r.entries[AI_RESOURCE_KIND_INFERENCE_RING_REGISTRATIONS].limit,
        r.entries[AI_RESOURCE_KIND_SCHEDULER_RUNNABLE].used,
        r.entries[AI_RESOURCE_KIND_SCHEDULER_RUNNABLE].limit,
        (uint64_t)high_water_kinds,
        (uint64_t)denied_kinds);
}

static void state_pressure(void) {
    ai_pressure_snapshot_t p;
    aios_status_t status = ai_pressure_read(&p);

    if (status != AIOS_OK) {
        STATE_EMIT("[STATE] pressure ready=0 status=%d\n", (int64_t)status);
        return;
    }

    STATE_EMIT("[STATE] pressure schema=%u observation_only=%u gate_filter_separate=%u max_levels=%u active_levels=%u planes=%u source_flags=%x sample=%u sampled_ns=%u sched_q10=%u memory_q10=%u policy_q10=%u hotspot=%s hotspot_valid=%u hotspot_q10=%u concentration_q10=%u sched_queue_concentration_q10=%u queued=%u runnable=%u largest_queue=%u largest_policy=%u active_domains=%u active_windows=%u shared_windows=%u participant_links=%u writer_pairs=%u read_write_pairs=%u max_fanout=%u budget_bytes=%u shared_bytes=%u weighted_shared_bytes=%u active_nodes=%u gate_evals=%u gate_denies=%u health_blocks=%u\n",
        (uint64_t)p.schema_version,
        (uint64_t)p.observation_only,
        (uint64_t)p.gate_filter_separate,
        (uint64_t)p.max_levels,
        (uint64_t)p.active_levels,
        (uint64_t)p.plane_count,
        (uint64_t)p.source_flags,
        p.sample_sequence,
        p.sampled_at_ns,
        (uint64_t)p.plane_score_q10[AI_PRESSURE_PLANE_SCHED],
        (uint64_t)p.plane_score_q10[AI_PRESSURE_PLANE_MEMORY],
        (uint64_t)p.plane_score_q10[AI_PRESSURE_PLANE_POLICY],
        (uint64_t)(uintptr_t)ai_pressure_plane_name(p.hotspot_plane),
        (uint64_t)p.hotspot_valid,
        (uint64_t)p.hotspot_score_q10,
        (uint64_t)p.plane_concentration_q10,
        (uint64_t)p.scheduler_queue_concentration_q10,
        (uint64_t)p.queued_tasks,
        (uint64_t)p.runnable_tasks,
        (uint64_t)p.largest_queue,
        (uint64_t)p.largest_queue_policy,
        (uint64_t)p.active_domains,
        (uint64_t)p.active_windows,
        (uint64_t)p.shared_windows,
        (uint64_t)p.participant_links,
        (uint64_t)p.writer_pairs,
        (uint64_t)p.read_write_pairs,
        (uint64_t)p.max_fanout,
        p.total_budget_bytes,
        p.shared_bytes,
        p.weighted_shared_bytes,
        (uint64_t)p.active_nodes,
        (uint64_t)p.gate_evaluations,
        (uint64_t)p.gate_denies,
        (uint64_t)p.gate_health_blocks);
}

static void state_health(void) {
    kernel_health_summary_t s;
    kernel_health_get_summary(&s);
    STATE_EMIT("[STATE] health stability=%s ok=%u degraded=%u failed=%u unknown=%u io_degraded=%u autonomy=%u risky_io=%u\n",
        kernel_stability_name(s.level),
        (uint64_t)s.ok_count,
        (uint64_t)s.degraded_count,
        (uint64_t)s.failed_count,
        (uint64_t)s.unknown_count,
        (uint64_t)s.io_degraded,
        (uint64_t)s.autonomy_allowed,
        (uint64_t)s.risky_io_allowed);
}

static void state_mem(void) {
    heap_stats_t s;
    heap_get_stats(&s);
    STATE_EMIT("[STATE] mem heap_total=%u heap_used=%u heap_free=%u blocks=%u allocs=%u frees=%u lock_acquires=%u\n",
        (uint64_t)s.total,
        (uint64_t)s.used,
        (uint64_t)s.free,
        (uint64_t)s.blocks,
        (uint64_t)s.allocs,
        (uint64_t)s.frees,
        heap_lock_acquire_count());
}

static void state_sec(void) {
    cpu_sec_info_t s;
    cpu_security_info(&s);
    STATE_EMIT("[STATE] sec nx=%u smep=%u umip=%u smap=%u canary=%u\n",
        (uint64_t)s.nx_enabled,
        (uint64_t)s.smep_enabled,
        (uint64_t)s.umip_enabled,
        (uint64_t)s.smap_enabled,
        (uint64_t)stack_guard_armed());
}

static void state_time(void) {
    STATE_EMIT("[STATE] time ticks=%u hz=%u tsc_khz=%u invariant=%u\n",
        kernel_timer_irq_ticks(),
        (uint64_t)kernel_timer_irq_hz(),
        kernel_time_tsc_khz(),
        (uint64_t)kernel_time_invariant_tsc());
}

static void state_version(void) {
    STATE_EMIT("[STATE] version release=%s arch=x86_64\n", SHELL_RELEASE);
}

static bool topic_is(const char *arg, uint32_t arg_len, const char *name) {
    size_t n = strlen(name);
    return (size_t)arg_len == n && strncmp(arg, name, n) == 0;
}

static void cmd_state(const char *arg, uint32_t arg_len) {
    if (arg_len == 0 || topic_is(arg, arg_len, "list")) { state_list();    return; }
    if (topic_is(arg, arg_len, "health"))               { state_health();  return; }
    if (topic_is(arg, arg_len, "mem"))                  { state_mem();     return; }
    if (topic_is(arg, arg_len, "sched"))                { state_sched();   return; }
    if (topic_is(arg, arg_len, "nodes"))                { state_nodes();   return; }
    if (topic_is(arg, arg_len, "pipeline"))             { state_pipeline(); return; }
    if (topic_is(arg, arg_len, "resource"))             { state_resource(); return; }
    if (topic_is(arg, arg_len, "pressure"))             { state_pressure(); return; }
    if (topic_is(arg, arg_len, "slm"))                  { state_slm();     return; }
    if (topic_is(arg, arg_len, "autonomy"))             { state_autonomy(); return; }
    if (topic_is(arg, arg_len, "user"))                 { state_user();    return; }
    if (topic_is(arg, arg_len, "sec"))                  { state_sec();     return; }
    if (topic_is(arg, arg_len, "time"))                 { state_time();    return; }
    if (topic_is(arg, arg_len, "version"))              { state_version(); return; }

    STATE_EMIT("[STATE] error reason=unknown-topic\n");
}

/* -------------------------------------------------------------------------
 * Built-in command handlers
 * ---------------------------------------------------------------------- */

static void cmd_help(void) {
    console_write_color(
        "Available commands:\n", VGA_LIGHT_CYAN, VGA_BLACK);
    kprintf("  help     - show this help\n");
    kprintf("  clear    - clear the screen\n");
    kprintf("  version  - show kernel version\n");
    kprintf("  info     - show CPU and timer info\n");
    kprintf("  mem      - show kernel heap statistics\n");
    kprintf("  uptime   - show uptime since boot\n");
    kprintf("  reboot   - reboot the system\n");
    kprintf("  ping     - machine-readable liveness probe\n");
    kprintf("  state    - machine-readable status (state list)\n");
}

static void cmd_version(void) {
    console_write_color(
        "AIOS - AI-Native Operating System\n", VGA_LIGHT_CYAN, VGA_BLACK);
    kprintf("Version      : %s\n", SHELL_VERSION);
    kprintf("Architecture : x86_64 (Long Mode)\n");
    kprintf("Build target : bare-metal, -O2, no-stdlib\n");
}

static void cmd_info(void) {
    kprintf("Architecture : x86_64 (Long Mode)\n");
    kprintf("Page size    : %u bytes\n",  (uint64_t)PAGE_SIZE);
    kprintf("Tensor align : %u bytes\n",  (uint64_t)TENSOR_ALIGN);
    kprintf("Timer freq   : %u Hz\n",     (uint64_t)kernel_timer_irq_hz());
    kprintf("TSC freq     : %u kHz\n",    kernel_time_tsc_khz());
    kprintf("Invariant TSC: %s\n",
            kernel_time_invariant_tsc() ? "yes" : "no");
}

static void cmd_mem(void) {
    heap_stats_t s;
    heap_get_stats(&s);
    kprintf("Heap total   : %u bytes  (%u KB)\n",
            (uint64_t)s.total,  (uint64_t)(s.total  / 1024));
    kprintf("Heap used    : %u bytes  (%u KB)\n",
            (uint64_t)s.used,   (uint64_t)(s.used   / 1024));
    kprintf("Heap free    : %u bytes  (%u KB)\n",
            (uint64_t)s.free,   (uint64_t)(s.free   / 1024));
    kprintf("Free blocks  : %u\n",     (uint64_t)s.blocks);
    kprintf("Total allocs : %u\n",     (uint64_t)s.allocs);
    kprintf("Total frees  : %u\n",     (uint64_t)s.frees);
}

static void cmd_uptime(void) {
    uint64_t ticks = kernel_timer_irq_ticks();
    uint32_t hz    = kernel_timer_irq_hz();
    if (hz == 0) {
        kprintf("Timer not initialised.\n");
        return;
    }
    uint64_t secs  = ticks / (uint64_t)hz;
    uint64_t mins  = secs  / 60ULL;
    uint64_t hours = mins  / 60ULL;
    secs %= 60ULL;
    mins %= 60ULL;
    kprintf("Uptime : %u h  %u m  %u s",   hours, mins, secs);
    kprintf("  (%u ticks @ %u Hz)\n", ticks, (uint64_t)hz);
}

static void cmd_reboot(void) {
    kprintf("Rebooting...\n");
    serial_write("[SHELL] reboot requested\n");
    __asm__ volatile ("cli");
    /* Pulse the keyboard controller reset line — works in QEMU and real HW */
    shell_outb(0x64, 0xFE);
    /* Fallback: spin — should never be reached */
    while (1) { __asm__ volatile ("hlt"); }
}

/* -------------------------------------------------------------------------
 * Command dispatcher
 * ---------------------------------------------------------------------- */

static void shell_dispatch(void) {
    /* Trim leading whitespace */
    uint32_t start = 0;
    while (start < cmd_len && cmd_buf[start] == ' ') start++;

    /* Trim trailing whitespace */
    uint32_t end = cmd_len;
    while (end > start && cmd_buf[end - 1] == ' ') end--;

    uint32_t len = end - start;
    if (len == 0) return; /* blank line */

    const char *tok = cmd_buf + start;

    /* Split "<cmd> <arg...>" so commands can take one argument */
    uint32_t cmd_end = 0;
    while (cmd_end < len && tok[cmd_end] != ' ') cmd_end++;
    uint32_t arg_start = cmd_end;
    while (arg_start < len && tok[arg_start] == ' ') arg_start++;
    const char *arg = tok + arg_start;
    uint32_t arg_len = len - arg_start;

#define MATCH(name) (len == sizeof(name)-1 && strncmp(tok, name, sizeof(name)-1) == 0)
#define MATCH_CMD(name) (cmd_end == sizeof(name)-1 && strncmp(tok, name, sizeof(name)-1) == 0)

    if (MATCH("help"))   { cmd_help();    return; }
    if (MATCH("clear"))  { console_clear(); return; }
    if (MATCH("version")){ cmd_version(); return; }
    if (MATCH("info"))   { cmd_info();    return; }
    if (MATCH("mem"))    { cmd_mem();     return; }
    if (MATCH("uptime")) { cmd_uptime();  return; }
    if (MATCH("reboot")) { cmd_reboot();  return; }
    if (MATCH("ping"))   { cmd_ping();    return; }
    if (MATCH_CMD("state")) { cmd_state(arg, arg_len); return; }

#undef MATCH
#undef MATCH_CMD

    /* Unknown command */
    console_write_color("Unknown command: ", VGA_LIGHT_RED, VGA_BLACK);
    for (uint32_t i = 0; i < len; i++) console_putchar(tok[i]);
    console_newline();
    kprintf("Type 'help' for a list of commands.\n");
}

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

aios_status_t shell_init(void) {
    cmd_len = 0;
    memset(cmd_buf, 0, sizeof(cmd_buf));
    return AIOS_OK;
}

void shell_run(void) {
    /* Switch to a clean black-background terminal */
    console_set_color(VGA_WHITE, VGA_BLACK);
    console_clear();

    /* Welcome banner */
    console_write_color(
        "+==============================================+\n",
        VGA_LIGHT_CYAN, VGA_BLACK);
    console_write_color(
        "|   AIOS Interactive Shell  -  v" SHELL_VERSION "  |\n",
        VGA_YELLOW, VGA_BLACK);
    console_write_color(
        "+==============================================+\n",
        VGA_LIGHT_CYAN, VGA_BLACK);
    kprintf("Type 'help' for available commands.\n\n");

    serial_write("[SHELL] Interactive shell started\n");

    /* Make sure interrupts are enabled so keyboard IRQs can fire */
    __asm__ volatile ("sti" ::: "memory");

    shell_prompt();

    while (1) {
        char c = 0;
        bool from_serial = false;

        /* Poll both input sources; sleep until the next IRQ when idle.
         * Serial RX has no IRQ enabled, so the 100 Hz timer bounds the
         * polling latency to ~10ms. */
        if (keyboard_haschar()) {
            c = keyboard_getchar();
        } else if (serial_data_ready()) {
            c = serial_getchar();
            from_serial = true;
        } else {
            __asm__ volatile ("hlt");
            continue;
        }

        /* Ctrl+C — abort current line */
        if (c == '\x03') {
            console_write_color("^C", VGA_LIGHT_RED, VGA_BLACK);
            console_newline();
            if (from_serial) serial_write("^C\n");
            cmd_len = 0;
            memset(cmd_buf, 0, sizeof(cmd_buf));
            shell_prompt();
            continue;
        }

        /* Enter — execute line */
        if (c == '\n' || c == '\r') {
            console_newline();
            if (from_serial) serial_write("\n");
            cmd_buf[cmd_len] = '\0';
            shell_dispatch();
            cmd_len = 0;
            memset(cmd_buf, 0, sizeof(cmd_buf));
            shell_prompt();
            continue;
        }

        /* Backspace (0x7F covers serial DEL) */
        if (c == '\b' || c == '\x7f') {
            if (cmd_len > 0) {
                cmd_len--;
                cmd_buf[cmd_len] = '\0';
                /* Erase the character on screen */
                console_putchar('\b');
                console_putchar(' ');
                console_putchar('\b');
                if (from_serial) serial_write("\b \b");
            }
            continue;
        }

        /* Printable character */
        if (cmd_len < CMD_MAX - 1) {
            cmd_buf[cmd_len++] = c;
            console_putchar(c);
            if (from_serial) serial_putchar(c);
        }
        /* else: line too long — silently drop */
    }
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
