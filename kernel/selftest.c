/*
 * AIOS Kernel - Boot-Time Self Tests
 * AI-Native Operating System
 */

#include <kernel/selftest.h>
#include <drivers/vga.h>
#include <drivers/serial.h>
#include <lib/string.h>

#define MEM_BENCH_BYTES      KB(256)
#define MEM_BENCH_ITERATIONS 64
#define MEMMOVE_OFFSET       128
#define CACHE_PROBE_BYTES    MB(16)
#define CACHE_PROBE_STRIDE   CACHE_LINE_SIZE
#define PIT_FREQ_HZ          1193182U
#define PIT_CALIBRATE_US     10000U

static uint8_t mem_src[MEM_BENCH_BYTES] ALIGNED(TENSOR_ALIGN);
static uint8_t mem_dst[MEM_BENCH_BYTES] ALIGNED(TENSOR_ALIGN);
static uint8_t mem_tmp[MEM_BENCH_BYTES] ALIGNED(TENSOR_ALIGN);
static uint8_t cache_probe[CACHE_PROBE_BYTES] ALIGNED(CACHE_LINE_SIZE);
static memory_selftest_result_t last_result;

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

static void detect_cache_hierarchy(memory_selftest_result_t *out) {
    uint32_t eax = 0;
    uint32_t ebx = 0;
    uint32_t ecx = 0;
    uint32_t edx = 0;

    memset(&out->l1d, 0, sizeof(out->l1d));
    memset(&out->l2, 0, sizeof(out->l2));
    memset(&out->l3, 0, sizeof(out->l3));

    for (uint32_t i = 0; i < 8; i++) {
        cpuid_leaf(4, i, &eax, &ebx, &ecx, &edx);
        uint32_t cache_type = eax & 0x1F;
        uint32_t cache_level = (eax >> 5) & 0x7;
        if (cache_type == 0) {
            break;
        }

        uint32_t line_size = (ebx & 0xFFF) + 1;
        uint32_t partitions = ((ebx >> 12) & 0x3FF) + 1;
        uint32_t ways = ((ebx >> 22) & 0x3FF) + 1;
        uint32_t sets = ecx + 1;
        uint32_t size = line_size * partitions * ways * sets;

        cache_info_t *target = NULL;
        if (cache_level == 1 && cache_type == 1) {
            target = &out->l1d;
        } else if (cache_level == 2) {
            target = &out->l2;
        } else if (cache_level == 3) {
            target = &out->l3;
        }

        if (!target) {
            continue;
        }

        target->present = true;
        target->size_bytes = size;
        target->line_size = line_size;
        target->ways = (uint16_t)ways;
        target->sets = sets;
    }
}

static uint64_t choose_working_set(uint32_t measured, uint32_t fallback, uint32_t maximum) {
    uint64_t size = measured ? measured : fallback;
    if (size > maximum) {
        size = maximum;
    }
    if (size < CACHE_LINE_SIZE) {
        size = CACHE_LINE_SIZE;
    }
    return ALIGN_UP(size, CACHE_LINE_SIZE);
}

static uint64_t measure_access_cycles_x100(uint64_t working_set, uint32_t rounds) {
    volatile uint64_t sink = 0;
    uint64_t accesses_per_round = working_set / CACHE_PROBE_STRIDE;
    if (accesses_per_round == 0) {
        accesses_per_round = 1;
    }

    uint64_t start = read_tsc();
    for (uint32_t round = 0; round < rounds; round++) {
        for (uint64_t i = 0; i < working_set; i += CACHE_PROBE_STRIDE) {
            sink += cache_probe[(i + ((uint64_t)round * CACHE_PROBE_STRIDE)) % working_set];
        }
    }
    uint64_t elapsed = read_tsc() - start;

    if (sink == 0xFFFFFFFFFFFFFFFFULL) {
        serial_write("");
    }

    return (elapsed * 100ULL) / MAX(accesses_per_round * rounds, 1);
}

static boot_perf_tier_t classify_boot_tier(const memory_selftest_result_t *out) {
    if (out->memcpy_mib_per_sec >= 6000 && out->dram_cycles_per_access_x100 <= 1400) {
        return BOOT_PERF_TIER_HIGH;
    }
    if (out->memcpy_mib_per_sec >= 2500 && out->dram_cycles_per_access_x100 <= 2600) {
        return BOOT_PERF_TIER_MID;
    }
    return BOOT_PERF_TIER_LOW;
}

static uint64_t compute_mib_per_sec(uint64_t total_bytes, uint64_t cycles, uint64_t tsc_khz) {
    if (cycles == 0 || tsc_khz == 0) {
        return 0;
    }

    uint64_t bytes_per_sec = (total_bytes * tsc_khz * 1000ULL) / cycles;
    return bytes_per_sec / MB(1);
}

static void fill_pattern(uint8_t *buf, uint64_t size, uint8_t seed) {
    for (uint64_t i = 0; i < size; i++) {
        buf[i] = (uint8_t)((i * 37 + seed) & 0xFF);
    }
}

static bool verify_value(const uint8_t *buf, uint64_t size, uint8_t value) {
    for (uint64_t i = 0; i < size; i++) {
        if (buf[i] != value) {
            return false;
        }
    }
    return true;
}

static bool verify_equal(const uint8_t *lhs, const uint8_t *rhs, uint64_t size) {
    return memcmp(lhs, rhs, size) == 0;
}

static bool verify_overlap_move(const uint8_t *buf, uint64_t size) {
    for (uint64_t i = 0; i < size - MEMMOVE_OFFSET; i++) {
        uint8_t expected = (uint8_t)((i * 37 + 0x03) & 0xFF);
        if (buf[i + MEMMOVE_OFFSET] != expected) {
            return false;
        }
    }
    return true;
}

aios_status_t kernel_memory_selftest_run(memory_selftest_result_t *out) {
    if (!out) {
        return AIOS_ERR_INVAL;
    }

    memset(out, 0, sizeof(*out));
    out->tsc_khz = calibrate_tsc_khz(&out->invariant_tsc);
    detect_cache_hierarchy(out);

    fill_pattern(mem_src, MEM_BENCH_BYTES, 0x5A);
    fill_pattern(mem_tmp, MEM_BENCH_BYTES, 0x03);
    memset(mem_dst, 0, MEM_BENCH_BYTES);
    fill_pattern(cache_probe, CACHE_PROBE_BYTES, 0x11);

    uint64_t start = read_tsc();
    for (uint64_t i = 0; i < MEM_BENCH_ITERATIONS; i++) {
        memset(mem_dst, 0xA5, MEM_BENCH_BYTES);
    }
    out->memset_cycles = read_tsc() - start;

    if (!verify_value(mem_dst, MEM_BENCH_BYTES, 0xA5)) {
        out->passed = false;
        return AIOS_ERR_IO;
    }

    start = read_tsc();
    for (uint64_t i = 0; i < MEM_BENCH_ITERATIONS; i++) {
        memcpy(mem_dst, mem_src, MEM_BENCH_BYTES);
    }
    out->memcpy_cycles = read_tsc() - start;

    if (!verify_equal(mem_dst, mem_src, MEM_BENCH_BYTES)) {
        out->passed = false;
        return AIOS_ERR_IO;
    }

    start = read_tsc();
    for (uint64_t i = 0; i < MEM_BENCH_ITERATIONS; i++) {
        fill_pattern(mem_tmp, MEM_BENCH_BYTES, 0x03);
        memmove(mem_tmp + MEMMOVE_OFFSET, mem_tmp, MEM_BENCH_BYTES - MEMMOVE_OFFSET);
    }
    out->memmove_cycles = read_tsc() - start;

    if (!verify_overlap_move(mem_tmp, MEM_BENCH_BYTES)) {
        out->passed = false;
        return AIOS_ERR_IO;
    }

    out->buffer_size = MEM_BENCH_BYTES;
    out->iterations = MEM_BENCH_ITERATIONS;
    out->memcpy_mib_per_sec = compute_mib_per_sec(
        out->buffer_size * out->iterations,
        out->memcpy_cycles,
        out->tsc_khz);

    out->l1_cycles_per_access_x100 = measure_access_cycles_x100(
        choose_working_set(out->l1d.size_bytes / 2, KB(32), KB(64)), 2048);
    out->l2_cycles_per_access_x100 = measure_access_cycles_x100(
        choose_working_set(out->l2.size_bytes / 2, KB(256), KB(512)), 512);
    out->l3_cycles_per_access_x100 = measure_access_cycles_x100(
        choose_working_set(out->l3.size_bytes / 2, MB(2), MB(8)), 128);
    out->dram_cycles_per_access_x100 = measure_access_cycles_x100(MB(16), 64);
    out->tier = classify_boot_tier(out);
    out->passed = true;
    last_result = *out;
    return AIOS_OK;
}

void kernel_memory_selftest_print(const memory_selftest_result_t *result) {
    if (!result) {
        return;
    }

    uint64_t total_kib = (result->buffer_size * result->iterations) / KB(1);
    uint64_t memset_cycles_per_kib = result->memset_cycles / MAX(total_kib, 1);
    uint64_t memcpy_cycles_per_kib = result->memcpy_cycles / MAX(total_kib, 1);
    uint64_t memmove_cycles_per_kib = result->memmove_cycles / MAX(total_kib, 1);

    console_write_color("[SELFTEST] ", VGA_LIGHT_GREEN, VGA_BLUE);
    kprintf("Memory microbench %s (%u KiB x %u)\n",
        result->passed ? "PASS" : "FAIL",
        result->buffer_size / KB(1),
        result->iterations);
    kprintf("           memset=%u cyc (%u cyc/KiB)\n",
        result->memset_cycles, memset_cycles_per_kib);
    kprintf("           memcpy=%u cyc (%u cyc/KiB)\n",
        result->memcpy_cycles, memcpy_cycles_per_kib);
    kprintf("           memmove=%u cyc (%u cyc/KiB)\n",
        result->memmove_cycles, memmove_cycles_per_kib);
    kprintf("           approx memcpy throughput=%u MiB/s | TSC=%u kHz | tier=%s\n",
        result->memcpy_mib_per_sec,
        result->tsc_khz,
        kernel_boot_perf_tier_name(result->tier));
    kprintf("           cache L1=%u KiB L2=%u KiB L3=%u KiB | latency x100 cyc: L1=%u L2=%u L3=%u DRAM=%u\n",
        result->l1d.size_bytes / KB(1),
        result->l2.size_bytes / KB(1),
        result->l3.size_bytes / KB(1),
        result->l1_cycles_per_access_x100,
        result->l2_cycles_per_access_x100,
        result->l3_cycles_per_access_x100,
        result->dram_cycles_per_access_x100);

    serial_printf("[SELFTEST] Memory microbench %s (%u KiB x %u)\n",
        (uint64_t)(uintptr_t)(result->passed ? "PASS" : "FAIL"),
        result->buffer_size / KB(1),
        result->iterations);
    serial_printf("[SELFTEST] memset=%u cyc (%u cyc/KiB)\n",
        result->memset_cycles, memset_cycles_per_kib);
    serial_printf("[SELFTEST] memcpy=%u cyc (%u cyc/KiB)\n",
        result->memcpy_cycles, memcpy_cycles_per_kib);
    serial_printf("[SELFTEST] memmove=%u cyc (%u cyc/KiB)\n",
        result->memmove_cycles, memmove_cycles_per_kib);
    serial_printf("[PROFILE] TSC=%u kHz invariant=%u memcpy=%u MiB/s tier=%s\n",
        result->tsc_khz,
        result->invariant_tsc ? 1ULL : 0ULL,
        result->memcpy_mib_per_sec,
        (uint64_t)(uintptr_t)kernel_boot_perf_tier_name(result->tier));
    serial_printf("[PROFILE] Cache KiB L1=%u L2=%u L3=%u | latency x100 cyc L1=%u L2=%u L3=%u DRAM=%u\n",
        result->l1d.size_bytes / KB(1),
        result->l2.size_bytes / KB(1),
        result->l3.size_bytes / KB(1),
        result->l1_cycles_per_access_x100,
        result->l2_cycles_per_access_x100,
        result->l3_cycles_per_access_x100,
        result->dram_cycles_per_access_x100);
}

const memory_selftest_result_t *kernel_memory_selftest_last(void) {
    return &last_result;
}

const char *kernel_boot_perf_tier_name(boot_perf_tier_t tier) {
    switch (tier) {
        case BOOT_PERF_TIER_HIGH: return "HIGH";
        case BOOT_PERF_TIER_MID:  return "MID";
        case BOOT_PERF_TIER_LOW:
        default:                  return "LOW";
    }
}
