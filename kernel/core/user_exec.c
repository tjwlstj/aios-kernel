/*
 * AIOS Kernel - First Ring3 User Execution Slice
 * AI-Native Operating System
 */

#include <kernel/user_exec.h>
#include <kernel/time.h>
#include <runtime/node_pipeline.h>
#include <drivers/serial.h>
#include <lib/string.h>

/* Ring3 entry / syscall path / demo program (kernel/core/user_entry.asm). */
extern void user_mode_run(uint64_t entry_rip, uint64_t user_stack_top);
extern uint8_t user_program_start[];
extern uint8_t user_program_end[];
extern volatile uint64_t g_user_syscalls;
extern volatile uint64_t g_user_exit_code;
extern volatile uint8_t  g_user_exited;

/* Boot page tables. Final user permission is the AND of the U/S bit across
 * every level, so the covering entry at p4/p3/p2 all get the User bit. */
extern uint64_t p4_table[];
extern uint64_t p3_table[];
extern uint64_t p2_table_0[];

/* Fixed user region well clear of the kernel image, heap, and tensor pool
 * (QEMU smoke runs with 256MB). Identity mapped, so virtual == physical. */
#define USER_REGION_BASE   0x4000000UL           /* 64 MB */
#define USER_REGION_SIZE   0x200000UL            /* one 2MB huge page */
#define USER_BUFFER_ADDR   (USER_REGION_BASE + 0x1000UL)
#define USER_STACK_TOP     (USER_REGION_BASE + USER_REGION_SIZE - 16UL)

/* 2MB PDE flags: Present | Writable | User | PageSize (huge), NX cleared so
 * ring3 can execute the copied program. This is the one deliberate W^X+U
 * page in the system; keeping it to a single fixed region is intentional. */
#define PDE_PRESENT   0x001UL
#define PDE_WRITABLE  0x002UL
#define PDE_USER      0x004UL
#define PDE_HUGE      0x080UL
#define USER_PDE_FLAGS (PDE_PRESENT | PDE_WRITABLE | PDE_USER | PDE_HUGE)

static user_exec_info_t g_user_exec;

static void tlb_flush_all(void) {
    __asm__ volatile ("mov %%cr3, %%rax; mov %%rax, %%cr3"
        ::: "rax", "memory");
}

static void map_user_region(void) {
    uint32_t pml4_index = (uint32_t)((USER_REGION_BASE >> 39) & 0x1FF);
    uint32_t pdpt_index = (uint32_t)((USER_REGION_BASE >> 30) & 0x1FF);
    uint32_t pde_index = (uint32_t)((USER_REGION_BASE >> 21) & 0x1FF);

    /* Open the User bit on the covering PML4 and PDPT entries. Sibling
     * PDEs without the User bit stay kernel-only, so only our page becomes
     * ring3-accessible. */
    p4_table[pml4_index] |= PDE_USER;
    p3_table[pdpt_index] |= PDE_USER;
    /* Physical frame stays identity mapped; set the full user leaf flags. */
    p2_table_0[pde_index] = (USER_REGION_BASE & ~0x1FFFFFUL) | USER_PDE_FLAGS;
    tlb_flush_all();
}

aios_status_t user_exec_run_first(void) {
    node_pipeline_snapshot_t *user_result;
    uint64_t program_size = (uint64_t)((uintptr_t)user_program_end -
                                       (uintptr_t)user_program_start);
    uint64_t enter_ns;
    uint64_t exit_ns;

    memset(&g_user_exec, 0, sizeof(g_user_exec));
    g_user_exec.attempted = true;

    if (program_size == 0 || program_size > 0x1000UL) {
        serial_write("[USER] ring3 exec ABORT: bad program size\n");
        return AIOS_ERR_INVAL;
    }

    /* 1. Promote the region to a user-accessible, executable page. */
    map_user_region();

    /* 2. Stage the program and clear the result buffer in the user page. */
    memcpy((void *)USER_REGION_BASE, user_program_start, (size_t)program_size);
    user_result = (node_pipeline_snapshot_t *)USER_BUFFER_ADDR;
    memset(user_result, 0, sizeof(*user_result));

    g_user_syscalls = 0;
    g_user_exit_code = 0;
    g_user_exited = 0;

    /* 3. Enter ring3. Returns here on the exit syscall. */
    enter_ns = kernel_time_monotonic_ns();
    user_mode_run(USER_REGION_BASE, USER_STACK_TOP);
    exit_ns = kernel_time_monotonic_ns();

    /* 4. Collect and verify the round trip. */
    g_user_exec.user_syscalls = (uint32_t)g_user_syscalls;
    g_user_exec.exit_code = g_user_exit_code;
    g_user_exec.duration_ns = exit_ns - enter_ns;
    g_user_exec.entered = g_user_syscalls >= 1;
    g_user_exec.returned = g_user_exited != 0;
    g_user_exec.observed_pipeline_max = user_result->max_pipelines;
    /* The user-issued SYS_PIPE_STATS must have written the real registry
     * capacity into the user buffer — proof ring3 actually reached the
     * kernel and got data back. */
    g_user_exec.syscall_ok =
        user_result->max_pipelines == NODE_PIPELINE_MAX;

    bool ok = g_user_exec.entered && g_user_exec.returned &&
              g_user_exec.syscall_ok && g_user_exec.exit_code == 42;

    serial_printf("[USER] ring3 exec %s entered=%u returned=%u syscalls=%u exit_code=%u pipe_max=%u dur_ns=%u\n",
        ok ? "PASS" : "FAIL",
        (uint64_t)g_user_exec.entered,
        (uint64_t)g_user_exec.returned,
        (uint64_t)g_user_exec.user_syscalls,
        g_user_exec.exit_code,
        (uint64_t)g_user_exec.observed_pipeline_max,
        g_user_exec.duration_ns);

    return ok ? AIOS_OK : AIOS_ERR_IO;
}

void user_exec_get_info(user_exec_info_t *out) {
    if (!out) {
        return;
    }
    *out = g_user_exec;
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
