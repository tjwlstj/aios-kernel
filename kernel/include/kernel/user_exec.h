/*
 * AIOS Kernel - First Ring3 User Execution Slice
 * AI-Native Operating System
 *
 * Activates a private bootstrap user slot, enters CPL3, and runs a tiny demo
 * program that calls back into the kernel via int 0x80 before exiting. This
 * proves the full
 * ring0<->ring3 round trip: privilege transition, user-issued syscall,
 * copy_to_user into a user buffer, and a clean return to the kernel.
 */

#ifndef _AIOS_KERNEL_USER_EXEC_H
#define _AIOS_KERNEL_USER_EXEC_H

#include <kernel/types.h>

typedef struct {
    bool     attempted;       /* run was launched */
    bool     entered;         /* reached ring3 (>=1 user syscall observed) */
    bool     returned;        /* came back via the exit syscall */
    bool     syscall_ok;      /* user buffer held valid syscall result */
    bool     boundary_ok;     /* ring3 attempt to reach kernel memory was denied */
    uint32_t user_syscalls;   /* int 0x80 count during the run */
    uint64_t exit_code;       /* exit() argument from ring3 */
    uint64_t duration_ns;     /* high-precision ring3 residency time */
    uint32_t observed_pipeline_max; /* value the user syscall read back */
    bool     elf_loaded;      /* image parsed and mapped as ELF64 */
    uint64_t elf_entry;       /* e_entry of the loaded image */
    uint32_t elf_segments;    /* PT_LOAD segments mapped */
    bool     private_cr3;     /* execution used a static private user slot */
    bool     cr3_restored;    /* exact pre-run CR3 was restored */
    bool     if_restored;     /* caller IF state matched after restoration */
    bool     leaf_sealed;     /* slot policy reset and backing scrubbed */
    bool     nx_enforced;     /* sealed leaf has hardware NX enforcement */
    bool     tensor_range_excluded; /* absent from tensor free/active sets */
    uint32_t address_space_slot;   /* static slot used by the run */
} user_exec_info_t;

/* Run the first ring3 slice. Returns AIOS_OK only on a fully verified
 * round trip. */
aios_status_t user_exec_run_first(void);

/* Snapshot of the most recent run (zeroed before the first run). */
void user_exec_get_info(user_exec_info_t *out);

#endif /* _AIOS_KERNEL_USER_EXEC_H */
