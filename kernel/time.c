/*
 * AIOS Kernel - Time Source
 * AI-Native Operating System
 */

#include <kernel/time.h>
#include <drivers/vga.h>
#include <drivers/serial.h>
#include <sched/ai_sched.h>

#define PIT_FREQ_HZ      1193182U
#define PIT_CALIBRATE_US 10000U
#define PIT_CHANNEL0     0x40
#define PIT_COMMAND      0x43
#define PIC1_COMMAND     0x20
#define PIC1_DATA        0x21
#define PIC2_COMMAND     0xA0
#define PIC2_DATA        0xA1
#define PIC_ICW1_INIT    0x11
#define PIC_ICW4_8086    0x01
#define TIMER_READY_NS   (50ULL * 1000ULL * 1000ULL)

static uint64_t g_tsc_khz = 0;
static bool g_invariant_tsc = false;
static uint64_t g_tsc_base = 0;
static volatile uint64_t g_timer_irq_ticks = 0;
static uint32_t g_timer_irq_hz = 0;

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

static inline void io_wait(void) {
    outb(0x80, 0);
}

static inline void irq_enable(void) {
    __asm__ volatile ("sti" ::: "memory");
}

static inline void irq_disable(void) {
    __asm__ volatile ("cli" ::: "memory");
}

static inline void cpuid_leaf(uint32_t leaf, uint32_t subleaf,
                              uint32_t *eax, uint32_t *ebx,
                              uint32_t *ecx, uint32_t *edx) {
    __asm__ volatile ("cpuid"
        : "=a"(*eax), "=b"(*ebx), "=c"(*ecx), "=d"(*edx)
        : "a"(leaf), "c"(subleaf));
}

static uint16_t pit_read_counter0(void) {
    outb(PIT_COMMAND, 0x00);
    uint8_t lo = inb(PIT_CHANNEL0);
    uint8_t hi = inb(PIT_CHANNEL0);
    return (uint16_t)(((uint16_t)hi << 8) | lo);
}

static void pic_remap_timer_only(void) {
    outb(PIC1_COMMAND, PIC_ICW1_INIT);
    io_wait();
    outb(PIC2_COMMAND, PIC_ICW1_INIT);
    io_wait();

    outb(PIC1_DATA, 0x20);
    io_wait();
    outb(PIC2_DATA, 0x28);
    io_wait();

    outb(PIC1_DATA, 0x04);
    io_wait();
    outb(PIC2_DATA, 0x02);
    io_wait();

    outb(PIC1_DATA, PIC_ICW4_8086);
    io_wait();
    outb(PIC2_DATA, PIC_ICW4_8086);
    io_wait();

    outb(PIC1_DATA, 0xFE);
    outb(PIC2_DATA, 0xFF);
}

static void pit_program_periodic(uint32_t hz) {
    uint32_t divisor = PIT_FREQ_HZ / hz;
    if (divisor == 0) {
        divisor = 1;
    } else if (divisor > 0xFFFF) {
        divisor = 0xFFFF;
    }

    outb(PIT_COMMAND, 0x36);
    outb(PIT_CHANNEL0, (uint8_t)(divisor & 0xFF));
    outb(PIT_CHANNEL0, (uint8_t)((divisor >> 8) & 0xFF));
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

    outb(PIT_COMMAND, 0x30);
    outb(PIT_CHANNEL0, (uint8_t)(pit_reload & 0xFF));
    outb(PIT_CHANNEL0, (uint8_t)(pit_reload >> 8));

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

aios_status_t kernel_timer_irq_init(uint32_t hz) {
    if (hz == 0) {
        return AIOS_ERR_INVAL;
    }

    irq_disable();
    g_timer_irq_ticks = 0;
    g_timer_irq_hz = hz;

    pic_remap_timer_only();
    pit_program_periodic(hz);

    uint64_t before = g_timer_irq_ticks;
    uint64_t start_ns = kernel_time_monotonic_ns();
    irq_enable();

    while (g_timer_irq_ticks == before) {
        uint64_t now_ns = kernel_time_monotonic_ns();
        if (now_ns > start_ns && (now_ns - start_ns) > TIMER_READY_NS) {
            serial_printf("[TIMER] PIT IRQ timeout hz=%u vector=%u ticks=%u\n",
                (uint64_t)hz,
                (uint64_t)KERNEL_TIMER_IRQ_VECTOR,
                g_timer_irq_ticks);
            return AIOS_ERR_TIMEOUT;
        }
    }

    serial_printf("[TIMER] PIT IRQ ready hz=%u vector=%u ticks=%u\n",
        (uint64_t)hz,
        (uint64_t)KERNEL_TIMER_IRQ_VECTOR,
        g_timer_irq_ticks);

    return AIOS_OK;
}

void kernel_timer_irq_handler(void) {
    g_timer_irq_ticks++;
    ai_sched_tick();
}

uint64_t kernel_timer_irq_ticks(void) {
    return g_timer_irq_ticks;
}

uint32_t kernel_timer_irq_hz(void) {
    return g_timer_irq_hz;
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
