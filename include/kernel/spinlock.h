/*
 * AIOS Kernel - Spinlock Primitive
 * AI-Native Operating System
 *
 * Minimal non-recursive spinlock for shared registry protection.
 * This is a bootstrap-safe foundation, not a full scheduler-aware lock layer.
 */

#ifndef _AIOS_SPINLOCK_H
#define _AIOS_SPINLOCK_H

#include <kernel/types.h>

typedef struct {
    volatile uint32_t state;
} spinlock_t;

#define SPINLOCK_INIT { 0 }

AIOS_STATIC_ASSERT(sizeof(spinlock_t) == sizeof(uint32_t),
    "spinlock_t must stay a compact atomic word");

static inline void spinlock_cpu_relax(void) {
    __asm__ volatile ("pause" ::: "memory");
}

static inline void spinlock_lock(spinlock_t *lock) {
    while (__atomic_test_and_set(&lock->state, __ATOMIC_ACQUIRE)) {
        while (__atomic_load_n(&lock->state, __ATOMIC_RELAXED)) {
            spinlock_cpu_relax();
        }
    }
}

static inline void spinlock_unlock(spinlock_t *lock) {
    __atomic_clear(&lock->state, __ATOMIC_RELEASE);
}

static inline uint64_t spinlock_irqsave(spinlock_t *lock) {
    uint64_t flags = 0;
    __asm__ volatile ("pushfq; popq %0; cli" : "=r"(flags) :: "memory");
    spinlock_lock(lock);
    return flags;
}

static inline void spinlock_irqrestore(spinlock_t *lock, uint64_t flags) {
    spinlock_unlock(lock);
    if ((flags & BIT(9)) != 0) {
        __asm__ volatile ("sti" ::: "memory");
    }
}

static inline bool spinlock_is_locked(const spinlock_t *lock) {
    return __atomic_load_n(&lock->state, __ATOMIC_RELAXED) != 0;
}

#endif /* _AIOS_SPINLOCK_H */
