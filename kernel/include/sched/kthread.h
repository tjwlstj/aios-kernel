/*
 * AIOS Kernel - Cooperative Kernel Threads
 * AI-Native Operating System
 *
 * Minimal kernel-thread context switching, distinct from the AI workload
 * scheduler (sched/ai_sched.c) which only accounts vruntime. A kthread owns
 * a stack and a saved stack pointer; kthread_switch swaps callee-saved
 * registers and rsp between two threads. This is the M3-b foundation for
 * preemptive multitasking.
 */

#ifndef _AIOS_SCHED_KTHREAD_H
#define _AIOS_SCHED_KTHREAD_H

#include <kernel/types.h>

typedef struct kthread {
    uint64_t    rsp;    /* saved stack pointer (offset 0 — used by asm) */
    uint32_t    id;
    const char *name;
} kthread_t;

/*
 * Prepare a thread to start at `entry` on the given stack. `stack_top` is
 * the high end of the stack region; the initial frame is laid out so the
 * first kthread_switch into it returns straight to `entry`.
 */
void kthread_init(kthread_t *t, uint32_t id, const char *name,
                  void (*entry)(void), void *stack_top);

/*
 * Save the current context into *save_rsp and resume the context at
 * load_rsp. Defined in kthread_switch.asm.
 */
extern void kthread_switch(uint64_t *save_rsp, uint64_t load_rsp);

/* Cumulative context switches performed (telemetry). */
uint64_t kthread_switch_count(void);

/* Boot-time cooperative ping-pong check of the switch primitive. */
aios_status_t kthread_selftest(void);

/*
 * Called from the timer IRQ handler (after EOI). When preemption is armed,
 * round-robins between the runnable kernel threads via kthread_switch.
 * A no-op (cheap early return) when not armed, so it is safe to call on
 * every tick in normal operation.
 */
void kthread_preempt_tick(void);

/*
 * Boot-time check that the timer preempts two kernel threads that never
 * voluntarily yield: both making progress proves the switch was forced by
 * the timer, not cooperative.
 */
aios_status_t kthread_preempt_selftest(void);

/* Timer ticks observed while preemption was armed (telemetry). */
uint64_t kthread_preempt_tick_count(void);

#endif /* _AIOS_SCHED_KTHREAD_H */
