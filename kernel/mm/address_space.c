/*
 * AIOS Kernel - Minimal Address-Space Switching Foundation
 *
 * M3-b-3a proves a top-level CR3 round trip. M3-b-3b1 adds two bounded,
 * statically-backed user mappings: each owns a private PML4, PDPT, and first
 * page directory, while all other kernel mappings remain shared. M3-b-3b2a
 * lends one slot to the synchronous ring3 runner with guarded rollback. This
 * remains a bootstrap mechanism, not a general physical-memory allocator.
 */

#include <mm/address_space.h>
#include <kernel/cpu_sec.h>
#include <kernel/user_layout.h>
#include <kernel/user_access.h>
#include <drivers/serial.h>
#include <lib/string.h>

#define CR3_ADDRESS_MASK 0x000FFFFFFFFFF000UL
#define IDENTITY_MAP_LIMIT 0x100000000ULL
#define PAGE_TABLE_ENTRIES 512U
#define USER_CANARY_ADDR (AIOS_BOOTSTRAP_USER_BASE + PAGE_SIZE)
#define ADDRESS_SPACE_NO_SLOT 0xFFFFFFFFU

#define PAGE_PRESENT  0x001UL
#define PAGE_WRITABLE 0x002UL
#define PAGE_USER     0x004UL
#define PAGE_HUGE     0x080UL
#define PAGE_NX       0x8000000000000000ULL
#define USER_TABLE_FLAGS (PAGE_PRESENT | PAGE_WRITABLE | PAGE_USER)
#define USER_LEAF_FLAGS  (USER_TABLE_FLAGS | PAGE_HUGE)

#define USER_CANARY_A 0xA105A11CE0000001ULL
#define USER_CANARY_B 0xA105A11CE0000002ULL

extern uint64_t p4_table[];
extern uint64_t p3_table[];
extern uint64_t p2_table_0[];

static uint64_t g_selftest_pml4[512] __attribute__((aligned(PAGE_SIZE)));
static uint64_t g_user_pml4[ADDRESS_SPACE_BOOTSTRAP_USER_SLOT_COUNT][PAGE_TABLE_ENTRIES]
    ALIGNED(PAGE_SIZE);
static uint64_t g_user_pdpt[ADDRESS_SPACE_BOOTSTRAP_USER_SLOT_COUNT][PAGE_TABLE_ENTRIES]
    ALIGNED(PAGE_SIZE);
static uint64_t g_user_pd0[ADDRESS_SPACE_BOOTSTRAP_USER_SLOT_COUNT][PAGE_TABLE_ENTRIES]
    ALIGNED(PAGE_SIZE);
static uint8_t g_user_backing[ADDRESS_SPACE_BOOTSTRAP_USER_SLOT_COUNT]
                             [AIOS_BOOTSTRAP_USER_SIZE]
    ALIGNED(HUGE_PAGE_SIZE);
static bool g_user_spaces_ready;
static bool g_user_slot_executable[ADDRESS_SPACE_BOOTSTRAP_USER_SLOT_COUNT];
static uint32_t g_active_slot = ADDRESS_SPACE_NO_SLOT;
static address_space_stats_t g_stats;

static inline uint64_t read_cr3(void) {
    uint64_t value;
    __asm__ volatile ("mov %%cr3, %0" : "=r"(value) : : "memory");
    return value;
}

static inline uint64_t read_rflags(void) {
    uint64_t flags;
    __asm__ volatile ("pushfq; popq %0" : "=r"(flags) : : "memory");
    return flags;
}

static inline void write_cr3(uint64_t value) {
    __asm__ volatile ("mov %0, %%cr3" : : "r"(value) : "memory");
}

static void switch_cr3_record(uint64_t next_cr3) {
    g_stats.last_from_cr3 = read_cr3() & CR3_ADDRESS_MASK;
    g_stats.last_to_cr3 = next_cr3 & CR3_ADDRESS_MASK;
    write_cr3(next_cr3);
    g_stats.switches++;
}

static inline uint64_t irq_save_disable(void) {
    uint64_t flags;
    __asm__ volatile ("pushfq; popq %0; cli" : "=r"(flags) : : "memory");
    return flags;
}

static inline void irq_restore(uint64_t flags) {
    if ((flags & (1UL << 9)) != 0) {
        __asm__ volatile ("sti" : : : "memory");
    } else {
        __asm__ volatile ("cli" : : : "memory");
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
    switch_cr3_record(clone_cr3);

    clone_active = ((read_cr3() & CR3_ADDRESS_MASK) ==
                    (clone_cr3 & CR3_ADDRESS_MASK));
    kernel_mapping_live = (live_marker == 0xA105C3A55A3C501AUL) &&
                          (g_selftest_pml4[0] == p4_table[0]);

    /* Roll back before evaluating the result so every failure path returns
     * to the boot address space. */
    switch_cr3_record(boot_cr3);
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
        g_stats.switches, clone_cr3 & CR3_ADDRESS_MASK);
    return AIOS_OK;
}

static bool identity_range_valid(uint64_t base, uint64_t size,
                                 uint64_t alignment) {
    return base != 0 && (base & (alignment - 1UL)) == 0 &&
           size <= IDENTITY_MAP_LIMIT &&
           base <= IDENTITY_MAP_LIMIT - size;
}

static aios_status_t bootstrap_user_leaf_flags(bool executable,
                                               uint64_t *flags_out) {
    cpu_sec_info_t sec;
    uint64_t flags = USER_LEAF_FLAGS;

    if (!flags_out || cpu_security_info(&sec) != AIOS_OK) {
        return AIOS_ERR_IO;
    }
    if (!executable && sec.nx_enabled) {
        flags |= PAGE_NX;
    }
    *flags_out = flags;
    return AIOS_OK;
}

static aios_status_t build_private_user_spaces(void) {
    const uint32_t pml4_index =
        (uint32_t)((AIOS_BOOTSTRAP_USER_BASE >> 39) & 0x1FFU);
    const uint32_t pdpt_index =
        (uint32_t)((AIOS_BOOTSTRAP_USER_BASE >> 30) & 0x1FFU);
    const uint32_t pde_index =
        (uint32_t)((AIOS_BOOTSTRAP_USER_BASE >> 21) & 0x1FFU);
    uint64_t user_leaf_flags;

    if (g_user_spaces_ready) {
        return AIOS_OK;
    }
    /* Bootstrap leaves default to data-only NX. A synchronous ELF runner
     * must explicitly request the temporary executable policy and seal the
     * inactive slot again after restoring the boot CR3. */
    if (bootstrap_user_leaf_flags(false, &user_leaf_flags) != AIOS_OK) {
        return AIOS_ERR_IO;
    }

    for (uint32_t i = 0;
         i < ADDRESS_SPACE_BOOTSTRAP_USER_SLOT_COUNT; i++) {
        const uint64_t pml4_phys = (uint64_t)(uintptr_t)g_user_pml4[i];
        const uint64_t pdpt_phys = (uint64_t)(uintptr_t)g_user_pdpt[i];
        const uint64_t pd0_phys = (uint64_t)(uintptr_t)g_user_pd0[i];
        const uint64_t backing_phys = (uint64_t)(uintptr_t)g_user_backing[i];

        if (!identity_range_valid(pml4_phys, PAGE_SIZE, PAGE_SIZE) ||
            !identity_range_valid(pdpt_phys, PAGE_SIZE, PAGE_SIZE) ||
            !identity_range_valid(pd0_phys, PAGE_SIZE, PAGE_SIZE) ||
            !identity_range_valid(backing_phys, AIOS_BOOTSTRAP_USER_SIZE,
                                  HUGE_PAGE_SIZE)) {
            return AIOS_ERR_INVAL;
        }

        memcpy(g_user_pml4[i], p4_table, PAGE_SIZE);
        memcpy(g_user_pdpt[i], p3_table, PAGE_SIZE);
        memcpy(g_user_pd0[i], p2_table_0, PAGE_SIZE);

        /* Only the 64MiB user branch is private. Sibling kernel leaves keep
         * their cloned supervisor-only flags, while higher 1GiB branches
         * continue sharing the boot page directories. */
        g_user_pd0[i][pde_index] = backing_phys | user_leaf_flags;
        g_user_pdpt[i][pdpt_index] = pd0_phys | USER_TABLE_FLAGS;
        g_user_pml4[i][pml4_index] = pdpt_phys | USER_TABLE_FLAGS;
        g_user_slot_executable[i] = false;
    }

    if ((uint64_t)(uintptr_t)g_user_backing[0] ==
        (uint64_t)(uintptr_t)g_user_backing[1]) {
        return AIOS_ERR_IO;
    }
    g_user_spaces_ready = true;
    return AIOS_OK;
}

aios_status_t address_space_user_isolation_selftest(void) {
    cpu_sec_info_t sec;
    const uint64_t boot_cr3 = read_cr3();
    volatile uint64_t *const user_canary =
        (volatile uint64_t *)(uintptr_t)USER_CANARY_ADDR;
    volatile uint64_t *const physical_a =
        (volatile uint64_t *)(void *)(g_user_backing[0] + PAGE_SIZE);
    volatile uint64_t *const physical_b =
        (volatile uint64_t *)(void *)(g_user_backing[1] + PAGE_SIZE);
    uint64_t observed_a;
    uint64_t initial_b;
    uint64_t observed_b;
    uint64_t observed_a_again;
    uint64_t flags;
    bool boot_restored;
    bool nx_preserved;
    aios_status_t status;

    g_stats.isolation_checks = 0;
    g_stats.user_leaf_slots = 0;
    g_stats.user_leaf_isolation_passed = false;

    status = build_private_user_spaces();
    if (status != AIOS_OK) {
        serial_write("[MM] user leaf isolation selftest FAIL setup\n");
        return status;
    }

    *physical_a = 0;
    *physical_b = 0;
    if (cpu_security_info(&sec) != AIOS_OK) {
        serial_write("[MM] user leaf isolation selftest FAIL security\n");
        return AIOS_ERR_IO;
    }
    nx_preserved = !sec.nx_enabled ||
        ((g_user_pd0[0][AIOS_BOOTSTRAP_USER_BASE >> 21] & PAGE_NX) != 0 &&
         (g_user_pd0[1][AIOS_BOOTSTRAP_USER_BASE >> 21] & PAGE_NX) != 0);
    flags = irq_save_disable();

    switch_cr3_record((uint64_t)(uintptr_t)g_user_pml4[0]);
    user_access_fence_begin();
    *user_canary = USER_CANARY_A;
    observed_a = *user_canary;
    user_access_fence_end();
    g_stats.isolation_checks++;

    switch_cr3_record((uint64_t)(uintptr_t)g_user_pml4[1]);
    user_access_fence_begin();
    initial_b = *user_canary;
    *user_canary = USER_CANARY_B;
    observed_b = *user_canary;
    user_access_fence_end();
    g_stats.isolation_checks += 2;

    switch_cr3_record((uint64_t)(uintptr_t)g_user_pml4[0]);
    user_access_fence_begin();
    observed_a_again = *user_canary;
    user_access_fence_end();
    g_stats.isolation_checks++;

    /* Close the SMAP fence before every CR3 transition and always restore
     * the raw boot CR3 before interpreting the result. */
    switch_cr3_record(boot_cr3);
    boot_restored = ((read_cr3() & CR3_ADDRESS_MASK) ==
                     (boot_cr3 & CR3_ADDRESS_MASK));
    irq_restore(flags);

    g_stats.user_leaf_slots = ADDRESS_SPACE_BOOTSTRAP_USER_SLOT_COUNT;
    g_stats.user_leaf_isolation_passed =
        observed_a == USER_CANARY_A &&
        initial_b == 0 &&
        observed_b == USER_CANARY_B &&
        observed_a_again == USER_CANARY_A &&
        *physical_a == USER_CANARY_A &&
        *physical_b == USER_CANARY_B &&
        nx_preserved &&
        boot_restored;

    if (!g_stats.user_leaf_isolation_passed) {
        serial_printf("[MM] user leaf isolation selftest FAIL a=%x b0=%x b=%x a2=%x nx=%u restored=%u\n",
            observed_a, initial_b, observed_b, observed_a_again,
            nx_preserved ? 1ULL : 0ULL,
            boot_restored ? 1ULL : 0ULL);
        return AIOS_ERR_IO;
    }

    serial_printf("[MM] user leaf isolation selftest PASS slots=%u checks=%u nx=%u restored=1\n",
        (uint64_t)g_stats.user_leaf_slots, g_stats.isolation_checks,
        sec.nx_enabled ? 1ULL : 0ULL);
    return AIOS_OK;
}

aios_status_t address_space_bootstrap_slot_prepare(
    uint32_t slot, bool executable, address_space_bootstrap_slot_t *out) {
    const uint32_t pde_index =
        (uint32_t)((AIOS_BOOTSTRAP_USER_BASE >> 21) & 0x1FFU);
    uint64_t leaf_flags;
    aios_status_t status;

    if (!out || slot >= ADDRESS_SPACE_BOOTSTRAP_USER_SLOT_COUNT) {
        return AIOS_ERR_INVAL;
    }
    if (g_active_slot != ADDRESS_SPACE_NO_SLOT) {
        return AIOS_ERR_BUSY;
    }

    status = build_private_user_spaces();
    if (status != AIOS_OK) {
        return status;
    }
    status = bootstrap_user_leaf_flags(executable, &leaf_flags);
    if (status != AIOS_OK) {
        return status;
    }

    memset(g_user_backing[slot], 0, AIOS_BOOTSTRAP_USER_SIZE);
    g_user_pd0[slot][pde_index] =
        (uint64_t)(uintptr_t)g_user_backing[slot] | leaf_flags;
    g_user_slot_executable[slot] = executable;

    out->slot = slot;
    out->cr3 = (uint64_t)(uintptr_t)g_user_pml4[slot];
    out->user_base = AIOS_BOOTSTRAP_USER_BASE;
    out->user_size = AIOS_BOOTSTRAP_USER_SIZE;
    out->backing_phys = (uint64_t)(uintptr_t)g_user_backing[slot];
    out->ready = true;
    out->executable = executable;
    return AIOS_OK;
}

aios_status_t address_space_activate(
    const address_space_bootstrap_slot_t *space,
    address_space_guard_t *guard) {
    uint64_t previous_cr3;
    uint64_t previous_flags;
    bool active;

    if (!space || !guard || !g_user_spaces_ready || !space->ready ||
        space->slot >= ADDRESS_SPACE_BOOTSTRAP_USER_SLOT_COUNT ||
        space->cr3 != (uint64_t)(uintptr_t)g_user_pml4[space->slot] ||
        space->user_base != AIOS_BOOTSTRAP_USER_BASE ||
        space->user_size != AIOS_BOOTSTRAP_USER_SIZE ||
        space->backing_phys !=
            (uint64_t)(uintptr_t)g_user_backing[space->slot] ||
        space->executable != g_user_slot_executable[space->slot]) {
        return AIOS_ERR_INVAL;
    }
    if (guard->active || g_active_slot != ADDRESS_SPACE_NO_SLOT) {
        return AIOS_ERR_BUSY;
    }

    previous_flags = irq_save_disable();
    previous_cr3 = read_cr3();
    guard->previous_cr3 = previous_cr3;
    guard->previous_flags = previous_flags;
    guard->active_slot = space->slot;
    guard->cr3_restored = false;
    guard->if_restored = false;
    guard->active = true;
    g_active_slot = space->slot;

    switch_cr3_record(space->cr3);
    active = read_cr3() == space->cr3;
    if (!active) {
        switch_cr3_record(previous_cr3);
        guard->cr3_restored = read_cr3() == previous_cr3;
        if (guard->cr3_restored) {
            g_active_slot = ADDRESS_SPACE_NO_SLOT;
            guard->active = false;
            irq_restore(previous_flags);
            guard->if_restored =
                ((read_rflags() ^ previous_flags) & BIT(9)) == 0;
        }
        /* If rollback cannot be proven, conservatively retain the guard and
         * IF=0. That prevents callers from sealing a possibly active slot. */
        return AIOS_ERR_IO;
    }
    return AIOS_OK;
}

aios_status_t address_space_restore(address_space_guard_t *guard) {
    if (!guard || !guard->active ||
        guard->active_slot != g_active_slot) {
        return AIOS_ERR_INVAL;
    }

    switch_cr3_record(guard->previous_cr3);
    guard->cr3_restored = read_cr3() == guard->previous_cr3;
    if (!guard->cr3_restored) {
        /* The current address space is uncertain. Keep the guard live and
         * interrupts disabled so the slot cannot be scrubbed underneath it. */
        return AIOS_ERR_IO;
    }

    g_active_slot = ADDRESS_SPACE_NO_SLOT;
    guard->active = false;
    irq_restore(guard->previous_flags);
    guard->if_restored =
        ((read_rflags() ^ guard->previous_flags) & BIT(9)) == 0;
    return guard->if_restored ? AIOS_OK : AIOS_ERR_IO;
}

aios_status_t address_space_bootstrap_slot_seal(uint32_t slot,
                                                bool *nx_enforced) {
    const uint32_t pde_index =
        (uint32_t)((AIOS_BOOTSTRAP_USER_BASE >> 21) & 0x1FFU);
    cpu_sec_info_t sec;
    uint64_t leaf_flags;
    aios_status_t status;

    if (nx_enforced) {
        *nx_enforced = false;
    }
    if (slot >= ADDRESS_SPACE_BOOTSTRAP_USER_SLOT_COUNT) {
        return AIOS_ERR_INVAL;
    }
    if (g_active_slot != ADDRESS_SPACE_NO_SLOT) {
        return AIOS_ERR_BUSY;
    }
    status = build_private_user_spaces();
    if (status != AIOS_OK || cpu_security_info(&sec) != AIOS_OK) {
        return AIOS_ERR_IO;
    }
    status = bootstrap_user_leaf_flags(false, &leaf_flags);
    if (status != AIOS_OK) {
        return status;
    }

    g_user_pd0[slot][pde_index] =
        (uint64_t)(uintptr_t)g_user_backing[slot] | leaf_flags;
    g_user_slot_executable[slot] = false;
    memset(g_user_backing[slot], 0, AIOS_BOOTSTRAP_USER_SIZE);

    if (sec.nx_enabled &&
        (g_user_pd0[slot][pde_index] & PAGE_NX) == 0) {
        return AIOS_ERR_IO;
    }
    if (nx_enforced) {
        *nx_enforced = sec.nx_enabled &&
            (g_user_pd0[slot][pde_index] & PAGE_NX) != 0;
    }
    return AIOS_OK;
}

void address_space_get_stats(address_space_stats_t *out) {
    if (out) {
        *out = g_stats;
    }
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
