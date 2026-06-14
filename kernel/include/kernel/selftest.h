/*
 * AIOS Kernel - Boot-Time Self Tests
 * AI-Native Operating System
 */

#ifndef _AIOS_SELFTEST_H
#define _AIOS_SELFTEST_H

#include <kernel/types.h>

typedef enum {
    BOOT_PERF_TIER_LOW = 0,
    BOOT_PERF_TIER_MID = 1,
    BOOT_PERF_TIER_HIGH = 2,
} boot_perf_tier_t;

typedef struct {
    uint32_t size_bytes;
    uint32_t line_size;
    uint16_t ways;
    uint32_t sets;
    bool present;
} cache_info_t;

typedef struct {
    uint64_t tsc_khz;
    bool invariant_tsc;
    uint64_t buffer_size;
    uint64_t iterations;
    uint64_t memset_cycles;
    uint64_t memcpy_cycles;
    uint64_t memmove_cycles;
    uint64_t memcpy_mib_per_sec;
    cache_info_t l1d;
    cache_info_t l2;
    cache_info_t l3;
    uint64_t l1_cycles_per_access_x100;
    uint64_t l2_cycles_per_access_x100;
    uint64_t l3_cycles_per_access_x100;
    uint64_t dram_cycles_per_access_x100;
    boot_perf_tier_t tier;
    bool passed;
} memory_selftest_result_t;

aios_status_t kernel_memory_selftest_run(memory_selftest_result_t *out);
void kernel_memory_selftest_print(const memory_selftest_result_t *result);
const memory_selftest_result_t *kernel_memory_selftest_last(void);
const char *kernel_boot_perf_tier_name(boot_perf_tier_t tier);

#endif /* _AIOS_SELFTEST_H */
