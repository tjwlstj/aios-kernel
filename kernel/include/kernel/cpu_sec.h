/*
 * AIOS Kernel - CPU Security Feature Control
 * AI-Native Operating System
 *
 * Enables CPU-level exploit mitigations: SMEP (kernel cannot execute user
 * pages), UMIP (user mode cannot read descriptor-table registers), and SMAP
 * when the uaccess and entry-boundary fences are available. NX itself is
 * enabled by the boot path (EFER.NXE); this module reports its state.
 */

#ifndef _AIOS_CPU_SEC_H
#define _AIOS_CPU_SEC_H

#include <kernel/types.h>

typedef struct {
    bool nx_supported;
    bool nx_enabled;        /* EFER.NXE set by boot path */
    bool smep_supported;
    bool smep_enabled;
    bool smap_supported;
    bool smap_enabled;      /* CR4.SMAP readback; also gates entry CLAC */
    bool umip_supported;
    bool umip_enabled;
} cpu_sec_info_t;

/* C/NASM contract for the bounded ring3 entry AC challenge. The assembly
 * stubs update only these counters; cpu_security_entry_ac_ready() owns their
 * semantic interpretation. Keep offsets append-only. */
#define CPU_SEC_ENTRY_AC_SCHEMA                       1U
#define CPU_SEC_ENTRY_COMMON_ENTRIES_OFFSET       0U
#define CPU_SEC_ENTRY_COMMON_SAVED_AC_OFFSET      8U
#define CPU_SEC_ENTRY_COMMON_CLAC_OFFSET          16U
#define CPU_SEC_ENTRY_COMMON_FALLBACK_OFFSET      24U
#define CPU_SEC_ENTRY_COMMON_POST_AC0_OFFSET      32U
#define CPU_SEC_ENTRY_INT80_ENTRIES_OFFSET        40U
#define CPU_SEC_ENTRY_INT80_SAVED_AC_OFFSET       48U
#define CPU_SEC_ENTRY_INT80_CLAC_OFFSET           56U
#define CPU_SEC_ENTRY_INT80_FALLBACK_OFFSET       64U
#define CPU_SEC_ENTRY_INT80_POST_AC0_OFFSET       72U
#define CPU_SEC_ENTRY_AC_INFO_SIZE                80U

typedef struct {
    uint64_t common_entries;
    uint64_t common_saved_ac;
    uint64_t common_clac;
    uint64_t common_fallback;
    uint64_t common_post_ac0;
    uint64_t int80_entries;
    uint64_t int80_saved_ac;
    uint64_t int80_clac;
    uint64_t int80_fallback;
    uint64_t int80_post_ac0;
} cpu_sec_entry_ac_info_t;

#define CPU_SEC_ENTRY_ASSERT_OFFSET(field, expected) \
    AIOS_STATIC_ASSERT( \
        __builtin_offsetof(cpu_sec_entry_ac_info_t, field) == (expected), \
        "entry AC evidence " #field " C/NASM offset drift")

CPU_SEC_ENTRY_ASSERT_OFFSET(common_entries,
    CPU_SEC_ENTRY_COMMON_ENTRIES_OFFSET);
CPU_SEC_ENTRY_ASSERT_OFFSET(common_saved_ac,
    CPU_SEC_ENTRY_COMMON_SAVED_AC_OFFSET);
CPU_SEC_ENTRY_ASSERT_OFFSET(common_clac,
    CPU_SEC_ENTRY_COMMON_CLAC_OFFSET);
CPU_SEC_ENTRY_ASSERT_OFFSET(common_fallback,
    CPU_SEC_ENTRY_COMMON_FALLBACK_OFFSET);
CPU_SEC_ENTRY_ASSERT_OFFSET(common_post_ac0,
    CPU_SEC_ENTRY_COMMON_POST_AC0_OFFSET);
CPU_SEC_ENTRY_ASSERT_OFFSET(int80_entries,
    CPU_SEC_ENTRY_INT80_ENTRIES_OFFSET);
CPU_SEC_ENTRY_ASSERT_OFFSET(int80_saved_ac,
    CPU_SEC_ENTRY_INT80_SAVED_AC_OFFSET);
CPU_SEC_ENTRY_ASSERT_OFFSET(int80_clac,
    CPU_SEC_ENTRY_INT80_CLAC_OFFSET);
CPU_SEC_ENTRY_ASSERT_OFFSET(int80_fallback,
    CPU_SEC_ENTRY_INT80_FALLBACK_OFFSET);
CPU_SEC_ENTRY_ASSERT_OFFSET(int80_post_ac0,
    CPU_SEC_ENTRY_INT80_POST_AC0_OFFSET);
AIOS_STATIC_ASSERT(sizeof(cpu_sec_entry_ac_info_t) ==
        CPU_SEC_ENTRY_AC_INFO_SIZE,
    "entry AC evidence C/NASM size drift");

#undef CPU_SEC_ENTRY_ASSERT_OFFSET

aios_status_t cpu_security_init(void);
aios_status_t cpu_security_info(cpu_sec_info_t *out);
void cpu_security_entry_ac_info(cpu_sec_entry_ac_info_t *out);
bool cpu_security_entry_clac_active(void);
bool cpu_security_entry_ac_ready(void);
void cpu_security_emit_entry_ac_marker(void);

#endif /* _AIOS_CPU_SEC_H */
