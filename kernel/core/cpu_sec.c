/*
 * AIOS Kernel - CPU Security Feature Control
 * AI-Native Operating System
 */

#include <kernel/cpu_sec.h>
#include <kernel/user_access.h>
#include <drivers/serial.h>

#define CPUID_EXT_FEATURES      0x80000001u
#define CPUID_EXT_EDX_NX        BIT(20)

#define CPUID_STRUCT_EXT_LEAF   0x7u
#define CPUID_LEAF7_EBX_SMEP    BIT(7)
#define CPUID_LEAF7_EBX_SMAP    BIT(20)
#define CPUID_LEAF7_ECX_UMIP    BIT(2)

#define MSR_EFER                0xC0000080u
#define EFER_NXE                BIT(11)

#define CR4_UMIP                BIT(11)
#define CR4_SMEP                BIT(20)
#define CR4_SMAP                BIT(21)

static cpu_sec_info_t g_cpu_sec;

static void cpuid(uint32_t leaf, uint32_t subleaf,
                  uint32_t *eax, uint32_t *ebx, uint32_t *ecx, uint32_t *edx) {
    uint32_t a = 0, b = 0, c = 0, d = 0;
    __asm__ volatile ("cpuid"
        : "=a"(a), "=b"(b), "=c"(c), "=d"(d)
        : "a"(leaf), "c"(subleaf));
    if (eax) *eax = a;
    if (ebx) *ebx = b;
    if (ecx) *ecx = c;
    if (edx) *edx = d;
}

static uint64_t read_msr(uint32_t msr) {
    uint32_t lo = 0, hi = 0;
    __asm__ volatile ("rdmsr" : "=a"(lo), "=d"(hi) : "c"(msr));
    return ((uint64_t)hi << 32) | lo;
}

static uint64_t read_cr4(void) {
    uint64_t value = 0;
    __asm__ volatile ("mov %%cr4, %0" : "=r"(value));
    return value;
}

static void write_cr4(uint64_t value) {
    __asm__ volatile ("mov %0, %%cr4" :: "r"(value) : "memory");
}

aios_status_t cpu_security_init(void) {
    uint32_t max_basic = 0;
    uint32_t max_ext = 0;
    uint32_t ebx = 0;
    uint32_t ecx = 0;
    uint32_t edx = 0;
    uint64_t cr4;

    cpuid(0, 0, &max_basic, NULL, NULL, NULL);
    cpuid(0x80000000u, 0, &max_ext, NULL, NULL, NULL);

    if (max_ext >= CPUID_EXT_FEATURES) {
        cpuid(CPUID_EXT_FEATURES, 0, NULL, NULL, NULL, &edx);
        g_cpu_sec.nx_supported = (edx & CPUID_EXT_EDX_NX) != 0;
    }
    g_cpu_sec.nx_enabled = (read_msr(MSR_EFER) & EFER_NXE) != 0;

    if (max_basic >= CPUID_STRUCT_EXT_LEAF) {
        cpuid(CPUID_STRUCT_EXT_LEAF, 0, NULL, &ebx, &ecx, NULL);
        g_cpu_sec.smep_supported = (ebx & CPUID_LEAF7_EBX_SMEP) != 0;
        g_cpu_sec.smap_supported = (ebx & CPUID_LEAF7_EBX_SMAP) != 0;
        g_cpu_sec.umip_supported = (ecx & CPUID_LEAF7_ECX_UMIP) != 0;
    }

    cr4 = read_cr4();
    if (g_cpu_sec.smep_supported) {
        cr4 |= CR4_SMEP;
    }
    if (g_cpu_sec.umip_supported) {
        cr4 |= CR4_UMIP;
    }
    /* SMAP is now backed by STAC/CLAC bracketing in the uaccess copies,
     * so enable it when supported. The CPU keeps AC=0 by default, so any
     * stray kernel touch of a user page outside a copy now faults. */
    if (g_cpu_sec.smap_supported) {
        cr4 |= CR4_SMAP;
    }
    write_cr4(cr4);

    cr4 = read_cr4();
    g_cpu_sec.smep_enabled = (cr4 & CR4_SMEP) != 0;
    g_cpu_sec.umip_enabled = (cr4 & CR4_UMIP) != 0;
    g_cpu_sec.smap_enabled = (cr4 & CR4_SMAP) != 0;

    /* Tell the uaccess layer whether to emit STAC/CLAC around user-page
     * touches (STAC/CLAC are #UD on CPUs without SMAP). */
    user_access_set_smap_active(g_cpu_sec.smap_enabled);

    serial_printf("[SEC] nx=%u smep=%u umip=%u smap_supported=%u smap=%u\n",
        (uint64_t)g_cpu_sec.nx_enabled,
        (uint64_t)g_cpu_sec.smep_enabled,
        (uint64_t)g_cpu_sec.umip_enabled,
        (uint64_t)g_cpu_sec.smap_supported,
        (uint64_t)g_cpu_sec.smap_enabled);

    return AIOS_OK;
}

aios_status_t cpu_security_info(cpu_sec_info_t *out) {
    if (!out) {
        return AIOS_ERR_INVAL;
    }
    *out = g_cpu_sec;
    return AIOS_OK;
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
