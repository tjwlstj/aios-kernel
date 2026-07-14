/*
 * AIOS Kernel - First Ring3 User Execution Slice
 * AI-Native Operating System
 */

#include <kernel/user_exec.h>
#include <kernel/user_access.h>
#include <kernel/elf.h>
#include <kernel/time.h>
#include <runtime/node_pipeline.h>
#include <drivers/serial.h>
#include <lib/string.h>

/* Ring3 entry / syscall path (kernel/core/user_entry.asm). */
extern void user_mode_run(uint64_t entry_rip, uint64_t user_stack_top);
/* Embedded static ELF64 demo image parsed by the ELF loader. */
extern uint8_t user_elf_image_start[];
extern uint8_t user_elf_image_end[];
extern volatile uint64_t g_user_syscalls;
extern volatile uint64_t g_user_exit_code;
extern volatile uint8_t  g_user_exited;

/* Boot page tables. Final user permission is the AND of the U/S bit across
 * every level, so the covering entry at p4/p3/p2 all get the User bit. */
extern uint64_t p4_table[];
extern uint64_t p3_table[];
extern uint64_t p2_table_0[];

/* Fixed bootstrap user region used by the original single-process slice.
 * It is identity mapped here; private physical backing is now proven by the
 * M3-b-3b1 address-space selftest but is not wired into this runner yet. */
#define USER_REGION_BASE   0x4000000UL           /* 64 MB */
#define USER_REGION_SIZE   0x200000UL            /* one 2MB huge page */
#define USER_BUFFER_ADDR   (USER_REGION_BASE + 0x1000UL)
#define USER_REJECT_ADDR   (USER_REGION_BASE + 0x1800UL)  /* rejection stash */
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
    uint64_t image_size = (uint64_t)((uintptr_t)user_elf_image_end -
                                     (uintptr_t)user_elf_image_start);
    elf_load_result_t elf = {0};
    aios_status_t elf_status;
    uint64_t enter_ns;
    uint64_t exit_ns;

    memset(&g_user_exec, 0, sizeof(g_user_exec));
    g_user_exec.attempted = true;

    /* 1. Promote the region to a user-accessible, executable page. */
    map_user_region();

    /* 2. Load the ELF image into the user region and stage the syscall
     *    scratch buffers. These are deliberate kernel writes to user pages,
     *    so bracket them with the SMAP fence (no-op when SMAP is off). The
     *    loader copies segments to their p_vaddr and zeroes any .bss tail. */
    user_result = (node_pipeline_snapshot_t *)USER_BUFFER_ADDR;
    user_access_fence_begin();
    elf_status = elf_load(user_elf_image_start, image_size,
                          USER_REGION_BASE, USER_REGION_SIZE, &elf);
    if (elf_status == AIOS_OK) {
        memset(user_result, 0, sizeof(*user_result));
        *(volatile int64_t *)USER_REJECT_ADDR = 0;
    }
    user_access_fence_end();

    if (elf_status != AIOS_OK) {
        serial_write("[USER] ring3 exec ABORT: ELF load failed\n");
        return elf_status;
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
    user_access_set_window(USER_REGION_BASE, USER_REGION_SIZE);
    enter_ns = kernel_time_monotonic_ns();
    user_mode_run(elf.entry, USER_STACK_TOP);
    exit_ns = kernel_time_monotonic_ns();
    user_access_clear_window();

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
    uint32_t observed_max = user_result->max_pipelines;
    int64_t reject_value = *(volatile int64_t *)USER_REJECT_ADDR;
    user_access_fence_end();

    g_user_exec.observed_pipeline_max = observed_max;
    g_user_exec.syscall_ok = observed_max == NODE_PIPELINE_MAX;
    g_user_exec.boundary_ok = reject_value == (int64_t)AIOS_ERR_PERM;

    bool ok = g_user_exec.elf_loaded && g_user_exec.entered &&
              g_user_exec.returned && g_user_exec.syscall_ok &&
              g_user_exec.boundary_ok && g_user_exec.exit_code == 42;

    serial_printf("[USER] ring3 exec %s elf_entry=%x segments=%u entered=%u returned=%u syscalls=%u boundary_ok=%u exit_code=%u pipe_max=%u dur_ns=%u\n",
        ok ? "PASS" : "FAIL",
        g_user_exec.elf_entry,
        (uint64_t)g_user_exec.elf_segments,
        (uint64_t)g_user_exec.entered,
        (uint64_t)g_user_exec.returned,
        (uint64_t)g_user_exec.user_syscalls,
        (uint64_t)g_user_exec.boundary_ok,
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
