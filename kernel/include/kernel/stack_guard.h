/*
 * AIOS Kernel - Stack Smashing Protector Runtime
 * AI-Native Operating System
 *
 * Backs the compiler's -fstack-protector-strong instrumentation with a
 * global canary (-mstack-protector-guard=global). The canary is seeded
 * from the TSC once, early in boot, before any instrumented function
 * that could return has been entered.
 */

#ifndef _AIOS_STACK_GUARD_H
#define _AIOS_STACK_GUARD_H

#include <kernel/types.h>

/* Compiler-referenced canary symbol (do not rename). */
extern uintptr_t __stack_chk_guard;

/*
 * Re-seed the canary from boot-time entropy. Must only be called from a
 * frame that never returns (kernel_main), because live instrumented frames
 * captured the previous canary value at function entry.
 */
aios_status_t stack_guard_init(void);

/* True once the canary has been re-seeded from boot-time entropy. */
bool stack_guard_armed(void);

/* Compiler-invoked failure hook (do not rename). */
NORETURN void __stack_chk_fail(void);

#endif /* _AIOS_STACK_GUARD_H */
