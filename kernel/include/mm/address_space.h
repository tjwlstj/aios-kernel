#ifndef AIOS_MM_ADDRESS_SPACE_H
#define AIOS_MM_ADDRESS_SPACE_H

#include <kernel/types.h>

typedef struct address_space_stats {
    uint64_t switches;
    uint64_t last_from_cr3;
    uint64_t last_to_cr3;
    bool selftest_passed;
} address_space_stats_t;

/* M3-b-3 foundation: clone the boot PML4 and prove a bounded CR3 round trip. */
aios_status_t address_space_selftest(void);
void address_space_get_stats(address_space_stats_t *out);

#endif /* AIOS_MM_ADDRESS_SPACE_H */
