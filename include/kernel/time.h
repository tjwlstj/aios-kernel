/*
 * AIOS Kernel - Time Source
 * AI-Native Operating System
 */

#ifndef _AIOS_TIME_H
#define _AIOS_TIME_H

#include <kernel/types.h>

aios_status_t kernel_time_init(void);
uint64_t kernel_time_monotonic_ns(void);
uint64_t kernel_time_tsc_khz(void);
bool kernel_time_invariant_tsc(void);

#endif /* _AIOS_TIME_H */
