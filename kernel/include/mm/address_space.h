#ifndef AIOS_MM_ADDRESS_SPACE_H
#define AIOS_MM_ADDRESS_SPACE_H

#include <kernel/types.h>

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
void address_space_get_stats(address_space_stats_t *out);

#endif /* AIOS_MM_ADDRESS_SPACE_H */
