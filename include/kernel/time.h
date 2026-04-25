/*
 * AIOS Kernel - Time Source
 * AI-Native Operating System
 */

#ifndef _AIOS_TIME_H
#define _AIOS_TIME_H

#include <kernel/types.h>

#define KERNEL_TIMER_DEFAULT_HZ 100U
#define KERNEL_TIMER_IRQ_VECTOR 32U

aios_status_t kernel_time_init(void);
uint64_t kernel_time_monotonic_ns(void);
uint64_t kernel_time_tsc_khz(void);
bool kernel_time_invariant_tsc(void);

aios_status_t kernel_timer_irq_init(uint32_t hz);
void kernel_timer_irq_handler(void);
uint64_t kernel_timer_irq_ticks(void);
uint32_t kernel_timer_irq_hz(void);

#endif /* _AIOS_TIME_H */
