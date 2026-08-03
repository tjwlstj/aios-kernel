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
    bool     process_bound;   /* run used a static bootstrap process owner */
    uint32_t process_id;      /* bootstrap process identifier */
    uint32_t kernel_stack_bytes; /* process-bound ring0 entry stack size */
    bool     rsp0_changed;    /* process stack differed from boot rsp0 */
    bool     rsp0_published;  /* TSS rsp0 exact publish readback passed */
    uint32_t int80_entries;   /* all int 0x80 entries, including exit */
    bool     all_int80_entries_in_stack; /* every int80 raw RSP on stack */
    bool     rsp0_restored;   /* exact boot rsp0 readback after run */
    bool     kernel_stack_floor_canary_ok; /* static floor canary survived */
    /* CPL3 trapframe capture evidence (armed #BP during the run) */
    uint64_t trap_captures;   /* armed #BP consumes during the run */
    uint64_t trap_cs;         /* captured frame CS */
    uint64_t trap_ss;         /* captured frame SS */
    bool     trap_captured;   /* exactly one armed capture happened */
    bool     trap_from_user;  /* frame CS RPL=3 with user cs/ss selectors */
    bool     trap_rsp_in_user; /* frame RSP inside the user window */
    bool     trap_rip_in_user; /* frame RIP inside the user window */
    bool     trap_canary_ok;  /* user register canaries survived the frame */
    bool     trap_frame_in_kstack; /* frame lay inside this process's stack */
    bool     trap_frame_addr_exact; /* frame base == stack_top - frame size */
} user_exec_info_t;

typedef struct {
    bool     attempted;
    bool     passed;
    uint64_t completed_runs;
    uint32_t first_process_id;
    uint32_t first_address_space_slot;
    uint32_t first_int80_entries;
    uint32_t second_process_id;
    uint32_t second_address_space_slot;
    uint32_t second_int80_entries;
    uint64_t second_exit_code;
    bool     second_syscall_ok;
    bool     second_boundary_ok;
    bool     distinct_pid;
    bool     distinct_slot;
    bool     distinct_cr3;
    bool     distinct_backing;
    bool     distinct_kernel_stack;
    bool     between_runs_clean;
    uint32_t current_pid;
    uint32_t last_pid;
    uint64_t rsp0_publishes;
    uint64_t rsp0_restores;
    bool     tss_rsp0_baseline;
    bool     both_restored;
} user_exec_pair_info_t;

/* Run both static bootstrap processes sequentially. This proves that slot 1
 * is executable through its own CR3, run state, and ring0 entry stack. It is
 * not a timer-preemptive process switch or a full-trapframe scheduler. */
aios_status_t user_exec_run_bootstrap_pair(void);

/* The primary snapshot remains PID 1 / slot 0 for compatibility. */
void user_exec_get_info(user_exec_info_t *out);
void user_exec_get_pair_info(user_exec_pair_info_t *out);

#endif /* _AIOS_KERNEL_USER_EXEC_H */
