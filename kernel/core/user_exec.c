/*
 * AIOS Kernel - First Ring3 User Execution Slice
 * AI-Native Operating System
 */

#include <kernel/user_exec.h>
#include <kernel/user_access.h>
#include <kernel/elf.h>
#include <kernel/time.h>
#include <kernel/user_layout.h>
#include <mm/address_space.h>
#include <mm/tensor_mm.h>
#include <runtime/node_pipeline.h>
#include <drivers/serial.h>
#include <interrupt/idt.h>
#include <lib/string.h>

/* Ring3 entry / syscall path (kernel/core/user_entry.asm). */
extern void user_mode_run(uint64_t entry_rip, uint64_t user_stack_top);
/* Embedded static ELF64 demo image parsed by the ELF loader. */
extern uint8_t user_elf_image_start[];
extern uint8_t user_elf_image_end[];
extern volatile uint64_t g_user_syscalls;
extern volatile uint64_t g_user_exit_code;
extern volatile uint8_t  g_user_exited;

#define USER_EXEC_PRIVATE_SLOT 0U

static user_exec_info_t g_user_exec;

aios_status_t user_exec_run_first(void) {
    node_pipeline_snapshot_t *const user_result =
        (node_pipeline_snapshot_t *)(uintptr_t)AIOS_BOOTSTRAP_USER_BUFFER;
    address_space_bootstrap_slot_t space = {0};
    address_space_guard_t guard = {0};
    uint64_t image_size = (uint64_t)((uintptr_t)user_elf_image_end -
                                     (uintptr_t)user_elf_image_start);
    elf_load_result_t elf = {0};
    aios_status_t elf_status;
    aios_status_t status;
    aios_status_t restore_status;
    aios_status_t seal_status;
    uint64_t enter_ns = 0;
    uint64_t exit_ns = 0;
    uint32_t observed_max = 0;
    int64_t reject_value = 0;
    bool window_active = false;
    bool result_collected = false;
    bool activation_nx_enforced = false;
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

    /* 1. Prepare and activate static slot 0. Unlike the original runner,
     *    this never opens the boot page table's identity leaf to ring3. */
    status = address_space_bootstrap_slot_prepare(
        USER_EXEC_PRIVATE_SLOT, true, &space);
    if (status != AIOS_OK) {
        serial_write("[USER] ring3 exec ABORT: private slot prepare failed\n");
        return status;
    }
    status = address_space_activate(&space, &guard);
    if (status != AIOS_OK) {
        if (guard.active) {
            serial_write("[USER] ring3 exec FATAL: private CR3 rollback unproven\n");
            kernel_panic("Private CR3 activation rollback failed");
        }
        if (guard.previous_cr3 == 0 ||
            (guard.cr3_restored && guard.if_restored)) {
            (void)address_space_bootstrap_slot_seal(
                USER_EXEC_PRIVATE_SLOT, &activation_nx_enforced);
        }
        serial_write("[USER] ring3 exec ABORT: private CR3 activation failed\n");
        return status;
    }
    g_user_exec.private_cr3 = true;

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

    g_user_syscalls = 0;
    g_user_exit_code = 0;
    g_user_exited = 0;

    /* 3. Constrain uaccess to the user page, then enter ring3 at the ELF
     *    entry point. Any syscall the program issues with a pointer outside
     *    the window is denied. The window is cleared on return so
     *    kernel-internal uaccess (which runs with no window) is unaffected. */
    user_access_set_window(AIOS_BOOTSTRAP_USER_BASE,
                           AIOS_BOOTSTRAP_USER_SIZE);
    window_active = true;
    enter_ns = kernel_time_monotonic_ns();
    user_mode_run(elf.entry, AIOS_BOOTSTRAP_USER_STACK_TOP);
    exit_ns = kernel_time_monotonic_ns();

    /* 4. Collect and verify the round trip. */
    g_user_exec.user_syscalls = (uint32_t)g_user_syscalls;
    g_user_exec.exit_code = g_user_exit_code;
    g_user_exec.duration_ns = exit_ns - enter_ns;
    g_user_exec.entered = g_user_syscalls >= 1;
    g_user_exec.returned = g_user_exited != 0;

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
    restore_status = address_space_restore(&guard);
    g_user_exec.cr3_restored = guard.cr3_restored;
    g_user_exec.if_restored = guard.if_restored;
    if (guard.active) {
        serial_write("[USER] ring3 exec FATAL: boot CR3 restoration unproven\n");
        kernel_panic("Private CR3 restoration failed");
    }
    if (g_user_exec.cr3_restored && g_user_exec.if_restored &&
        !guard.active) {
        seal_status = address_space_bootstrap_slot_seal(
            USER_EXEC_PRIVATE_SLOT, &g_user_exec.nx_enforced);
        g_user_exec.leaf_sealed = seal_status == AIOS_OK;
    } else {
        seal_status = AIOS_ERR_BUSY;
    }
    g_user_exec.tensor_range_excluded =
        tensor_mm_bootstrap_user_range_excluded();

    ok = result_collected &&
         g_user_exec.elf_loaded && g_user_exec.entered &&
         g_user_exec.returned && g_user_exec.syscall_ok &&
         g_user_exec.boundary_ok && g_user_exec.exit_code == 42 &&
         g_user_exec.private_cr3 && g_user_exec.cr3_restored &&
         g_user_exec.if_restored && g_user_exec.leaf_sealed &&
         g_user_exec.tensor_range_excluded;

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
    }

    if (ok) {
        return AIOS_OK;
    }
    if (elf_status != AIOS_OK) {
        return elf_status;
    }
    (void)restore_status;
    (void)seal_status;
    return AIOS_ERR_IO;
}

void user_exec_get_info(user_exec_info_t *out) {
    if (!out) {
        return;
    }
    *out = g_user_exec;
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
