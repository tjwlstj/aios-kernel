/*
 * AIOS Kernel - CPU Security Feature Control
 * AI-Native Operating System
 *
 * Enables CPU-level exploit mitigations that require no page-table or
 * uaccess cooperation: SMEP (kernel cannot execute user pages) and UMIP
 * (user mode cannot read descriptor-table registers). SMAP is detected
 * and reported but left disabled until copy_*_user gains stac/clac
 * bracketing. NX itself is enabled by the boot path (EFER.NXE); this
 * module only reports its state.
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
    bool smap_enabled;      /* intentionally false for now */
    bool umip_supported;
    bool umip_enabled;
} cpu_sec_info_t;

aios_status_t cpu_security_init(void);
aios_status_t cpu_security_info(cpu_sec_info_t *out);

#endif /* _AIOS_CPU_SEC_H */
