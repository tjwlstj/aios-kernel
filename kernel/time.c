/*
 * AIOS Kernel - Time Source
 * AI-Native Operating System
 */

#include <kernel/time.h>
#include <drivers/vga.h>

#define PIT_FREQ_HZ      1193182U
#define PIT_CALIBRATE_US 10000U

static uint64_t g_tsc_khz = 0;
static bool g_invariant_tsc = false;
static uint64_t g_tsc_base = 0;

static inline uint64_t read_tsc(void) {
    uint32_t lo;
    uint32_t hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((uint64_t)hi << 32) | lo;
}

static inline void outb(uint16_t port, uint8_t val) {
    __asm__ volatile ("outb %0, %1" : : "a"(val), "Nd"(port));
}

static inline uint8_t inb(uint16_t port) {
    uint8_t ret;
    __asm__ volatile ("inb %1, %0" : "=a"(ret) : "Nd"(port));
    return ret;
}

static inline void cpuid_leaf(uint32_t leaf, uint32_t subleaf,
                              uint32_t *eax, uint32_t *ebx,
                              uint32_t *ecx, uint32_t *edx) {
    __asm__ volatile ("cpuid"
        : "=a"(*eax), "=b"(*ebx), "=c"(*ecx), "=d"(*edx)
        : "a"(leaf), "c"(subleaf));
}

static uint16_t pit_read_counter0(void) {
    outb(0x43, 0x00);
    uint8_t lo = inb(0x40);
    uint8_t hi = inb(0x40);
    return (uint16_t)(((uint16_t)hi << 8) | lo);
}

static uint64_t calibrate_tsc_khz(bool *invariant_tsc) {
    uint32_t eax = 0;
    uint32_t ebx = 0;
    uint32_t ecx = 0;
    uint32_t edx = 0;

    if (invariant_tsc) {
        *invariant_tsc = false;
    }

    cpuid_leaf(0x80000007U, 0, &eax, &ebx, &ecx, &edx);
    if (invariant_tsc && (edx & BIT(8))) {
        *invariant_tsc = true;
    }

    uint16_t pit_reload = (uint16_t)((PIT_FREQ_HZ * PIT_CALIBRATE_US) / 1000000U);
    if (pit_reload < 1024) {
        pit_reload = 1024;
    }

    outb(0x43, 0x30);
    outb(0x40, (uint8_t)(pit_reload & 0xFF));
    outb(0x40, (uint8_t)(pit_reload >> 8));

    uint64_t tsc_start = read_tsc();
    while (pit_read_counter0() > 32) {
    }
    uint64_t tsc_end = read_tsc();

    uint64_t elapsed_cycles = tsc_end - tsc_start;
    uint64_t elapsed_us = ((uint64_t)pit_reload * 1000000ULL) / PIT_FREQ_HZ;
    if (elapsed_us == 0) {
        elapsed_us = PIT_CALIBRATE_US;
    }

    return (elapsed_cycles * 1000ULL) / elapsed_us;
}

aios_status_t kernel_time_init(void) {
    g_tsc_khz = calibrate_tsc_khz(&g_invariant_tsc);
    g_tsc_base = read_tsc();

    kprintf("\n");
    kprintf("    Kernel Time Source initialized:\n");
    kprintf("    TSC frequency: %u kHz\n", g_tsc_khz);
    kprintf("    Invariant TSC: %u\n", (uint64_t)g_invariant_tsc);

    return AIOS_OK;
}

uint64_t kernel_time_monotonic_ns(void) {
    if (g_tsc_khz == 0) {
        return 0;
    }

    uint64_t delta_cycles = read_tsc() - g_tsc_base;
    return (delta_cycles * 1000000ULL) / g_tsc_khz;
}

uint64_t kernel_time_tsc_khz(void) {
    return g_tsc_khz;
}

bool kernel_time_invariant_tsc(void) {
    return g_invariant_tsc;
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
