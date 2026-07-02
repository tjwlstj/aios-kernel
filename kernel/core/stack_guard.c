/*
 * AIOS Kernel - Stack Smashing Protector Runtime
 * AI-Native Operating System
 */

#include <kernel/stack_guard.h>
#include <interrupt/idt.h>    /* kernel_panic */
#include <drivers/serial.h>

/* Static fallback keeps pre-init instrumented frames consistent. */
#define STACK_GUARD_STATIC_SEED 0xA105C0DE5AFE57ACULL

uintptr_t __stack_chk_guard = STACK_GUARD_STATIC_SEED;

static bool g_stack_guard_armed = false;

static inline uint64_t stack_guard_rdtsc(void) {
    uint32_t lo = 0;
    uint32_t hi = 0;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((uint64_t)hi << 32) | lo;
}

/*
 * Never instrument the function that swaps the live canary: its own
 * prologue would capture the old value and the epilogue would compare
 * against the new one.
 */
__attribute__((no_stack_protector))
aios_status_t stack_guard_init(void) {
    uint64_t seed = stack_guard_rdtsc();

    /* Golden-ratio mix to spread low TSC entropy across all bits. */
    seed ^= STACK_GUARD_STATIC_SEED;
    seed *= 0x9E3779B97F4A7C15ULL;
    seed ^= seed >> 29;

    /*
     * Keep the lowest byte NUL so runaway string writes terminate before
     * reproducing the canary (same convention as Linux).
     */
    seed &= ~0xFFULL;
    if (seed == 0) {
        seed = STACK_GUARD_STATIC_SEED & ~0xFFULL;
    }

    __stack_chk_guard = (uintptr_t)seed;
    g_stack_guard_armed = true;
    serial_write("[SEC] Stack canary armed (strong, global guard)\n");
    return AIOS_OK;
}

bool stack_guard_armed(void) {
    return g_stack_guard_armed;
}

__attribute__((no_stack_protector))
NORETURN void __stack_chk_fail(void) {
    kernel_panic("Stack smashing detected - kernel stack canary corrupted");
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
