/*
 * AIOS Kernel - First Ring3 User Execution Slice
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
#include <lib/string.h>

/* Ring3 entry / syscall path (kernel/core/user_entry.asm). */
extern void user_mode_run(uint64_t entry_rip, uint64_t user_stack_top,
                          bootstrap_user_run_state_t *run_state);
/* Embedded static ELF64 demo image parsed by the ELF loader. */
extern uint8_t user_elf_image_start[];
extern uint8_t user_elf_image_end[];
#define USER_EXEC_PRIVATE_SLOT 0U

static user_exec_info_t g_user_exec;

aios_status_t user_exec_run_first(void) {
    node_pipeline_snapshot_t *const user_result =
        (node_pipeline_snapshot_t *)(uintptr_t)AIOS_BOOTSTRAP_USER_BUFFER;
    bootstrap_process_t *process = NULL;
    bootstrap_process_guard_t process_guard = {0};
    uint64_t image_size = (uint64_t)((uintptr_t)user_elf_image_end -
                                     (uintptr_t)user_elf_image_start);
    elf_load_result_t elf = {0};
    aios_status_t elf_status;
    aios_status_t status;
    aios_status_t finish_status;
    uint64_t enter_ns = 0;
    uint64_t exit_ns = 0;
    uint32_t observed_max = 0;
    int64_t reject_value = 0;
    bool window_active = false;
    bool result_collected = false;
    bool ok;

    memset(&g_user_exec, 0, sizeof(g_user_exec));
    g_user_exec.attempted = true;
    g_user_exec.address_space_slot = USER_EXEC_PRIVATE_SLOT;
    g_user_exec.tensor_range_excluded =
        tensor_mm_bootstrap_user_range_excluded();

    if (!g_user_exec.tensor_range_excluded) {
        serial_write("[USER] ring3 exec ABORT: tensor range exclusion failed\n");
        return AIOS_ERR_BUSY;
    }

    /* 1. Bind static slot 0 and its dedicated ring0 entry stack to one
     *    bootstrap process, then activate its private CR3 with IF=0. */
    status = bootstrap_process_prepare(
        USER_EXEC_PRIVATE_SLOT, true, &process);
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
    g_user_exec.private_cr3 = true;
    g_user_exec.process_bound = true;
    g_user_exec.process_id = process->pid;
    g_user_exec.kernel_stack_bytes = (uint32_t)process->kernel_stack_size;
    g_user_exec.rsp0_changed = process_guard.rsp0_changed;
    g_user_exec.rsp0_published = process_guard.rsp0.published;

    /* 2. Load the ELF image into the user region and stage the syscall
     *    scratch buffers. These are deliberate kernel writes to user pages,
     *    so bracket them with the SMAP fence (no-op when SMAP is off). The
     *    loader copies segments to their p_vaddr and zeroes any .bss tail. */
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

    g_user_exec.elf_loaded = true;
    g_user_exec.elf_entry = elf.entry;
    g_user_exec.elf_segments = elf.loadable_segments;

    /* 3. Constrain uaccess to the user page, then enter ring3 at the ELF
     *    entry point. Any syscall the program issues with a pointer outside
     *    the window is denied. The window is cleared on return so
     *    kernel-internal uaccess (which runs with no window) is unaffected. */
    user_access_set_window(AIOS_BOOTSTRAP_USER_BASE,
                           AIOS_BOOTSTRAP_USER_SIZE);
    window_active = true;
    enter_ns = kernel_time_monotonic_ns();
    user_mode_run(elf.entry, AIOS_BOOTSTRAP_USER_STACK_TOP,
                  &process->run_state);
    exit_ns = kernel_time_monotonic_ns();

    /* 4. Collect and verify the round trip. */
    g_user_exec.user_syscalls = (uint32_t)process->run_state.user_syscalls;
    g_user_exec.exit_code = process->run_state.exit_code;
    g_user_exec.duration_ns = exit_ns - enter_ns;
    g_user_exec.entered = process->run_state.user_syscalls >= 1;
    g_user_exec.returned = process->run_state.exited != 0;

    /* Read the user page's result and rejection stash back (SMAP fenced).
     * SYS_PIPE_STATS must have written the real registry capacity — proof
     * ring3 reached the kernel and got data — and the hostile syscall with
     * a kernel-range pointer must have been denied with AIOS_ERR_PERM,
     * proving the uaccess window blocked ring3 from reaching kernel memory. */
    user_access_fence_begin();
    observed_max = user_result->max_pipelines;
    reject_value = *(volatile int64_t *)(uintptr_t)
        AIOS_BOOTSTRAP_USER_REJECT;
    user_access_fence_end();
    result_collected = true;

    g_user_exec.observed_pipeline_max = observed_max;
    g_user_exec.syscall_ok = observed_max == NODE_PIPELINE_MAX;
    g_user_exec.boundary_ok = reject_value == (int64_t)AIOS_ERR_PERM;

cleanup:
    if (window_active) {
        user_access_clear_window();
    }
    finish_status = bootstrap_process_finish(process, &process_guard);
    if (process_guard.active) {
        serial_write("[USER] ring3 exec FATAL: process restoration unproven\n");
        kernel_panic("Bootstrap process restoration failed");
    }
    g_user_exec.cr3_restored = process_guard.address_space.cr3_restored;
    g_user_exec.if_restored = process_guard.address_space.if_restored;
    g_user_exec.leaf_sealed = process_guard.leaf_sealed;
    g_user_exec.nx_enforced = process_guard.nx_enforced;
    g_user_exec.int80_entries = (uint32_t)process_guard.int80_entries;
    g_user_exec.all_int80_entries_in_stack =
        process_guard.all_int80_entries_in_stack;
    g_user_exec.rsp0_restored = process_guard.rsp0.restored;
    g_user_exec.kernel_stack_floor_canary_ok =
        process_guard.kernel_stack_floor_canary_ok;
    g_user_exec.tensor_range_excluded =
        tensor_mm_bootstrap_user_range_excluded();

    ok = result_collected &&
         g_user_exec.elf_loaded && g_user_exec.entered &&
         g_user_exec.returned && g_user_exec.syscall_ok &&
         g_user_exec.boundary_ok && g_user_exec.exit_code == 42 &&
         g_user_exec.private_cr3 && g_user_exec.cr3_restored &&
         g_user_exec.if_restored && g_user_exec.leaf_sealed &&
         g_user_exec.tensor_range_excluded &&
         g_user_exec.process_bound && g_user_exec.process_id == 1U &&
         g_user_exec.kernel_stack_bytes == AIOS_USER_KERNEL_STACK_SIZE &&
         g_user_exec.rsp0_changed && g_user_exec.rsp0_published &&
         g_user_exec.int80_entries == 3U &&
         g_user_exec.all_int80_entries_in_stack &&
         g_user_exec.rsp0_restored &&
         g_user_exec.kernel_stack_floor_canary_ok &&
         finish_status == AIOS_OK;

    serial_printf("[USER] ring3 exec %s elf_entry=%x segments=%u entered=%u returned=%u syscalls=%u boundary_ok=%u exit_code=%u pipe_max=%u dur_ns=%u private_cr3=%u slot=%u cr3_restored=%u if_restored=%u leaf_sealed=%u nx_enforced=%u tensor_excluded=%u\n",
        ok ? "PASS" : "FAIL",
        g_user_exec.elf_entry,
        (uint64_t)g_user_exec.elf_segments,
        (uint64_t)g_user_exec.entered,
        (uint64_t)g_user_exec.returned,
        (uint64_t)g_user_exec.user_syscalls,
        (uint64_t)g_user_exec.boundary_ok,
        g_user_exec.exit_code,
        (uint64_t)g_user_exec.observed_pipeline_max,
        g_user_exec.duration_ns,
        (uint64_t)g_user_exec.private_cr3,
        (uint64_t)g_user_exec.address_space_slot,
        (uint64_t)g_user_exec.cr3_restored,
        (uint64_t)g_user_exec.if_restored,
        (uint64_t)g_user_exec.leaf_sealed,
        (uint64_t)g_user_exec.nx_enforced,
        (uint64_t)g_user_exec.tensor_range_excluded);

    if (ok) {
        serial_printf("[USER] private address space exec PASS slot=%u cr3_restored=1 if_restored=1 leaf_sealed=1 nx_enforced=%u tensor_excluded=1\n",
            (uint64_t)g_user_exec.address_space_slot,
            (uint64_t)g_user_exec.nx_enforced);
        serial_printf("[USER] bootstrap process stack PASS pid=%u slot=%u process_bound=1 kstack_bytes=%u rsp0_changed=1 rsp0_published=1 int80_entries=%u all_int80_entries_in_stack=1 rsp0_restored=1 kstack_floor_canary=1\n",
            (uint64_t)g_user_exec.process_id,
            (uint64_t)g_user_exec.address_space_slot,
            (uint64_t)g_user_exec.kernel_stack_bytes,
            (uint64_t)g_user_exec.int80_entries);
    }

    if (ok) {
        return AIOS_OK;
    }
    if (elf_status != AIOS_OK) {
        return elf_status;
    }
    return AIOS_ERR_IO;
}

void user_exec_get_info(user_exec_info_t *out) {
    if (!out) {
        return;
    }
    *out = g_user_exec;
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
