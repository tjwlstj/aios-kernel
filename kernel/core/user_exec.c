/*
 * AIOS Kernel - Static Bootstrap Ring3 Execution
 * AI-Native Operating System
 */

#include <kernel/user_exec.h>
#include <kernel/user_access.h>
#include <kernel/elf.h>
#include <kernel/time.h>
#include <kernel/user_layout.h>
#include <kernel/process.h>
#include <mm/address_space.h>
#include <mm/tensor_mm.h>
#include <runtime/node_pipeline.h>
#include <drivers/serial.h>
#include <interrupt/idt.h>
#include <interrupt/trapframe.h>
#include <lib/string.h>

/* Ring3 entry / syscall path (kernel/core/user_entry.asm). */
extern void user_mode_run(uint64_t entry_rip, uint64_t user_stack_top,
                          bootstrap_user_run_state_t *run_state);
/* Embedded static ELF64 demo image parsed by the ELF loader. */
extern uint8_t user_elf_image_start[];
extern uint8_t user_elf_image_end[];

#define USER_EXEC_PRIMARY_SLOT   0U
#define USER_EXEC_SECONDARY_SLOT 1U

AIOS_STATIC_ASSERT(BOOTSTRAP_PROCESS_COUNT == 2U,
    "bootstrap pair proof currently expects exactly two processes");

typedef struct {
    user_exec_info_t info;
    uint64_t address_space_cr3;
    uint64_t address_space_backing;
    uint64_t kernel_stack_base;
    uint64_t kernel_stack_top;
    uint64_t run_generation;
    uint64_t trap_snapshot_storage;
    bootstrap_process_trap_snapshot_t trap_snapshot;
    bool trap_snapshot_loaded;
    bool trap_snapshot_persisted;
} user_exec_run_result_t;

static user_exec_info_t g_user_exec;
static user_exec_pair_info_t g_user_exec_pair;

static bool run_restored(const user_exec_info_t *info) {
    return info && info->cr3_restored && info->if_restored &&
        info->leaf_sealed && info->rsp0_restored &&
        info->kernel_stack_floor_canary_ok;
}

static bool run_trap_evidence_ok(const user_exec_info_t *info) {
    return info && info->trap_captured && info->trap_from_user &&
        info->trap_rsp_in_user && info->trap_rip_in_user &&
        info->trap_canary_ok && info->trap_frame_in_kstack &&
        info->trap_frame_addr_exact;
}

static bool trap_snapshot_same(
    const bootstrap_process_trap_snapshot_t *a,
    const bootstrap_process_trap_snapshot_t *b) {
    return a && b &&
        memcmp(&a->frame, &b->frame, sizeof(a->frame)) == 0 &&
        a->frame_addr == b->frame_addr &&
        a->capture_sequence == b->capture_sequence &&
        a->run_generation == b->run_generation &&
        a->owner_cr3 == b->owner_cr3 &&
        a->owner_rsp0 == b->owner_rsp0 &&
        a->captures == b->captures &&
        a->owner_pid == b->owner_pid &&
        a->owner_slot == b->owner_slot &&
        a->evidence_valid == b->evidence_valid &&
        a->owner_bound == b->owner_bound &&
        a->frame_copied == b->frame_copied &&
        a->from_user == b->from_user &&
        a->frame_addr_exact == b->frame_addr_exact &&
        a->cr3_matched == b->cr3_matched &&
        a->rsp0_matched == b->rsp0_matched &&
        a->resume_ready == b->resume_ready;
}

static bool trap_snapshot_refresh(user_exec_run_result_t *result) {
    bootstrap_process_trap_snapshot_t persisted = {0};

    if (!result || !result->trap_snapshot_loaded) {
        return false;
    }
    result->trap_snapshot_persisted =
        bootstrap_process_get_trap_snapshot(
            result->info.address_space_slot, &persisted) == AIOS_OK &&
        trap_snapshot_same(&result->trap_snapshot, &persisted);
    if (result->trap_snapshot_persisted) {
        result->trap_snapshot = persisted;
    }
    return result->trap_snapshot_persisted;
}

static bool trap_snapshot_valid(const user_exec_run_result_t *result) {
    const bootstrap_process_trap_snapshot_t *s;

    if (!result || !result->trap_snapshot_loaded ||
        !result->trap_snapshot_persisted) {
        return false;
    }
    s = &result->trap_snapshot;
    return s->evidence_valid && s->captures == 1ULL &&
        s->capture_sequence != 0 &&
        s->run_generation == result->run_generation &&
        !s->resume_ready;
}

static bool trap_snapshot_owner_ok(const user_exec_run_result_t *result) {
    const bootstrap_process_trap_snapshot_t *s = &result->trap_snapshot;

    return trap_snapshot_valid(result) && s->owner_bound &&
        s->owner_pid == result->info.process_id &&
        s->owner_slot == result->info.address_space_slot;
}

static bool trap_snapshot_frame_ok(const user_exec_run_result_t *result) {
    const bootstrap_process_trap_snapshot_t *s = &result->trap_snapshot;
    const interrupt_frame_t *f = &s->frame;

    return trap_snapshot_valid(result) && s->frame_copied &&
        s->from_user && s->frame_addr_exact &&
        f->int_no == 3ULL && f->err_code == 0ULL &&
        f->cs == (uint64_t)AIOS_USER_CS_RPL3 &&
        f->ss == (uint64_t)AIOS_USER_DS_RPL3 &&
        f->rip >= AIOS_BOOTSTRAP_USER_BASE &&
        f->rip < AIOS_BOOTSTRAP_USER_END &&
        f->rsp >= AIOS_BOOTSTRAP_USER_BASE &&
        f->rsp < AIOS_BOOTSTRAP_USER_END &&
        f->rbx == TRAPFRAME_SELFTEST_CANARY(13) &&
        f->rcx == TRAPFRAME_SELFTEST_CANARY(12) &&
        f->r8  == TRAPFRAME_SELFTEST_CANARY(7) &&
        f->r12 == TRAPFRAME_SELFTEST_CANARY(3) &&
        s->frame_addr >= result->kernel_stack_base &&
        s->frame_addr + (uint64_t)TRAPFRAME_SIZE <=
            result->kernel_stack_top &&
        s->frame_addr == result->kernel_stack_top -
            (uint64_t)TRAPFRAME_SIZE;
}

static bool trap_snapshot_cr3_ok(const user_exec_run_result_t *result) {
    return trap_snapshot_valid(result) && result->trap_snapshot.cr3_matched &&
        result->trap_snapshot.owner_cr3 == result->address_space_cr3;
}

static bool trap_snapshot_rsp0_ok(const user_exec_run_result_t *result) {
    return trap_snapshot_valid(result) && result->trap_snapshot.rsp0_matched &&
        result->trap_snapshot.owner_rsp0 == result->kernel_stack_top;
}

/* Fold the armed CPL3 #BP capture from this run into the run info. The demo
 * program issues exactly one int3 with register canaries loaded; the frame
 * must be a ring3 frame that landed at the exact top of this process's
 * ring0 entry stack. */
static void collect_trap_capture(
    const bootstrap_process_t *process, user_exec_run_result_t *result) {
    trapframe_capture_result_t cap = {0};
    bootstrap_process_trap_snapshot_t snapshot = {0};
    user_exec_info_t *info = &result->info;
    const interrupt_frame_t *f;
    uint64_t stack_top;
    bool taken;
    bool snapshot_loaded;

    taken = trapframe_capture_take(&cap);
    stack_top = process->kernel_stack_top;
    result->trap_snapshot_storage =
        (uint64_t)(uintptr_t)&process->trap_snapshot;
    snapshot_loaded = taken && cap.captures == 1ULL &&
        bootstrap_process_get_trap_snapshot(process->slot, &snapshot) ==
            AIOS_OK &&
        snapshot.captures == 1ULL &&
        snapshot.frame_addr == cap.frame_addr &&
        memcmp(&snapshot.frame, &cap.frame, sizeof(snapshot.frame)) == 0;
    result->trap_snapshot_loaded = snapshot_loaded;
    if (snapshot_loaded) {
        result->trap_snapshot = snapshot;
    }
    f = snapshot_loaded ? &snapshot.frame : &cap.frame;

    info->trap_captures = cap.captures;
    info->trap_captured = snapshot_loaded;
    info->trap_cs = f->cs;
    info->trap_ss = f->ss;
    info->trap_from_user = snapshot_loaded &&
        interrupt_frame_from_user(f) &&
        f->cs == (uint64_t)AIOS_USER_CS_RPL3 &&
        f->ss == (uint64_t)AIOS_USER_DS_RPL3;
    info->trap_rsp_in_user = snapshot_loaded &&
        f->rsp >= AIOS_BOOTSTRAP_USER_BASE &&
        f->rsp < AIOS_BOOTSTRAP_USER_END;
    info->trap_rip_in_user = snapshot_loaded &&
        f->rip >= AIOS_BOOTSTRAP_USER_BASE &&
        f->rip < AIOS_BOOTSTRAP_USER_END;
    info->trap_canary_ok = snapshot_loaded &&
        f->rbx == TRAPFRAME_SELFTEST_CANARY(13) &&
        f->rcx == TRAPFRAME_SELFTEST_CANARY(12) &&
        f->r8  == TRAPFRAME_SELFTEST_CANARY(7) &&
        f->r12 == TRAPFRAME_SELFTEST_CANARY(3);
    info->trap_frame_in_kstack = snapshot_loaded &&
        snapshot.frame_addr >= process->kernel_stack_base &&
        snapshot.frame_addr + (uint64_t)TRAPFRAME_SIZE <= stack_top;
    /* A CPL3->0 breakpoint enters on TSS rsp0 == stack_top (16-byte
     * aligned), so the frame base is exactly stack_top - TRAPFRAME_SIZE —
     * the same exactness style as the int80 stack_top-40 entry proof. */
    info->trap_frame_addr_exact = snapshot_loaded &&
        snapshot.frame_addr == stack_top - (uint64_t)TRAPFRAME_SIZE;
}

static void emit_trap_capture_marker(
    const user_exec_info_t *a, const user_exec_info_t *b) {
    bool pass = run_trap_evidence_ok(a) && run_trap_evidence_ok(b) &&
        a->trap_cs == b->trap_cs && a->trap_ss == b->trap_ss &&
        trapframe_contract_passed();

    serial_printf("[TRAP] user frame capture %s pid_a=%u pid_b=%u captures_a=%u captures_b=%u from_user=%u cs=%x ss=%x rsp_user=%u rip_user=%u canary_ok=%u frame_in_kstack=%u frame_addr_exact=%u contract=%u\n",
        pass ? "PASS" : "FAIL",
        (uint64_t)a->process_id,
        (uint64_t)b->process_id,
        a->trap_captures,
        b->trap_captures,
        (uint64_t)(a->trap_from_user && b->trap_from_user),
        a->trap_cs,
        a->trap_ss,
        (uint64_t)(a->trap_rsp_in_user && b->trap_rsp_in_user),
        (uint64_t)(a->trap_rip_in_user && b->trap_rip_in_user),
        (uint64_t)(a->trap_canary_ok && b->trap_canary_ok),
        (uint64_t)(a->trap_frame_in_kstack && b->trap_frame_in_kstack),
        (uint64_t)(a->trap_frame_addr_exact && b->trap_frame_addr_exact),
        (uint64_t)trapframe_contract_passed());
}

static void emit_trap_snapshot_marker(void) {
    serial_printf("[PROC] trap evidence snapshot %s schema=1 captures=%u pid_a=%u slot_a=%u seq_a=%u valid_a=%u owner_a=%u frame_a=%u cr3_a=%u rsp0_a=%u pid_b=%u slot_b=%u seq_b=%u valid_b=%u owner_b=%u frame_b=%u cr3_b=%u rsp0_b=%u distinct_storage=%u current_pid=%u stale_owner=%u resume_ready=%u\n",
        g_user_exec_pair.passed ? "PASS" : "FAIL",
        g_user_exec_pair.saved_captures,
        (uint64_t)g_user_exec_pair.first_process_id,
        (uint64_t)g_user_exec_pair.first_address_space_slot,
        g_user_exec_pair.saved_seq_a,
        (uint64_t)g_user_exec_pair.saved_valid_a,
        (uint64_t)g_user_exec_pair.saved_owner_a,
        (uint64_t)g_user_exec_pair.saved_frame_a,
        (uint64_t)g_user_exec_pair.saved_cr3_a,
        (uint64_t)g_user_exec_pair.saved_rsp0_a,
        (uint64_t)g_user_exec_pair.second_process_id,
        (uint64_t)g_user_exec_pair.second_address_space_slot,
        g_user_exec_pair.saved_seq_b,
        (uint64_t)g_user_exec_pair.saved_valid_b,
        (uint64_t)g_user_exec_pair.saved_owner_b,
        (uint64_t)g_user_exec_pair.saved_frame_b,
        (uint64_t)g_user_exec_pair.saved_cr3_b,
        (uint64_t)g_user_exec_pair.saved_rsp0_b,
        (uint64_t)g_user_exec_pair.saved_distinct_storage,
        (uint64_t)g_user_exec_pair.saved_current_pid,
        (uint64_t)g_user_exec_pair.saved_stale_owner,
        (uint64_t)g_user_exec_pair.saved_resume_ready);
}

static void emit_pair_marker(void) {
    serial_printf("[USER] bootstrap process pair %s runs=%u order=1,2 pid_a=%u slot_a=%u pid_b=%u slot_b=%u distinct_pid=%u distinct_slot=%u distinct_cr3=%u distinct_backing=%u distinct_stack=%u int80_a=%u int80_b=%u between_clean=%u current_pid=%u last_pid=%u rsp0_publishes=%u rsp0_restores=%u tss_rsp0_baseline=%u both_restored=%u\n",
        g_user_exec_pair.passed ? "PASS" : "FAIL",
        g_user_exec_pair.completed_runs,
        (uint64_t)g_user_exec_pair.first_process_id,
        (uint64_t)g_user_exec_pair.first_address_space_slot,
        (uint64_t)g_user_exec_pair.second_process_id,
        (uint64_t)g_user_exec_pair.second_address_space_slot,
        (uint64_t)g_user_exec_pair.distinct_pid,
        (uint64_t)g_user_exec_pair.distinct_slot,
        (uint64_t)g_user_exec_pair.distinct_cr3,
        (uint64_t)g_user_exec_pair.distinct_backing,
        (uint64_t)g_user_exec_pair.distinct_kernel_stack,
        (uint64_t)g_user_exec_pair.first_int80_entries,
        (uint64_t)g_user_exec_pair.second_int80_entries,
        (uint64_t)g_user_exec_pair.between_runs_clean,
        (uint64_t)g_user_exec_pair.current_pid,
        (uint64_t)g_user_exec_pair.last_pid,
        g_user_exec_pair.rsp0_publishes,
        g_user_exec_pair.rsp0_restores,
        (uint64_t)g_user_exec_pair.tss_rsp0_baseline,
        (uint64_t)g_user_exec_pair.both_restored);
}

static aios_status_t user_exec_run_slot(
    uint32_t slot, user_exec_run_result_t *result,
    bool emit_primary_markers) {
    node_pipeline_snapshot_t *const user_result =
        (node_pipeline_snapshot_t *)(uintptr_t)AIOS_BOOTSTRAP_USER_BUFFER;
    bootstrap_process_t *process = NULL;
    bootstrap_process_guard_t process_guard = {0};
    user_exec_info_t *info;
    uint64_t image_size;
    elf_load_result_t elf = {0};
    aios_status_t elf_status = AIOS_ERR_IO;
    aios_status_t status;
    aios_status_t finish_status;
    uint64_t enter_ns = 0;
    uint64_t exit_ns = 0;
    uint32_t observed_max = 0;
    int64_t reject_value = 0;
    bool window_active = false;
    bool result_collected = false;
    bool ok;

    if (!result || slot >= BOOTSTRAP_PROCESS_COUNT) {
        return AIOS_ERR_INVAL;
    }

    memset(result, 0, sizeof(*result));
    info = &result->info;
    info->attempted = true;
    info->address_space_slot = slot;
    info->tensor_range_excluded =
        tensor_mm_bootstrap_user_range_excluded();
    image_size = (uint64_t)((uintptr_t)user_elf_image_end -
                            (uintptr_t)user_elf_image_start);

    if (!info->tensor_range_excluded) {
        serial_write("[USER] ring3 exec ABORT: tensor range exclusion failed\n");
        return AIOS_ERR_BUSY;
    }

    /* Bind the selected static slot and its dedicated ring0 entry stack to
     * one bootstrap process, then activate its private CR3 with IF=0. */
    status = bootstrap_process_prepare(slot, true, &process);
    if (status != AIOS_OK) {
        serial_write("[USER] ring3 exec ABORT: process prepare failed\n");
        return status;
    }
    status = bootstrap_process_activate(process, &process_guard);
    if (status != AIOS_OK) {
        aios_status_t cancel_status;

        if (process_guard.active) {
            serial_write("[USER] ring3 exec FATAL: process activation rollback unproven\n");
            kernel_panic("Bootstrap process activation rollback failed");
        }
        cancel_status = bootstrap_process_cancel(process);
        if (cancel_status != AIOS_OK) {
            serial_write("[USER] ring3 exec FATAL: process cancellation unproven\n");
            kernel_panic("Bootstrap process cancellation failed");
        }
        serial_write("[USER] ring3 exec ABORT: process activation failed\n");
        return status;
    }

    info->private_cr3 = true;
    info->process_bound = true;
    info->process_id = process->pid;
    info->kernel_stack_bytes = (uint32_t)process->kernel_stack_size;
    info->rsp0_changed = process_guard.rsp0_changed;
    info->rsp0_published = process_guard.rsp0.published;
    result->address_space_cr3 = process->address_space.cr3;
    result->address_space_backing = process->address_space.backing_phys;
    result->kernel_stack_base = process->kernel_stack_base;
    result->kernel_stack_top = process->kernel_stack_top;
    result->run_generation = process->run_generation;

    /* Load the ELF image and scratch buffers into the active private user
     * mapping. These deliberate kernel accesses are SMAP fenced. */
    user_access_fence_begin();
    elf_status = elf_load(user_elf_image_start, image_size,
                          AIOS_BOOTSTRAP_USER_BASE,
                          AIOS_BOOTSTRAP_USER_SIZE, &elf);
    if (elf_status == AIOS_OK) {
        memset(user_result, 0, sizeof(*user_result));
        *(volatile int64_t *)(uintptr_t)AIOS_BOOTSTRAP_USER_REJECT = 0;
    }
    user_access_fence_end();

    if (elf_status != AIOS_OK) {
        serial_write("[USER] ring3 exec ABORT: ELF load failed\n");
        goto cleanup;
    }

    info->elf_loaded = true;
    info->elf_entry = elf.entry;
    info->elf_segments = elf.loadable_segments;

    /* Constrain uaccess to the active private mapping and enter ring3. */
    user_access_set_window(AIOS_BOOTSTRAP_USER_BASE,
                           AIOS_BOOTSTRAP_USER_SIZE);
    window_active = true;
    enter_ns = kernel_time_monotonic_ns();
    /* Arm the one-shot #BP capture: the demo program issues exactly one
     * canary-loaded int3, which is this run's CPL3 trapframe evidence. */
    trapframe_capture_arm();
    user_mode_run(elf.entry, AIOS_BOOTSTRAP_USER_STACK_TOP,
                  &process->run_state);
    exit_ns = kernel_time_monotonic_ns();
    collect_trap_capture(process, result);

    info->user_syscalls = (uint32_t)process->run_state.user_syscalls;
    info->exit_code = process->run_state.exit_code;
    info->duration_ns = exit_ns - enter_ns;
    info->entered = process->run_state.user_syscalls >= 1;
    info->returned = process->run_state.exited != 0;

    /* Read back the syscall result and hostile-pointer rejection from this
     * slot's backing while the same private CR3 remains active. */
    user_access_fence_begin();
    observed_max = user_result->max_pipelines;
    reject_value = *(volatile int64_t *)(uintptr_t)
        AIOS_BOOTSTRAP_USER_REJECT;
    user_access_fence_end();
    result_collected = true;

    info->observed_pipeline_max = observed_max;
    info->syscall_ok = observed_max == NODE_PIPELINE_MAX;
    info->boundary_ok = reject_value == (int64_t)AIOS_ERR_PERM;

cleanup:
    if (window_active) {
        user_access_clear_window();
    }
    finish_status = bootstrap_process_finish(process, &process_guard);
    if (process_guard.active) {
        serial_write("[USER] ring3 exec FATAL: process restoration unproven\n");
        kernel_panic("Bootstrap process restoration failed");
    }

    info->cr3_restored = process_guard.address_space.cr3_restored;
    info->if_restored = process_guard.address_space.if_restored;
    info->leaf_sealed = process_guard.leaf_sealed;
    info->nx_enforced = process_guard.nx_enforced;
    info->int80_entries = (uint32_t)process_guard.int80_entries;
    info->all_int80_entries_in_stack =
        process_guard.all_int80_entries_in_stack;
    info->rsp0_restored = process_guard.rsp0.restored;
    info->kernel_stack_floor_canary_ok =
        process_guard.kernel_stack_floor_canary_ok;
    info->tensor_range_excluded =
        tensor_mm_bootstrap_user_range_excluded();

    (void)trap_snapshot_refresh(result);

    ok = result_collected &&
         info->elf_loaded && info->entered && info->returned &&
         info->syscall_ok && info->boundary_ok && info->exit_code == 42 &&
         info->private_cr3 && info->cr3_restored && info->if_restored &&
         info->leaf_sealed && info->tensor_range_excluded &&
         info->process_bound &&
         info->process_id == BOOTSTRAP_PROCESS_PID_BASE + slot &&
         info->address_space_slot == slot &&
         info->kernel_stack_bytes == AIOS_USER_KERNEL_STACK_SIZE &&
         info->rsp0_changed && info->rsp0_published &&
         info->int80_entries == 3U &&
         info->all_int80_entries_in_stack && info->rsp0_restored &&
         info->kernel_stack_floor_canary_ok &&
         run_trap_evidence_ok(info) &&
         trap_snapshot_owner_ok(result) &&
         trap_snapshot_frame_ok(result) &&
         trap_snapshot_cr3_ok(result) &&
         trap_snapshot_rsp0_ok(result) &&
         finish_status == AIOS_OK;

    if (emit_primary_markers) {
        serial_printf("[USER] ring3 exec %s elf_entry=%x segments=%u entered=%u returned=%u syscalls=%u boundary_ok=%u exit_code=%u pipe_max=%u dur_ns=%u private_cr3=%u slot=%u cr3_restored=%u if_restored=%u leaf_sealed=%u nx_enforced=%u tensor_excluded=%u\n",
            ok ? "PASS" : "FAIL",
            info->elf_entry,
            (uint64_t)info->elf_segments,
            (uint64_t)info->entered,
            (uint64_t)info->returned,
            (uint64_t)info->user_syscalls,
            (uint64_t)info->boundary_ok,
            info->exit_code,
            (uint64_t)info->observed_pipeline_max,
            info->duration_ns,
            (uint64_t)info->private_cr3,
            (uint64_t)info->address_space_slot,
            (uint64_t)info->cr3_restored,
            (uint64_t)info->if_restored,
            (uint64_t)info->leaf_sealed,
            (uint64_t)info->nx_enforced,
            (uint64_t)info->tensor_range_excluded);

        if (ok) {
            serial_printf("[USER] private address space exec PASS slot=%u cr3_restored=1 if_restored=1 leaf_sealed=1 nx_enforced=%u tensor_excluded=1\n",
                (uint64_t)info->address_space_slot,
                (uint64_t)info->nx_enforced);
            serial_printf("[USER] bootstrap process stack PASS pid=%u slot=%u process_bound=1 kstack_bytes=%u rsp0_changed=1 rsp0_published=1 int80_entries=%u all_int80_entries_in_stack=1 rsp0_restored=1 kstack_floor_canary=1\n",
                (uint64_t)info->process_id,
                (uint64_t)info->address_space_slot,
                (uint64_t)info->kernel_stack_bytes,
                (uint64_t)info->int80_entries);
        }
    } else {
        serial_printf("[USER] secondary process exec %s pid=%u slot=%u entered=%u returned=%u syscall_ok=%u boundary_ok=%u exit_code=%u int80_entries=%u cr3_restored=%u if_restored=%u leaf_sealed=%u nx_enforced=%u rsp0_restored=%u kstack_floor_canary=%u\n",
            ok ? "PASS" : "FAIL",
            (uint64_t)info->process_id,
            (uint64_t)info->address_space_slot,
            (uint64_t)info->entered,
            (uint64_t)info->returned,
            (uint64_t)info->syscall_ok,
            (uint64_t)info->boundary_ok,
            info->exit_code,
            (uint64_t)info->int80_entries,
            (uint64_t)info->cr3_restored,
            (uint64_t)info->if_restored,
            (uint64_t)info->leaf_sealed,
            (uint64_t)info->nx_enforced,
            (uint64_t)info->rsp0_restored,
            (uint64_t)info->kernel_stack_floor_canary_ok);
    }

    if (ok) {
        return AIOS_OK;
    }
    if (elf_status != AIOS_OK) {
        return elf_status;
    }
    return AIOS_ERR_IO;
}

aios_status_t user_exec_run_bootstrap_pair(void) {
    user_exec_run_result_t first = {0};
    user_exec_run_result_t second = {0};
    bootstrap_process_stats_t before = {0};
    bootstrap_process_stats_t between = {0};
    bootstrap_process_stats_t after = {0};
    aios_status_t first_status;
    aios_status_t second_status;

    if (g_user_exec_pair.attempted) {
        return AIOS_ERR_BUSY;
    }

    memset(&g_user_exec, 0, sizeof(g_user_exec));
    memset(&g_user_exec_pair, 0, sizeof(g_user_exec_pair));
    g_user_exec_pair.attempted = true;

    bootstrap_process_get_stats(&before);
    if (!bootstrap_process_ready() || bootstrap_process_current() ||
        before.current_pid != 0 || before.completed_runs != 0 ||
        before.rsp0_publishes != 0 || before.rsp0_restores != 0 ||
        !before.tss_rsp0_baseline) {
        emit_pair_marker();
        return AIOS_ERR_BUSY;
    }

    first_status = user_exec_run_slot(
        USER_EXEC_PRIMARY_SLOT, &first, true);
    g_user_exec = first.info;
    g_user_exec_pair.first_process_id = first.info.process_id;
    g_user_exec_pair.first_address_space_slot =
        first.info.address_space_slot;
    g_user_exec_pair.first_int80_entries = first.info.int80_entries;
    if (first_status != AIOS_OK) {
        bootstrap_process_get_stats(&after);
        g_user_exec_pair.completed_runs = after.completed_runs;
        g_user_exec_pair.current_pid = after.current_pid;
        g_user_exec_pair.last_pid = after.last_pid;
        g_user_exec_pair.rsp0_publishes = after.rsp0_publishes;
        g_user_exec_pair.rsp0_restores = after.rsp0_restores;
        g_user_exec_pair.tss_rsp0_baseline = after.tss_rsp0_baseline;
        emit_pair_marker();
        return first_status;
    }

    bootstrap_process_get_stats(&between);
    g_user_exec_pair.between_runs_clean =
        !bootstrap_process_current() &&
        between.current_pid == 0 &&
        between.last_pid == BOOTSTRAP_PROCESS_PID_BASE &&
        between.completed_runs == before.completed_runs + 1U &&
        between.rsp0_publishes == before.rsp0_publishes + 1U &&
        between.rsp0_restores == before.rsp0_restores + 1U &&
        between.tss_rsp0_baseline;
    if (bootstrap_process_current() || between.current_pid != 0 ||
        !between.tss_rsp0_baseline) {
        serial_write("[USER] bootstrap process pair FATAL: unsafe state between runs\n");
        kernel_panic("Bootstrap process pair inter-run restoration failed");
    }
    if (!g_user_exec_pair.between_runs_clean) {
        g_user_exec_pair.completed_runs = between.completed_runs;
        g_user_exec_pair.current_pid = between.current_pid;
        g_user_exec_pair.last_pid = between.last_pid;
        g_user_exec_pair.rsp0_publishes = between.rsp0_publishes;
        g_user_exec_pair.rsp0_restores = between.rsp0_restores;
        g_user_exec_pair.tss_rsp0_baseline = between.tss_rsp0_baseline;
        emit_pair_marker();
        return AIOS_ERR_IO;
    }

    second_status = user_exec_run_slot(
        USER_EXEC_SECONDARY_SLOT, &second, false);
    bootstrap_process_get_stats(&after);

    /* Re-read both descriptor-owned snapshots at the final pair boundary.
     * The first run's post-finish check alone cannot prove that slot 1's
     * execution left slot 0's durable evidence untouched. */
    (void)trap_snapshot_refresh(&first);
    (void)trap_snapshot_refresh(&second);

    g_user_exec_pair.completed_runs = after.completed_runs;
    g_user_exec_pair.second_process_id = second.info.process_id;
    g_user_exec_pair.second_address_space_slot =
        second.info.address_space_slot;
    g_user_exec_pair.second_int80_entries = second.info.int80_entries;
    g_user_exec_pair.second_exit_code = second.info.exit_code;
    g_user_exec_pair.second_syscall_ok = second.info.syscall_ok;
    g_user_exec_pair.second_boundary_ok = second.info.boundary_ok;
    g_user_exec_pair.distinct_pid =
        first.info.process_id != second.info.process_id;
    g_user_exec_pair.distinct_slot =
        first.info.address_space_slot != second.info.address_space_slot;
    g_user_exec_pair.distinct_cr3 =
        first.address_space_cr3 != second.address_space_cr3;
    g_user_exec_pair.distinct_backing =
        first.address_space_backing != second.address_space_backing;
    g_user_exec_pair.distinct_kernel_stack =
        first.kernel_stack_base != second.kernel_stack_base;
    g_user_exec_pair.current_pid = after.current_pid;
    g_user_exec_pair.last_pid = after.last_pid;
    g_user_exec_pair.rsp0_publishes = after.rsp0_publishes;
    g_user_exec_pair.rsp0_restores = after.rsp0_restores;
    g_user_exec_pair.tss_rsp0_baseline = after.tss_rsp0_baseline;
    g_user_exec_pair.both_restored =
        run_restored(&first.info) && run_restored(&second.info) &&
        !bootstrap_process_current() && after.current_pid == 0 &&
        after.tss_rsp0_baseline;
    g_user_exec_pair.saved_captures =
        first.trap_snapshot.captures + second.trap_snapshot.captures;
    g_user_exec_pair.saved_seq_a =
        first.trap_snapshot.capture_sequence;
    g_user_exec_pair.saved_seq_b =
        second.trap_snapshot.capture_sequence;
    g_user_exec_pair.saved_valid_a = trap_snapshot_valid(&first);
    g_user_exec_pair.saved_valid_b = trap_snapshot_valid(&second);
    g_user_exec_pair.saved_owner_a = trap_snapshot_owner_ok(&first);
    g_user_exec_pair.saved_owner_b = trap_snapshot_owner_ok(&second);
    g_user_exec_pair.saved_frame_a = trap_snapshot_frame_ok(&first);
    g_user_exec_pair.saved_frame_b = trap_snapshot_frame_ok(&second);
    g_user_exec_pair.saved_cr3_a = trap_snapshot_cr3_ok(&first);
    g_user_exec_pair.saved_cr3_b = trap_snapshot_cr3_ok(&second);
    g_user_exec_pair.saved_rsp0_a = trap_snapshot_rsp0_ok(&first);
    g_user_exec_pair.saved_rsp0_b = trap_snapshot_rsp0_ok(&second);
    g_user_exec_pair.saved_distinct_storage =
        first.trap_snapshot_storage != 0 &&
        second.trap_snapshot_storage != 0 &&
        first.trap_snapshot_storage != second.trap_snapshot_storage;
    g_user_exec_pair.saved_current_pid = after.current_pid;
    g_user_exec_pair.saved_stale_owner =
        !g_user_exec_pair.saved_owner_a ||
        !g_user_exec_pair.saved_owner_b;
    g_user_exec_pair.saved_resume_ready =
        first.trap_snapshot.resume_ready ||
        second.trap_snapshot.resume_ready;

    if (bootstrap_process_current() || after.current_pid != 0 ||
        !after.tss_rsp0_baseline) {
        serial_write("[USER] bootstrap process pair FATAL: unsafe final ownership state\n");
        kernel_panic("Bootstrap process pair final restoration failed");
    }

    g_user_exec_pair.passed =
        first_status == AIOS_OK && second_status == AIOS_OK &&
        g_user_exec_pair.completed_runs == before.completed_runs + 2U &&
        g_user_exec_pair.first_process_id == BOOTSTRAP_PROCESS_PID_BASE &&
        g_user_exec_pair.first_address_space_slot ==
            USER_EXEC_PRIMARY_SLOT &&
        g_user_exec_pair.second_process_id ==
            BOOTSTRAP_PROCESS_PID_BASE + USER_EXEC_SECONDARY_SLOT &&
        g_user_exec_pair.second_address_space_slot ==
            USER_EXEC_SECONDARY_SLOT &&
        g_user_exec_pair.first_int80_entries == 3U &&
        g_user_exec_pair.second_int80_entries == 3U &&
        g_user_exec_pair.second_exit_code == 42U &&
        g_user_exec_pair.second_syscall_ok &&
        g_user_exec_pair.second_boundary_ok &&
        g_user_exec_pair.distinct_pid &&
        g_user_exec_pair.distinct_slot &&
        g_user_exec_pair.distinct_cr3 &&
        g_user_exec_pair.distinct_backing &&
        g_user_exec_pair.distinct_kernel_stack &&
        g_user_exec_pair.between_runs_clean &&
        g_user_exec_pair.current_pid == 0 &&
        g_user_exec_pair.last_pid ==
            BOOTSTRAP_PROCESS_PID_BASE + USER_EXEC_SECONDARY_SLOT &&
        g_user_exec_pair.rsp0_publishes ==
            before.rsp0_publishes + 2U &&
        g_user_exec_pair.rsp0_restores ==
            before.rsp0_restores + 2U &&
        g_user_exec_pair.tss_rsp0_baseline &&
        g_user_exec_pair.both_restored &&
        g_user_exec_pair.saved_captures == 2ULL &&
        g_user_exec_pair.saved_seq_a == 1ULL &&
        g_user_exec_pair.saved_seq_b == 2ULL &&
        g_user_exec_pair.saved_valid_a &&
        g_user_exec_pair.saved_valid_b &&
        g_user_exec_pair.saved_owner_a &&
        g_user_exec_pair.saved_owner_b &&
        g_user_exec_pair.saved_frame_a &&
        g_user_exec_pair.saved_frame_b &&
        g_user_exec_pair.saved_cr3_a &&
        g_user_exec_pair.saved_cr3_b &&
        g_user_exec_pair.saved_rsp0_a &&
        g_user_exec_pair.saved_rsp0_b &&
        g_user_exec_pair.saved_distinct_storage &&
        g_user_exec_pair.saved_current_pid == 0 &&
        !g_user_exec_pair.saved_stale_owner &&
        !g_user_exec_pair.saved_resume_ready;

    emit_pair_marker();
    emit_trap_capture_marker(&first.info, &second.info);
    emit_trap_snapshot_marker();
    return g_user_exec_pair.passed ? AIOS_OK : AIOS_ERR_IO;
}

void user_exec_get_info(user_exec_info_t *out) {
    if (out) {
        *out = g_user_exec;
    }
}

void user_exec_get_pair_info(user_exec_pair_info_t *out) {
    if (out) {
        *out = g_user_exec_pair;
    }
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
