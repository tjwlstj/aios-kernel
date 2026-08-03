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
#include <interrupt/trapframe.h>
#include <mm/address_space.h>

#define BOOTSTRAP_PROCESS_COUNT ADDRESS_SPACE_BOOTSTRAP_USER_SLOT_COUNT
#define BOOTSTRAP_PROCESS_PID_BASE 1U
#define BOOTSTRAP_PROCESS_EVENT_SCHEMA 1U
#define BOOTSTRAP_PROCESS_EVENT_CAPACITY 8U
#define BOOTSTRAP_PROCESS_EVENTS_PER_RUN 3U

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

/* Process-owned copy of one validated CPL3 trap. This is durable historical
 * evidence after the entry stack and user mapping are reused; it is not a
 * continuation and must never be used as an iretq source. C-only structure:
 * no C/NASM or public ABI offsets are attached to it. */
typedef struct bootstrap_process_trap_snapshot {
    interrupt_frame_t frame;
    uint64_t frame_addr;
    uint64_t capture_sequence;
    uint64_t run_generation;
    uint64_t owner_cr3;
    uint64_t owner_rsp0;
    uint64_t captures;
    pid_t owner_pid;
    uint32_t owner_slot;
    bool evidence_valid;
    bool owner_bound;
    bool frame_copied;
    bool from_user;
    bool frame_addr_exact;
    bool cr3_matched;
    bool rsp0_matched;
    bool resume_ready;
} bootstrap_process_trap_snapshot_t;

/* Per-boot, append-only evidence journal for the bounded bootstrap lifecycle.
 * These are observation records, not resumable contexts or scheduler switch
 * commands. Numeric values escape through the serial evidence contract and
 * therefore remain explicit and append-only. */
typedef enum bootstrap_process_event_kind {
    BOOTSTRAP_PROCESS_EVENT_KIND_INVALID = 0,
    BOOTSTRAP_PROCESS_EVENT_KIND_ACQUIRE = 1,
    BOOTSTRAP_PROCESS_EVENT_KIND_TRAP_CAPTURE = 2,
    BOOTSTRAP_PROCESS_EVENT_KIND_RELEASE = 3,
    BOOTSTRAP_PROCESS_EVENT_KIND_COUNT = 4
} bootstrap_process_event_kind_t;

typedef enum bootstrap_process_event_reason {
    BOOTSTRAP_PROCESS_EVENT_REASON_INVALID = 0,
    BOOTSTRAP_PROCESS_EVENT_REASON_ACTIVATE_PUBLISH = 1,
    BOOTSTRAP_PROCESS_EVENT_REASON_BREAKPOINT_CAPTURE = 2,
    BOOTSTRAP_PROCESS_EVENT_REASON_RESTORE_PUBLISH = 3,
    BOOTSTRAP_PROCESS_EVENT_REASON_COUNT = 4
} bootstrap_process_event_reason_t;

typedef enum bootstrap_process_event_outcome {
    BOOTSTRAP_PROCESS_EVENT_OUTCOME_INVALID = 0,
    BOOTSTRAP_PROCESS_EVENT_OUTCOME_COMMITTED = 1,
    BOOTSTRAP_PROCESS_EVENT_OUTCOME_COUNT = 2
} bootstrap_process_event_outcome_t;

AIOS_STATIC_ASSERT(BOOTSTRAP_PROCESS_EVENT_KIND_ACQUIRE == 1,
    "process event acquire ID drift");
AIOS_STATIC_ASSERT(BOOTSTRAP_PROCESS_EVENT_KIND_TRAP_CAPTURE == 2,
    "process event capture ID drift");
AIOS_STATIC_ASSERT(BOOTSTRAP_PROCESS_EVENT_KIND_RELEASE == 3,
    "process event release ID drift");
AIOS_STATIC_ASSERT(BOOTSTRAP_PROCESS_EVENT_REASON_ACTIVATE_PUBLISH == 1,
    "process event activate reason ID drift");
AIOS_STATIC_ASSERT(BOOTSTRAP_PROCESS_EVENT_REASON_BREAKPOINT_CAPTURE == 2,
    "process event capture reason ID drift");
AIOS_STATIC_ASSERT(BOOTSTRAP_PROCESS_EVENT_REASON_RESTORE_PUBLISH == 3,
    "process event restore reason ID drift");
AIOS_STATIC_ASSERT(BOOTSTRAP_PROCESS_EVENT_OUTCOME_COMMITTED == 1,
    "process event committed outcome ID drift");
AIOS_STATIC_ASSERT(BOOTSTRAP_PROCESS_EVENT_CAPACITY >=
        BOOTSTRAP_PROCESS_COUNT * BOOTSTRAP_PROCESS_EVENTS_PER_RUN,
    "process event journal cannot hold the bootstrap pair proof");

typedef struct bootstrap_process_event {
    uint64_t event_sequence;
    uint64_t run_generation;
    uint64_t capture_sequence;
    uint64_t from_cr3;
    uint64_t to_cr3;
    uint64_t from_rsp0;
    uint64_t to_rsp0;
    /* Historical #BP metadata only. This deliberately omits the full GPR
     * image and must never be used as an iretq source. */
    uint64_t frame_addr;
    uint64_t frame_rip;
    uint64_t frame_rsp;
    uint64_t frame_rflags;
    uint64_t frame_cs;
    uint64_t frame_ss;
    uint64_t frame_int_no;
    uint64_t frame_err_code;
    pid_t from_pid;
    pid_t to_pid;
    pid_t current_pid;
    uint32_t schema;
    uint32_t slot;
    bootstrap_process_event_kind_t kind;
    bootstrap_process_event_reason_t reason;
    bootstrap_process_event_outcome_t outcome;
    bool owner_ok;
    bool cr3_ok;
    bool rsp0_ok;
    bool if_disabled;
    bool snapshot_ref;
    bool frame_valid;
    bool frame_from_user;
    bool frame_addr_exact;
    bool direct_switch;
    bool resume_ready;
    bool valid;
} bootstrap_process_event_t;

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
    uint64_t run_generation;
    bootstrap_process_trap_snapshot_t trap_snapshot;
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
    uint32_t event_count;
    uint64_t event_last_sequence;
    uint64_t event_dropped;
    bool event_overflow;
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
/* ISR-time ownership binding for the first armed CPL3 trap. */
aios_status_t bootstrap_process_capture_current_trap(
    const interrupt_frame_t *frame);
/* Snapshot remains readable after finish until this slot is prepared again. */
aios_status_t bootstrap_process_get_trap_snapshot(
    uint32_t slot, bootstrap_process_trap_snapshot_t *out);
/* Stable readback is available only while no bootstrap process is active. */
aios_status_t bootstrap_process_get_event(
    uint32_t index, bootstrap_process_event_t *out);
bootstrap_process_t *bootstrap_process_current(void);
void bootstrap_process_get_stats(bootstrap_process_stats_t *out);

#endif /* _AIOS_KERNEL_PROCESS_H */
