/*
 * AIOS Kernel - Kernel Room Topology Foundation
 * AI-Native Operating System
 *
 * Provides a read-only snapshot of the current "Kernel Room" state
 * and a stable descriptor table for axis gates mapped onto syscall ranges.
 */

#ifndef _AIOS_KERNEL_KERNEL_ROOM_H
#define _AIOS_KERNEL_KERNEL_ROOM_H

#include <kernel/types.h>
#include <kernel/health.h>

typedef enum {
    KERNEL_ROOM_GATE_MODEL = 0,
    KERNEL_ROOM_GATE_TENSOR = 1,
    KERNEL_ROOM_GATE_INFER = 2,
    KERNEL_ROOM_GATE_TRAIN = 3,
    KERNEL_ROOM_GATE_ACCEL = 4,
    KERNEL_ROOM_GATE_PIPE = 5,
    KERNEL_ROOM_GATE_INFO = 6,
    KERNEL_ROOM_GATE_AUTONOMY = 7,
    KERNEL_ROOM_GATE_SLM = 8,
    KERNEL_ROOM_GATE_COUNT = 9,
} kernel_room_gate_id_t;

AIOS_STATIC_ASSERT(KERNEL_ROOM_GATE_COUNT == 9,
    "Update Kernel Room gate users when gate enum changes");

typedef enum {
    KERNEL_ROOM_GATE_RISK_OBSERVE = 0,
    KERNEL_ROOM_GATE_RISK_BOUNDED_CONTROL = 1,
    KERNEL_ROOM_GATE_RISK_BOUNDED_DATA = 2,
    KERNEL_ROOM_GATE_RISK_IO_PATH = 3,
    KERNEL_ROOM_GATE_RISK_COUNT = 4,
} kernel_room_gate_risk_t;

AIOS_STATIC_ASSERT(KERNEL_ROOM_GATE_RISK_COUNT == 4,
    "Update Kernel Room gate risk users when risk enum changes");

typedef struct {
    kernel_room_gate_id_t id;
    const char *name;
    uint16_t syscall_start;
    uint16_t syscall_end;
    kernel_stability_t min_stability;
    kernel_room_gate_risk_t risk;
    bool completion_ready;
    bool shared_memory_ready;
    bool risky_io;
} kernel_room_gate_descriptor_t;

typedef struct {
    uint64_t ts_ns;
    kernel_stability_t stability;
    bool autonomy_allowed;
    bool risky_io_allowed;
    bool user_mode_ready;
    uint32_t ok_subsystems;
    uint32_t degraded_subsystems;
    uint32_t failed_subsystems;
    uint32_t unknown_subsystems;
    uint32_t memory_domains;
    uint32_t memory_windows;
    uint8_t memory_topology;
    uint32_t driver_entries;
    uint32_t driver_ready;
    uint32_t driver_degraded;
    uint16_t merged_queue_depth_hint;
    uint16_t merged_poll_budget_hint;
    uint32_t merged_dma_window_kib_hint;
    uint32_t slm_plan_count;
    uint32_t agent_nodes;
    uint16_t pipeline_submit_entries;
    uint16_t pipeline_completion_entries;
    uint32_t registered_rings;
    uint32_t active_rings;
    uint64_t ring_notifies;
    uint32_t gate_count;
} kernel_room_snapshot_t;

static inline bool kernel_room_gate_valid(uint32_t gate_id) {
    return gate_id < KERNEL_ROOM_GATE_COUNT;
}

aios_status_t kernel_room_snapshot_read(kernel_room_snapshot_t *out);
const kernel_room_gate_descriptor_t *kernel_room_gate_descriptors(uint32_t *count);
const char *kernel_room_gate_risk_name(kernel_room_gate_risk_t risk);
void kernel_room_dump(void);

#endif /* _AIOS_KERNEL_KERNEL_ROOM_H */
