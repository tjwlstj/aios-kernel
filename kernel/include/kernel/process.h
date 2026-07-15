/*
 * AIOS Kernel - Static Bootstrap Process Ownership
 *
 * This is a bounded bridge between private address-space slots and future
 * schedulable processes. It owns static descriptors, run state, and one
 * 16KiB ring3->ring0 entry stack per slot; it is not a general process table.
 */

#ifndef _AIOS_KERNEL_PROCESS_H
#define _AIOS_KERNEL_PROCESS_H

#include <kernel/types.h>
#include <kernel/user_mode.h>
#include <mm/address_space.h>

#define BOOTSTRAP_PROCESS_COUNT ADDRESS_SPACE_BOOTSTRAP_USER_SLOT_COUNT
#define BOOTSTRAP_PROCESS_PID_BASE 1U

/* C/NASM contract used by kernel/core/user_entry.asm. Keep the explicit
 * offsets append-only and let the runtime proof catch assembly drift. */
#define BOOTSTRAP_RUN_STATE_RESUME_RSP_OFFSET         0U
#define BOOTSTRAP_RUN_STATE_SYSCALLS_OFFSET           8U
#define BOOTSTRAP_RUN_STATE_INT80_ENTRIES_OFFSET     16U
#define BOOTSTRAP_RUN_STATE_EXIT_CODE_OFFSET         24U
#define BOOTSTRAP_RUN_STATE_ENTRY_RSP_MIN_OFFSET     32U
#define BOOTSTRAP_RUN_STATE_ENTRY_RSP_MAX_OFFSET     40U
#define BOOTSTRAP_RUN_STATE_EXITED_OFFSET            48U
#define BOOTSTRAP_RUN_STATE_SIZE                     56U

typedef struct bootstrap_user_run_state {
    uint64_t kernel_resume_rsp;
    uint64_t user_syscalls;       /* non-exit int 0x80 dispatches */
    uint64_t int80_entries;       /* includes the exit entry */
    uint64_t exit_code;
    uint64_t entry_rsp_min;
    uint64_t entry_rsp_max;
    uint8_t  exited;
    uint8_t  reserved[7];
} bootstrap_user_run_state_t;

AIOS_STATIC_ASSERT(
    __builtin_offsetof(bootstrap_user_run_state_t, kernel_resume_rsp) ==
        BOOTSTRAP_RUN_STATE_RESUME_RSP_OFFSET,
    "run-state resume RSP ABI drift");
AIOS_STATIC_ASSERT(
    __builtin_offsetof(bootstrap_user_run_state_t, user_syscalls) ==
        BOOTSTRAP_RUN_STATE_SYSCALLS_OFFSET,
    "run-state syscall ABI drift");
AIOS_STATIC_ASSERT(
    __builtin_offsetof(bootstrap_user_run_state_t, int80_entries) ==
        BOOTSTRAP_RUN_STATE_INT80_ENTRIES_OFFSET,
    "run-state int80 ABI drift");
AIOS_STATIC_ASSERT(
    __builtin_offsetof(bootstrap_user_run_state_t, exit_code) ==
        BOOTSTRAP_RUN_STATE_EXIT_CODE_OFFSET,
    "run-state exit code ABI drift");
AIOS_STATIC_ASSERT(
    __builtin_offsetof(bootstrap_user_run_state_t, entry_rsp_min) ==
        BOOTSTRAP_RUN_STATE_ENTRY_RSP_MIN_OFFSET,
    "run-state minimum RSP ABI drift");
AIOS_STATIC_ASSERT(
    __builtin_offsetof(bootstrap_user_run_state_t, entry_rsp_max) ==
        BOOTSTRAP_RUN_STATE_ENTRY_RSP_MAX_OFFSET,
    "run-state maximum RSP ABI drift");
AIOS_STATIC_ASSERT(
    __builtin_offsetof(bootstrap_user_run_state_t, exited) ==
        BOOTSTRAP_RUN_STATE_EXITED_OFFSET,
    "run-state exited ABI drift");
AIOS_STATIC_ASSERT(sizeof(bootstrap_user_run_state_t) ==
        BOOTSTRAP_RUN_STATE_SIZE,
    "run-state C/NASM size ABI drift");

typedef struct bootstrap_process {
    pid_t pid;
    uint32_t slot;
    address_space_bootstrap_slot_t address_space;
    bootstrap_user_run_state_t run_state;
    uint64_t kernel_stack_base;
    uint64_t kernel_stack_top;
    uint64_t kernel_stack_size;
    bool prepared;
    bool active;
} bootstrap_process_t;

typedef struct bootstrap_process_guard {
    address_space_guard_t address_space;
    user_mode_rsp0_guard_t rsp0;
    uint64_t int80_entries;
    bool rsp0_changed;
    bool all_int80_entries_in_stack;
    bool kernel_stack_floor_canary_ok;
    bool leaf_sealed;
    bool nx_enforced;
    bool active;
} bootstrap_process_guard_t;

typedef struct bootstrap_process_stats {
    uint32_t slots;
    uint32_t owned_processes;
    uint32_t current_pid;
    uint32_t last_pid;
    uint64_t completed_runs;
    uint64_t rsp0_publishes;
    uint64_t rsp0_restores;
    bool ownership_selftest_passed;
    bool unique_cr3;
    bool unique_backing;
    bool unique_kernel_stack;
    bool tss_rsp0_baseline;
} bootstrap_process_stats_t;

aios_status_t bootstrap_process_init(void);
bool bootstrap_process_ready(void);
aios_status_t bootstrap_process_prepare(
    uint32_t slot, bool executable, bootstrap_process_t **out);
aios_status_t bootstrap_process_cancel(bootstrap_process_t *process);
aios_status_t bootstrap_process_activate(
    bootstrap_process_t *process, bootstrap_process_guard_t *guard);
aios_status_t bootstrap_process_finish(
    bootstrap_process_t *process, bootstrap_process_guard_t *guard);
bootstrap_process_t *bootstrap_process_current(void);
void bootstrap_process_get_stats(bootstrap_process_stats_t *out);

#endif /* _AIOS_KERNEL_PROCESS_H */
