/*
 * AIOS Kernel - Minimal Address-Space Switching Foundation
 *
 * This first M3-b-3 slice deliberately clones only the top-level boot PML4.
 * Lower-level kernel mappings remain shared.  It proves that CR3 can switch
 * away from and back to the boot address space without losing the live kernel
 * mapping; per-process user page tables and ring3 scheduling remain follow-up.
 */

#include <mm/address_space.h>
#include <drivers/serial.h>
#include <lib/string.h>

#define CR3_ADDRESS_MASK 0x000FFFFFFFFFF000UL

extern uint64_t p4_table[];

static uint64_t g_selftest_pml4[512] __attribute__((aligned(PAGE_SIZE)));
static address_space_stats_t g_stats;

static inline uint64_t read_cr3(void) {
    uint64_t value;
    __asm__ volatile ("mov %%cr3, %0" : "=r"(value) : : "memory");
    return value;
}

static inline void write_cr3(uint64_t value) {
    __asm__ volatile ("mov %0, %%cr3" : : "r"(value) : "memory");
}

static inline uint64_t irq_save_disable(void) {
    uint64_t flags;
    __asm__ volatile ("pushfq; popq %0; cli" : "=r"(flags) : : "memory");
    return flags;
}

static inline void irq_restore(uint64_t flags) {
    if ((flags & (1UL << 9)) != 0) {
        __asm__ volatile ("sti" : : : "memory");
    }
}

aios_status_t address_space_selftest(void) {
    const uint64_t boot_cr3 = read_cr3();
    const uint64_t clone_cr3 = (uint64_t)(uintptr_t)g_selftest_pml4;
    const volatile uint64_t live_marker = 0xA105C3A55A3C501AUL;
    uint64_t flags;
    bool clone_active;
    bool kernel_mapping_live;
    bool boot_restored;

    memset(&g_stats, 0, sizeof(g_stats));
    memcpy(g_selftest_pml4, p4_table, PAGE_SIZE);

    if ((clone_cr3 & (PAGE_SIZE - 1UL)) != 0 ||
        (boot_cr3 & CR3_ADDRESS_MASK) == (clone_cr3 & CR3_ADDRESS_MASK)) {
        serial_write("[MM] address space selftest FAIL setup\n");
        return AIOS_ERR_INVAL;
    }

    flags = irq_save_disable();
    g_stats.last_from_cr3 = boot_cr3 & CR3_ADDRESS_MASK;
    g_stats.last_to_cr3 = clone_cr3 & CR3_ADDRESS_MASK;
    write_cr3(clone_cr3);
    g_stats.switches++;

    clone_active = ((read_cr3() & CR3_ADDRESS_MASK) ==
                    (clone_cr3 & CR3_ADDRESS_MASK));
    kernel_mapping_live = (live_marker == 0xA105C3A55A3C501AUL) &&
                          (g_selftest_pml4[0] == p4_table[0]);

    /* Roll back before evaluating the result so every failure path returns
     * to the boot address space. */
    write_cr3(boot_cr3);
    g_stats.switches++;
    boot_restored = ((read_cr3() & CR3_ADDRESS_MASK) ==
                     (boot_cr3 & CR3_ADDRESS_MASK));
    irq_restore(flags);

    g_stats.selftest_passed = clone_active && kernel_mapping_live && boot_restored;
    if (!g_stats.selftest_passed) {
        serial_printf("[MM] address space selftest FAIL clone=%u mapping=%u restored=%u\n",
            clone_active ? 1ULL : 0ULL,
            kernel_mapping_live ? 1ULL : 0ULL,
            boot_restored ? 1ULL : 0ULL);
        return AIOS_ERR_IO;
    }

    serial_printf("[MM] address space selftest PASS switches=%u clone_cr3=%x restored=1\n",
        g_stats.switches, g_stats.last_to_cr3);
    return AIOS_OK;
}

void address_space_get_stats(address_space_stats_t *out) {
    if (out) {
        *out = g_stats;
    }
}
