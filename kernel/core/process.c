/*
 * AIOS Kernel - Static Bootstrap Process Ownership
 */

#include <kernel/process.h>
#include <kernel/user_layout.h>
#include <drivers/serial.h>
#include <lib/string.h>

#define PROCESS_STACK_FLOOR_CANARY 0x50524F434B535447ULL

AIOS_STATIC_ASSERT(BOOTSTRAP_PROCESS_COUNT == 2U,
    "bootstrap ownership proof currently expects exactly two slots");

static bootstrap_process_t g_processes[BOOTSTRAP_PROCESS_COUNT];
static uint8_t g_process_kernel_stacks[BOOTSTRAP_PROCESS_COUNT]
                                      [AIOS_USER_KERNEL_STACK_SIZE]
    ALIGNED(PAGE_SIZE);
static bootstrap_process_t *g_current_process;
static bootstrap_process_stats_t g_stats;
static bootstrap_process_event_t
    g_process_events[BOOTSTRAP_PROCESS_EVENT_CAPACITY];
static uint32_t g_process_event_count;
static uint64_t g_process_event_sequence;
static uint64_t g_process_event_dropped;
static bool g_process_event_overflow;
static uint64_t g_baseline_cr3;
static uint64_t g_baseline_rsp0;
static uint64_t g_trap_capture_sequence;
static bool g_initialized;

static inline uint64_t process_read_cr3(void) {
    uint64_t value;
    __asm__ volatile ("mov %%cr3, %0" : "=r"(value) : : "memory");
    return value;
}

static inline uint64_t process_read_rflags(void) {
    uint64_t flags;
    __asm__ volatile ("pushfq; popq %0" : "=r"(flags) : : "memory");
    return flags;
}

static void run_state_reset(bootstrap_user_run_state_t *state) {
    memset(state, 0, sizeof(*state));
    state->entry_rsp_min = ~0ULL;
}

static void trap_snapshot_reset(bootstrap_process_t *process) {
    memset(&process->trap_snapshot, 0, sizeof(process->trap_snapshot));
}

static void stack_reset(bootstrap_process_t *process) {
    memset((void *)(uintptr_t)process->kernel_stack_base, 0xA5,
           process->kernel_stack_size);
    *(uint64_t *)(uintptr_t)process->kernel_stack_base =
        PROCESS_STACK_FLOOR_CANARY;
}

static bool stack_floor_canary_ok(const bootstrap_process_t *process) {
    return *(const uint64_t *)(uintptr_t)process->kernel_stack_base ==
           PROCESS_STACK_FLOOR_CANARY;
}

static bool process_descriptor_valid(const bootstrap_process_t *process) {
    uint64_t expected_base;
    uint64_t expected_top;

    if (!process || process->slot >= BOOTSTRAP_PROCESS_COUNT ||
        process != &g_processes[process->slot] ||
        process->pid != BOOTSTRAP_PROCESS_PID_BASE + process->slot ||
        process->kernel_stack_size != AIOS_USER_KERNEL_STACK_SIZE) {
        return false;
    }
    expected_base = (uint64_t)(uintptr_t)
        g_process_kernel_stacks[process->slot];
    expected_top = expected_base + AIOS_USER_KERNEL_STACK_SIZE;
    return process->kernel_stack_base == expected_base &&
           process->kernel_stack_top == expected_top &&
           (expected_top & 0xFULL) == 0 &&
           process->address_space.slot == process->slot &&
           process->address_space.ready;
}

static bool process_event_frame_empty(
    const bootstrap_process_event_t *event) {
    return event && event->capture_sequence == 0 &&
        event->frame_addr == 0 && event->frame_rip == 0 &&
        event->frame_rsp == 0 && event->frame_rflags == 0 &&
        event->frame_cs == 0 && event->frame_ss == 0 &&
        event->frame_int_no == 0 && event->frame_err_code == 0 &&
        !event->snapshot_ref && !event->frame_valid &&
        !event->frame_from_user && !event->frame_addr_exact;
}

static bool process_event_frame_valid(
    const bootstrap_process_event_t *event,
    const bootstrap_process_t *process) {
    return event && process && event->capture_sequence != 0 &&
        event->snapshot_ref && event->frame_valid &&
        event->frame_from_user && event->frame_addr_exact &&
        event->frame_int_no == 3ULL && event->frame_err_code == 0 &&
        event->frame_cs == (uint64_t)AIOS_USER_CS_RPL3 &&
        event->frame_ss == (uint64_t)AIOS_USER_DS_RPL3 &&
        event->frame_rip >= AIOS_BOOTSTRAP_USER_BASE &&
        event->frame_rip < AIOS_BOOTSTRAP_USER_END &&
        event->frame_rsp >= AIOS_BOOTSTRAP_USER_BASE &&
        event->frame_rsp < AIOS_BOOTSTRAP_USER_END &&
        event->frame_addr == process->kernel_stack_top -
            (uint64_t)TRAPFRAME_SIZE &&
        (event->frame_rflags & 0x2ULL) != 0 &&
        (event->frame_rflags & BIT(10)) == 0 &&
        (event->frame_rflags & BIT(18)) != 0;
}

static bool process_event_fields_valid(
    const bootstrap_process_event_t *event,
    const bootstrap_process_t *process) {
    bool frame_bound;

    if (!event || !process_descriptor_valid(process) ||
        event->schema != BOOTSTRAP_PROCESS_EVENT_SCHEMA ||
        event->slot != process->slot || event->run_generation == 0 ||
        event->run_generation > process->run_generation ||
        event->outcome != BOOTSTRAP_PROCESS_EVENT_OUTCOME_COMMITTED ||
        !event->owner_ok || !event->cr3_ok || !event->rsp0_ok ||
        !event->if_disabled || event->direct_switch || event->resume_ready) {
        return false;
    }

    frame_bound = process_event_frame_valid(event, process);
    switch (event->kind) {
        case BOOTSTRAP_PROCESS_EVENT_KIND_ACQUIRE:
            return event->reason ==
                    BOOTSTRAP_PROCESS_EVENT_REASON_ACTIVATE_PUBLISH &&
                event->from_pid == 0 && event->to_pid == process->pid &&
                event->current_pid == process->pid &&
                event->from_cr3 == g_baseline_cr3 &&
                event->to_cr3 == process->address_space.cr3 &&
                event->from_rsp0 == g_baseline_rsp0 &&
                event->to_rsp0 == process->kernel_stack_top &&
                process_event_frame_empty(event);
        case BOOTSTRAP_PROCESS_EVENT_KIND_TRAP_CAPTURE:
            return event->reason ==
                    BOOTSTRAP_PROCESS_EVENT_REASON_BREAKPOINT_CAPTURE &&
                event->from_pid == process->pid &&
                event->to_pid == process->pid &&
                event->current_pid == process->pid &&
                event->from_cr3 == process->address_space.cr3 &&
                event->to_cr3 == process->address_space.cr3 &&
                event->from_rsp0 == process->kernel_stack_top &&
                event->to_rsp0 == process->kernel_stack_top &&
                frame_bound;
        case BOOTSTRAP_PROCESS_EVENT_KIND_RELEASE:
            if (event->reason !=
                    BOOTSTRAP_PROCESS_EVENT_REASON_RESTORE_PUBLISH ||
                event->from_pid != process->pid || event->to_pid != 0 ||
                event->current_pid != 0 ||
                event->from_cr3 != process->address_space.cr3 ||
                event->to_cr3 != g_baseline_cr3 ||
                event->from_rsp0 != process->kernel_stack_top ||
                event->to_rsp0 != g_baseline_rsp0) {
                return false;
            }
            /* Cleanup must remain possible when execution failed before the
             * armed #BP. A successful boot proof separately requires the
             * release record to reference that run's capture. */
            return frame_bound || process_event_frame_empty(event);
        case BOOTSTRAP_PROCESS_EVENT_KIND_INVALID:
        case BOOTSTRAP_PROCESS_EVENT_KIND_COUNT:
        default:
            return false;
    }
}

static aios_status_t process_event_preflight(uint32_t needed) {
    if (needed == 0 || needed > BOOTSTRAP_PROCESS_EVENT_CAPACITY) {
        return AIOS_ERR_INVAL;
    }
    if (g_process_event_count >
            BOOTSTRAP_PROCESS_EVENT_CAPACITY - needed ||
        g_process_event_sequence > ~0ULL - (uint64_t)needed) {
        g_process_event_dropped++;
        g_process_event_overflow = true;
        return AIOS_ERR_BUSY;
    }
    return AIOS_OK;
}

static aios_status_t process_event_append(
    const bootstrap_process_event_t *event,
    const bootstrap_process_t *process) {
    bootstrap_process_event_t committed;
    uint64_t next_sequence;

    if (!event || event->valid ||
        !process_event_fields_valid(event, process)) {
        g_process_event_dropped++;
        return AIOS_ERR_IO;
    }
    if (g_process_event_count >= BOOTSTRAP_PROCESS_EVENT_CAPACITY ||
        g_process_event_sequence == ~0ULL) {
        g_process_event_dropped++;
        g_process_event_overflow = true;
        return AIOS_ERR_BUSY;
    }
    if (g_process_event_count > 0) {
        const bootstrap_process_event_t *previous =
            &g_process_events[g_process_event_count - 1U];

        if (!previous->valid ||
            previous->event_sequence != g_process_event_sequence) {
            g_process_event_dropped++;
            return AIOS_ERR_IO;
        }
    }

    next_sequence = g_process_event_sequence + 1ULL;
    committed = *event;
    committed.event_sequence = next_sequence;
    committed.valid = false;
    g_process_events[g_process_event_count] = committed;
    __asm__ volatile ("" : : : "memory");
    g_process_events[g_process_event_count].valid = true;
    g_process_event_sequence = next_sequence;
    g_process_event_count++;
    return AIOS_OK;
}

static void process_event_bind_frame(
    bootstrap_process_event_t *event, uint64_t capture_sequence,
    uint64_t frame_addr, const interrupt_frame_t *frame,
    const bootstrap_process_t *process) {
    if (!event || !frame || !process || capture_sequence == 0) {
        return;
    }

    event->capture_sequence = capture_sequence;
    event->frame_addr = frame_addr;
    event->frame_rip = frame->rip;
    event->frame_rsp = frame->rsp;
    event->frame_rflags = frame->rflags;
    event->frame_cs = frame->cs;
    event->frame_ss = frame->ss;
    event->frame_int_no = frame->int_no;
    event->frame_err_code = frame->err_code;
    event->snapshot_ref = true;
    event->frame_from_user = interrupt_frame_from_user(frame);
    event->frame_addr_exact = frame_addr == process->kernel_stack_top -
        (uint64_t)TRAPFRAME_SIZE;
    event->frame_valid = event->frame_from_user &&
        event->frame_addr_exact && frame->int_no == 3ULL &&
        frame->err_code == 0 &&
        frame->cs == (uint64_t)AIOS_USER_CS_RPL3 &&
        frame->ss == (uint64_t)AIOS_USER_DS_RPL3 &&
        frame->rip >= AIOS_BOOTSTRAP_USER_BASE &&
        frame->rip < AIOS_BOOTSTRAP_USER_END &&
        frame->rsp >= AIOS_BOOTSTRAP_USER_BASE &&
        frame->rsp < AIOS_BOOTSTRAP_USER_END &&
        (frame->rflags & 0x2ULL) != 0 &&
        (frame->rflags & BIT(10)) == 0 &&
        (frame->rflags & BIT(18)) != 0;
}

aios_status_t bootstrap_process_init(void) {
    bool unique_cr3;
    bool unique_backing;
    bool unique_stack;

    /* Initialization is idempotent once ownership is established, and must
     * never scrub a live process descriptor or its active ring0 stack. */
    if (g_current_process) {
        return AIOS_ERR_BUSY;
    }
    for (uint32_t slot = 0; slot < BOOTSTRAP_PROCESS_COUNT; slot++) {
        if (g_processes[slot].active) {
            return AIOS_ERR_BUSY;
        }
    }
    if (g_initialized) {
        return AIOS_OK;
    }

    memset(g_processes, 0, sizeof(g_processes));
    memset(&g_stats, 0, sizeof(g_stats));
    memset(g_process_events, 0, sizeof(g_process_events));
    g_current_process = NULL;
    g_process_event_count = 0;
    g_process_event_sequence = 0;
    g_process_event_dropped = 0;
    g_process_event_overflow = false;
    g_baseline_cr3 = process_read_cr3();
    g_baseline_rsp0 = user_mode_rsp0_read();
    g_trap_capture_sequence = 0;
    g_initialized = false;

    if (!user_mode_scaffold_ready() || g_baseline_cr3 == 0 ||
        g_baseline_rsp0 == 0) {
        return AIOS_ERR_IO;
    }

    for (uint32_t slot = 0; slot < BOOTSTRAP_PROCESS_COUNT; slot++) {
        bootstrap_process_t *process = &g_processes[slot];
        aios_status_t status;

        process->pid = BOOTSTRAP_PROCESS_PID_BASE + slot;
        process->slot = slot;
        process->kernel_stack_base = (uint64_t)(uintptr_t)
            g_process_kernel_stacks[slot];
        process->kernel_stack_size = AIOS_USER_KERNEL_STACK_SIZE;
        process->kernel_stack_top = process->kernel_stack_base +
                                    process->kernel_stack_size;
        run_state_reset(&process->run_state);
        trap_snapshot_reset(process);
        stack_reset(process);

        status = address_space_bootstrap_slot_prepare(
            slot, false, &process->address_space);
        if (status != AIOS_OK) {
            return status;
        }
        process->prepared = true;
        g_stats.owned_processes++;
    }

    unique_cr3 = g_processes[0].address_space.cr3 !=
                  g_processes[1].address_space.cr3;
    unique_backing = g_processes[0].address_space.backing_phys !=
                     g_processes[1].address_space.backing_phys;
    unique_stack = g_processes[0].kernel_stack_base !=
                   g_processes[1].kernel_stack_base &&
                   g_processes[0].kernel_stack_top <=
                       g_processes[1].kernel_stack_base;

    g_stats.slots = BOOTSTRAP_PROCESS_COUNT;
    g_stats.unique_cr3 = unique_cr3;
    g_stats.unique_backing = unique_backing;
    g_stats.unique_kernel_stack = unique_stack;
    g_stats.tss_rsp0_baseline = user_mode_rsp0_read() == g_baseline_rsp0;
    g_stats.ownership_selftest_passed =
        g_stats.owned_processes == BOOTSTRAP_PROCESS_COUNT &&
        process_descriptor_valid(&g_processes[0]) &&
        process_descriptor_valid(&g_processes[1]) &&
        stack_floor_canary_ok(&g_processes[0]) &&
        stack_floor_canary_ok(&g_processes[1]) &&
        unique_cr3 && unique_backing && unique_stack &&
        g_stats.tss_rsp0_baseline;
    g_initialized = g_stats.ownership_selftest_passed;

    serial_printf("[PROC] bootstrap ownership selftest %s slots=%u owned=%u stack_bytes=%u unique_cr3=%u unique_backing=%u unique_stack=%u\n",
        g_initialized ? "PASS" : "FAIL",
        (uint64_t)g_stats.slots,
        (uint64_t)g_stats.owned_processes,
        (uint64_t)AIOS_USER_KERNEL_STACK_SIZE,
        unique_cr3 ? 1ULL : 0ULL,
        unique_backing ? 1ULL : 0ULL,
        unique_stack ? 1ULL : 0ULL);
    return g_initialized ? AIOS_OK : AIOS_ERR_IO;
}

bool bootstrap_process_ready(void) {
    return g_initialized && g_stats.ownership_selftest_passed;
}

aios_status_t bootstrap_process_prepare(
    uint32_t slot, bool executable, bootstrap_process_t **out) {
    bootstrap_process_t *process;
    aios_status_t status;

    if (!out || !g_initialized || slot >= BOOTSTRAP_PROCESS_COUNT) {
        return AIOS_ERR_INVAL;
    }
    if (g_current_process) {
        return AIOS_ERR_BUSY;
    }

    process = &g_processes[slot];
    if (process->active) {
        return AIOS_ERR_BUSY;
    }
    if (process->run_generation == ~0ULL) {
        return AIOS_ERR_IO;
    }
    status = address_space_bootstrap_slot_prepare(
        slot, executable, &process->address_space);
    if (status != AIOS_OK) {
        return status;
    }

    run_state_reset(&process->run_state);
    trap_snapshot_reset(process);
    process->run_generation++;
    stack_reset(process);
    process->prepared = true;
    *out = process;
    return AIOS_OK;
}

aios_status_t bootstrap_process_cancel(bootstrap_process_t *process) {
    bool nx_enforced = false;
    aios_status_t status;

    if (!g_initialized || !process_descriptor_valid(process) ||
        !process->prepared) {
        return AIOS_ERR_INVAL;
    }
    if (g_current_process || process->active) {
        return AIOS_ERR_BUSY;
    }

    status = address_space_bootstrap_slot_seal(
        process->slot, &nx_enforced);
    process->prepared = false;
    (void)nx_enforced;
    return status;
}

aios_status_t bootstrap_process_activate(
    bootstrap_process_t *process, bootstrap_process_guard_t *guard) {
    bootstrap_process_event_t event = {0};
    aios_status_t status;

    if (!guard || !g_initialized || !process_descriptor_valid(process) ||
        !process->prepared) {
        return AIOS_ERR_INVAL;
    }
    if (guard->active || g_current_process || process->active) {
        return AIOS_ERR_BUSY;
    }
    status = process_event_preflight(BOOTSTRAP_PROCESS_EVENTS_PER_RUN);
    if (status != AIOS_OK) {
        return status;
    }

    memset(guard, 0, sizeof(*guard));
    run_state_reset(&process->run_state);
    status = address_space_activate(&process->address_space,
                                    &guard->address_space);
    if (status != AIOS_OK) {
        guard->active = guard->address_space.active ||
                        guard->address_space.irq_restore_pending;
        return status;
    }

    status = user_mode_rsp0_publish(process->kernel_stack_base,
                                    process->kernel_stack_size,
                                    &guard->rsp0);
    if (status != AIOS_OK) {
        if (guard->rsp0.active) {
            guard->active = true;
            return status;
        }
        (void)address_space_restore(&guard->address_space);
        guard->active = guard->address_space.active ||
                        guard->address_space.irq_restore_pending;
        return status;
    }
    if (guard->rsp0.previous_rsp0 == guard->rsp0.published_rsp0) {
        if (user_mode_rsp0_restore(&guard->rsp0) != AIOS_OK ||
            address_space_restore(&guard->address_space) != AIOS_OK) {
            guard->active = true;
            return AIOS_ERR_IO;
        }
        return AIOS_ERR_IO;
    }

    guard->rsp0_changed = true;
    guard->active = true;
    process->active = true;
    g_current_process = process;
    g_stats.current_pid = process->pid;
    g_stats.rsp0_publishes++;
    g_stats.tss_rsp0_baseline = false;

    event.schema = BOOTSTRAP_PROCESS_EVENT_SCHEMA;
    event.kind = BOOTSTRAP_PROCESS_EVENT_KIND_ACQUIRE;
    event.reason = BOOTSTRAP_PROCESS_EVENT_REASON_ACTIVATE_PUBLISH;
    event.outcome = BOOTSTRAP_PROCESS_EVENT_OUTCOME_COMMITTED;
    event.run_generation = process->run_generation;
    event.from_pid = 0;
    event.to_pid = process->pid;
    event.current_pid = process->pid;
    event.slot = process->slot;
    event.from_cr3 = guard->address_space.previous_cr3;
    event.to_cr3 = process->address_space.cr3;
    event.from_rsp0 = guard->rsp0.previous_rsp0;
    event.to_rsp0 = guard->rsp0.published_rsp0;
    event.owner_ok = g_current_process == process && process->active &&
        g_stats.current_pid == process->pid;
    event.cr3_ok = process_read_cr3() == event.to_cr3;
    event.rsp0_ok = user_mode_rsp0_read() == event.to_rsp0;
    event.if_disabled = (process_read_rflags() & BIT(9)) == 0;

    status = process_event_append(&event, process);
    if (status != AIOS_OK) {
        /* The live guard remains owned and IF remains disabled. The caller's
         * existing activation-failure path will fail-stop rather than run
         * without an auditable owner publication. */
        return status;
    }
    return AIOS_OK;
}

aios_status_t bootstrap_process_finish(
    bootstrap_process_t *process, bootstrap_process_guard_t *guard) {
    bootstrap_user_run_state_t *state;
    bootstrap_process_trap_snapshot_t snapshot = {0};
    bootstrap_process_event_t event = {0};
    uint64_t expected_entry_rsp;
    bool int80_entries_within_stack;
    aios_status_t restore_status;
    aios_status_t seal_status;
    aios_status_t irq_status;
    aios_status_t event_status;

    if (!process_descriptor_valid(process) || !guard || !guard->active ||
        g_current_process != process || !process->active) {
        return AIOS_ERR_INVAL;
    }

    state = &process->run_state;
    expected_entry_rsp = process->kernel_stack_top -
                         AIOS_RING3_ENTRY_FRAME_SIZE;
    guard->int80_entries = state->int80_entries;
    int80_entries_within_stack = state->int80_entries == 0 ||
        (state->entry_rsp_min >= process->kernel_stack_base &&
         state->entry_rsp_max < process->kernel_stack_top);
    guard->all_int80_entries_in_stack = state->int80_entries > 0 &&
        int80_entries_within_stack &&
        state->entry_rsp_min == expected_entry_rsp &&
        state->entry_rsp_max == expected_entry_rsp;
    guard->kernel_stack_floor_canary_ok =
        stack_floor_canary_ok(process);

    restore_status = user_mode_rsp0_restore(&guard->rsp0);
    if (restore_status != AIOS_OK) {
        return restore_status;
    }
    g_stats.rsp0_restores++;

    restore_status = address_space_restore_deferred_irq(
        &guard->address_space);
    if (restore_status != AIOS_OK) {
        return restore_status;
    }

    seal_status = address_space_bootstrap_slot_seal(
        process->slot, &guard->nx_enforced);
    guard->leaf_sealed = seal_status == AIOS_OK;

    /* Publish the boot ownership state while IF is still forced low. An IRQ
     * must never observe boot CR3/rsp0 with a stale current process. */
    process->active = false;
    process->prepared = false;
    g_current_process = NULL;
    g_stats.current_pid = 0;
    if (state->exited != 0 && state->int80_entries > 0) {
        g_stats.last_pid = process->pid;
        g_stats.completed_runs++;
    }
    g_stats.tss_rsp0_baseline = user_mode_rsp0_read() == g_baseline_rsp0;

    /* A damaged entry stack, unsealed user leaf, or wrong baseline is not a
     * recoverable demo failure. Keep IF=0 and the guard live so the caller
     * must fail-stop instead of continuing on an unsafe bootstrap state. */
    if (!int80_entries_within_stack ||
        !guard->kernel_stack_floor_canary_ok || !guard->rsp0.restored ||
        !guard->leaf_sealed || seal_status != AIOS_OK ||
        !g_stats.tss_rsp0_baseline) {
        guard->active = true;
        return AIOS_ERR_IO;
    }

    event.schema = BOOTSTRAP_PROCESS_EVENT_SCHEMA;
    event.kind = BOOTSTRAP_PROCESS_EVENT_KIND_RELEASE;
    event.reason = BOOTSTRAP_PROCESS_EVENT_REASON_RESTORE_PUBLISH;
    event.outcome = BOOTSTRAP_PROCESS_EVENT_OUTCOME_COMMITTED;
    event.run_generation = process->run_generation;
    event.from_pid = process->pid;
    event.to_pid = 0;
    event.current_pid = 0;
    event.slot = process->slot;
    event.from_cr3 = process->address_space.cr3;
    event.to_cr3 = guard->address_space.previous_cr3;
    event.from_rsp0 = process->kernel_stack_top;
    event.to_rsp0 = guard->rsp0.previous_rsp0;
    event.owner_ok = !g_current_process && !process->active &&
        g_stats.current_pid == 0;
    event.cr3_ok = guard->address_space.cr3_restored &&
        process_read_cr3() == event.to_cr3;
    event.rsp0_ok = guard->rsp0.restored &&
        user_mode_rsp0_read() == event.to_rsp0;
    event.if_disabled = (process_read_rflags() & BIT(9)) == 0;

    if (bootstrap_process_get_trap_snapshot(process->slot, &snapshot) ==
            AIOS_OK) {
        process_event_bind_frame(&event, snapshot.capture_sequence,
            snapshot.frame_addr, &snapshot.frame, process);
    }

    event_status = process_event_append(&event, process);
    if (event_status != AIOS_OK) {
        /* CR3/rsp0/current are already in their safe baseline state and IF
         * remains disabled. Preserve the live outer guard so the caller
         * fail-stops on an unauditable publication. */
        return event_status;
    }

    irq_status = address_space_restore_interrupts(&guard->address_space);
    guard->active = irq_status != AIOS_OK ||
                    guard->address_space.active ||
                    guard->address_space.irq_restore_pending;

    if (irq_status != AIOS_OK) {
        return AIOS_ERR_IO;
    }
    return AIOS_OK;
}

aios_status_t bootstrap_process_capture_current_trap(
    const interrupt_frame_t *frame) {
    bootstrap_process_t *process = g_current_process;
    bootstrap_process_trap_snapshot_t *snapshot;
    bootstrap_process_event_t event = {0};
    uint64_t live_cr3;
    uint64_t live_rsp0;
    uint64_t live_rflags;
    uint64_t frame_addr;
    uint64_t next_sequence;
    aios_status_t event_status;

    if (!g_initialized || !frame || !process ||
        !process_descriptor_valid(process) || !process->prepared ||
        !process->active || process->run_generation == 0) {
        return AIOS_ERR_INVAL;
    }

    snapshot = &process->trap_snapshot;
    if (snapshot->evidence_valid) {
        return AIOS_ERR_BUSY;
    }
    if (g_trap_capture_sequence == ~0ULL) {
        return AIOS_ERR_IO;
    }

    live_cr3 = process_read_cr3();
    live_rsp0 = user_mode_rsp0_read();
    live_rflags = process_read_rflags();
    frame_addr = (uint64_t)(uintptr_t)frame;
    /* The entry stub sanitizes only live kernel AC; a saved user AC value is
     * valid process evidence and must remain untouched. The dedicated entry
     * counters prove that the bounded demo actually supplied AC=1. */
    if ((live_rflags & (BIT(9) | BIT(18))) != 0 ||
        live_cr3 != process->address_space.cr3 ||
        live_rsp0 != process->kernel_stack_top ||
        frame->int_no != 3ULL || frame->err_code != 0ULL ||
        !interrupt_frame_from_user(frame) ||
        frame->cs != (uint64_t)AIOS_USER_CS_RPL3 ||
        frame->ss != (uint64_t)AIOS_USER_DS_RPL3 ||
        frame->rip < AIOS_BOOTSTRAP_USER_BASE ||
        frame->rip >= AIOS_BOOTSTRAP_USER_END ||
        frame->rsp < AIOS_BOOTSTRAP_USER_BASE ||
        frame->rsp >= AIOS_BOOTSTRAP_USER_END ||
        frame_addr != process->kernel_stack_top -
            (uint64_t)TRAPFRAME_SIZE ||
        (frame->rflags & 0x2ULL) == 0 ||
        (frame->rflags & BIT(10)) != 0 ||
        (frame->rflags & BIT(18)) == 0) {
        return AIOS_ERR_IO;
    }

    next_sequence = g_trap_capture_sequence + 1ULL;
    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->frame = *frame;
    snapshot->frame_addr = frame_addr;
    snapshot->capture_sequence = next_sequence;
    snapshot->run_generation = process->run_generation;
    snapshot->owner_cr3 = live_cr3;
    snapshot->owner_rsp0 = live_rsp0;
    snapshot->captures = 1ULL;
    snapshot->owner_pid = process->pid;
    snapshot->owner_slot = process->slot;
    snapshot->owner_bound = g_current_process == process;
    snapshot->frame_copied =
        memcmp(&snapshot->frame, frame, sizeof(snapshot->frame)) == 0;
    snapshot->from_user = interrupt_frame_from_user(&snapshot->frame);
    snapshot->frame_addr_exact =
        snapshot->frame_addr == process->kernel_stack_top -
            (uint64_t)TRAPFRAME_SIZE;
    snapshot->cr3_matched = snapshot->owner_cr3 ==
        process->address_space.cr3;
    snapshot->rsp0_matched = snapshot->owner_rsp0 ==
        process->kernel_stack_top;
    snapshot->resume_ready = false;

    if (!snapshot->owner_bound || !snapshot->frame_copied ||
        !snapshot->from_user || !snapshot->frame_addr_exact ||
        !snapshot->cr3_matched || !snapshot->rsp0_matched) {
        trap_snapshot_reset(process);
        return AIOS_ERR_IO;
    }

    event.schema = BOOTSTRAP_PROCESS_EVENT_SCHEMA;
    event.kind = BOOTSTRAP_PROCESS_EVENT_KIND_TRAP_CAPTURE;
    event.reason = BOOTSTRAP_PROCESS_EVENT_REASON_BREAKPOINT_CAPTURE;
    event.outcome = BOOTSTRAP_PROCESS_EVENT_OUTCOME_COMMITTED;
    event.run_generation = process->run_generation;
    event.from_pid = process->pid;
    event.to_pid = process->pid;
    event.current_pid = process->pid;
    event.slot = process->slot;
    event.from_cr3 = live_cr3;
    event.to_cr3 = live_cr3;
    event.from_rsp0 = live_rsp0;
    event.to_rsp0 = live_rsp0;
    event.owner_ok = g_current_process == process && process->active &&
        g_stats.current_pid == process->pid;
    event.cr3_ok = live_cr3 == process->address_space.cr3;
    event.rsp0_ok = live_rsp0 == process->kernel_stack_top;
    event.if_disabled = (live_rflags & BIT(9)) == 0;
    process_event_bind_frame(&event, next_sequence, frame_addr, frame,
        process);

    event_status = process_event_append(&event, process);
    if (event_status != AIOS_OK) {
        trap_snapshot_reset(process);
        return event_status;
    }

    g_trap_capture_sequence = next_sequence;
    __asm__ volatile ("" : : : "memory");
    snapshot->evidence_valid = true;
    return AIOS_OK;
}

aios_status_t bootstrap_process_get_trap_snapshot(
    uint32_t slot, bootstrap_process_trap_snapshot_t *out) {
    const bootstrap_process_t *process;
    const bootstrap_process_trap_snapshot_t *snapshot;

    if (!out || !g_initialized || slot >= BOOTSTRAP_PROCESS_COUNT) {
        return AIOS_ERR_INVAL;
    }

    process = &g_processes[slot];
    snapshot = &process->trap_snapshot;
    if (!process_descriptor_valid(process) || !snapshot->evidence_valid ||
        !snapshot->owner_bound || !snapshot->frame_copied ||
        !snapshot->from_user || !snapshot->frame_addr_exact ||
        !snapshot->cr3_matched || !snapshot->rsp0_matched ||
        snapshot->resume_ready || snapshot->captures != 1ULL ||
        snapshot->capture_sequence == 0 ||
        snapshot->run_generation != process->run_generation ||
        snapshot->owner_pid != process->pid ||
        snapshot->owner_slot != process->slot ||
        snapshot->owner_cr3 != process->address_space.cr3 ||
        snapshot->owner_rsp0 != process->kernel_stack_top) {
        return AIOS_ERR_IO;
    }

    *out = *snapshot;
    return AIOS_OK;
}

aios_status_t bootstrap_process_get_event(
    uint32_t index, bootstrap_process_event_t *out) {
    const bootstrap_process_event_t *event;
    const bootstrap_process_t *process;

    if (!out || !g_initialized || index >= g_process_event_count) {
        return AIOS_ERR_INVAL;
    }
    if (g_current_process) {
        return AIOS_ERR_BUSY;
    }

    event = &g_process_events[index];
    if (event->slot >= BOOTSTRAP_PROCESS_COUNT) {
        return AIOS_ERR_IO;
    }
    process = &g_processes[event->slot];
    if (!event->valid || event->event_sequence != (uint64_t)index + 1ULL ||
        !process_event_fields_valid(event, process)) {
        return AIOS_ERR_IO;
    }

    *out = *event;
    return AIOS_OK;
}

bootstrap_process_t *bootstrap_process_current(void) {
    return g_current_process;
}

void bootstrap_process_get_stats(bootstrap_process_stats_t *out) {
    if (!out) {
        return;
    }
    *out = g_stats;
    out->current_pid = g_current_process ? g_current_process->pid : 0;
    out->tss_rsp0_baseline = !g_current_process &&
        user_mode_rsp0_read() == g_baseline_rsp0;
    out->event_count = g_process_event_count;
    out->event_last_sequence = g_process_event_sequence;
    out->event_dropped = g_process_event_dropped;
    out->event_overflow = g_process_event_overflow;
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
