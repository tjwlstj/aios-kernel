#ifndef AIOS_MM_ADDRESS_SPACE_H
#define AIOS_MM_ADDRESS_SPACE_H

#include <kernel/types.h>

#define ADDRESS_SPACE_BOOTSTRAP_USER_SLOT_COUNT 2U

/* A bounded descriptor for one statically-backed bootstrap user mapping.
 * This is deliberately not a general VMM address-space object. */
typedef struct address_space_bootstrap_slot {
    uint32_t slot;
    uint64_t cr3;
    uint64_t user_base;
    uint64_t user_size;
    uint64_t backing_phys;
    bool ready;
    bool executable;
} address_space_bootstrap_slot_t;

/* Guard for a synchronous private-CR3 residency interval. The raw previous
 * CR3 and caller RFLAGS are restored even when the caller entered with IF=0. */
typedef struct address_space_guard {
    uint64_t previous_cr3;
    uint64_t previous_flags;
    uint32_t active_slot;
    bool cr3_restored;
    bool if_restored;
    bool active;
} address_space_guard_t;

typedef struct address_space_stats {
    uint64_t switches;
    uint64_t last_from_cr3;
    uint64_t last_to_cr3;
    uint64_t isolation_checks;
    uint32_t user_leaf_slots;
    bool selftest_passed;
    bool user_leaf_isolation_passed;
} address_space_stats_t;

/* M3-b-3 foundation: clone the boot PML4 and prove a bounded CR3 round trip. */
aios_status_t address_space_selftest(void);

/* M3-b-3b1: prove that two private user leaf mappings isolate the same VA. */
aios_status_t address_space_user_isolation_selftest(void);

/* Prepare one static slot for a synchronous runner. Preparation zeros its
 * backing and applies either executable or NX data-leaf policy. */
aios_status_t address_space_bootstrap_slot_prepare(
    uint32_t slot, bool executable, address_space_bootstrap_slot_t *out);

/* Activate a prepared slot and later restore the exact previous CR3/IF state.
 * Guards are non-nestable and must be zero-initialized by the caller. */
aios_status_t address_space_activate(
    const address_space_bootstrap_slot_t *space,
    address_space_guard_t *guard);
aios_status_t address_space_restore(address_space_guard_t *guard);

/* Reset an inactive slot's executable policy and scrub its backing. The
 * output distinguishes a completed software seal from hardware NX support. */
aios_status_t address_space_bootstrap_slot_seal(uint32_t slot,
                                                bool *nx_enforced);

void address_space_get_stats(address_space_stats_t *out);

#endif /* AIOS_MM_ADDRESS_SPACE_H */
