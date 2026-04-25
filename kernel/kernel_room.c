/*
 * AIOS Kernel - Kernel Room Topology Foundation
 * AI-Native Operating System
 */

#include <kernel/kernel_room.h>
#include <drivers/driver_model.h>
#include <drivers/serial.h>
#include <drivers/vga.h>
#include <kernel/time.h>
#include <kernel/user_mode.h>
#include <lib/string.h>
#include <mm/memory_fabric.h>
#include <runtime/ai_syscall.h>
#include <runtime/slm_orchestrator.h>

static const kernel_room_gate_descriptor_t g_gate_table[KERNEL_ROOM_GATE_COUNT] = {
    [KERNEL_ROOM_GATE_MODEL] = {
        .id = KERNEL_ROOM_GATE_MODEL,
        .name = "model",
        .syscall_start = SYS_MODEL_LOAD,
        .syscall_end = SYS_MODEL_OPTIMIZE,
        .min_stability = KERNEL_STABILITY_DEGRADED,
        .risk = KERNEL_ROOM_GATE_RISK_BOUNDED_CONTROL,
        .completion_ready = false,
        .shared_memory_ready = false,
        .risky_io = false,
    },
    [KERNEL_ROOM_GATE_TENSOR] = {
        .id = KERNEL_ROOM_GATE_TENSOR,
        .name = "tensor",
        .syscall_start = SYS_TENSOR_CREATE,
        .syscall_end = SYS_TENSOR_INFO,
        .min_stability = KERNEL_STABILITY_DEGRADED,
        .risk = KERNEL_ROOM_GATE_RISK_BOUNDED_DATA,
        .completion_ready = false,
        .shared_memory_ready = true,
        .risky_io = false,
    },
    [KERNEL_ROOM_GATE_INFER] = {
        .id = KERNEL_ROOM_GATE_INFER,
        .name = "infer",
        .syscall_start = SYS_INFER_SUBMIT,
        .syscall_end = SYS_INFER_RING_STATUS,
        .min_stability = KERNEL_STABILITY_DEGRADED,
        .risk = KERNEL_ROOM_GATE_RISK_BOUNDED_DATA,
        .completion_ready = true,
        .shared_memory_ready = true,
        .risky_io = false,
    },
    [KERNEL_ROOM_GATE_TRAIN] = {
        .id = KERNEL_ROOM_GATE_TRAIN,
        .name = "train",
        .syscall_start = SYS_TRAIN_FORWARD,
        .syscall_end = SYS_TRAIN_RESTORE,
        .min_stability = KERNEL_STABILITY_DEGRADED,
        .risk = KERNEL_ROOM_GATE_RISK_BOUNDED_CONTROL,
        .completion_ready = false,
        .shared_memory_ready = false,
        .risky_io = false,
    },
    [KERNEL_ROOM_GATE_ACCEL] = {
        .id = KERNEL_ROOM_GATE_ACCEL,
        .name = "accel",
        .syscall_start = SYS_ACCEL_LIST,
        .syscall_end = SYS_ACCEL_STATS,
        .min_stability = KERNEL_STABILITY_DEGRADED,
        .risk = KERNEL_ROOM_GATE_RISK_IO_PATH,
        .completion_ready = false,
        .shared_memory_ready = false,
        .risky_io = true,
    },
    [KERNEL_ROOM_GATE_PIPE] = {
        .id = KERNEL_ROOM_GATE_PIPE,
        .name = "pipe",
        .syscall_start = SYS_PIPE_CREATE,
        .syscall_end = SYS_PIPE_DESTROY,
        .min_stability = KERNEL_STABILITY_DEGRADED,
        .risk = KERNEL_ROOM_GATE_RISK_BOUNDED_CONTROL,
        .completion_ready = false,
        .shared_memory_ready = false,
        .risky_io = false,
    },
    [KERNEL_ROOM_GATE_INFO] = {
        .id = KERNEL_ROOM_GATE_INFO,
        .name = "info",
        .syscall_start = SYS_INFO_MEMORY,
        .syscall_end = SYS_INFO_BOOTSTRAP,
        .min_stability = KERNEL_STABILITY_UNSAFE,
        .risk = KERNEL_ROOM_GATE_RISK_OBSERVE,
        .completion_ready = false,
        .shared_memory_ready = false,
        .risky_io = false,
    },
    [KERNEL_ROOM_GATE_AUTONOMY] = {
        .id = KERNEL_ROOM_GATE_AUTONOMY,
        .name = "autonomy",
        .syscall_start = SYS_AUTONOMY_ACTION_PROPOSE,
        .syscall_end = SYS_AUTONOMY_TELEMETRY_LAST,
        .min_stability = KERNEL_STABILITY_STABLE,
        .risk = KERNEL_ROOM_GATE_RISK_BOUNDED_CONTROL,
        .completion_ready = false,
        .shared_memory_ready = false,
        .risky_io = false,
    },
    [KERNEL_ROOM_GATE_SLM] = {
        .id = KERNEL_ROOM_GATE_SLM,
        .name = "slm",
        .syscall_start = SYS_SLM_HW_SNAPSHOT,
        .syscall_end = SYS_SLM_PLAN_LIST,
        .min_stability = KERNEL_STABILITY_DEGRADED,
        .risk = KERNEL_ROOM_GATE_RISK_BOUNDED_CONTROL,
        .completion_ready = false,
        .shared_memory_ready = false,
        .risky_io = true,
    },
};

AIOS_STATIC_ASSERT(ARRAY_SIZE(g_gate_table) == KERNEL_ROOM_GATE_COUNT,
    "Kernel Room gate table must stay aligned with kernel_room_gate_id_t");

static uint32_t count_slm_plans(void) {
    slm_plan_list_t plans;
    if (slm_plan_list(&plans) != AIOS_OK) {
        return 0;
    }
    return plans.count;
}

const kernel_room_gate_descriptor_t *kernel_room_gate_descriptors(uint32_t *count) {
    if (count) {
        *count = KERNEL_ROOM_GATE_COUNT;
    }
    return g_gate_table;
}

const char *kernel_room_gate_risk_name(kernel_room_gate_risk_t risk) {
    switch (risk) {
        case KERNEL_ROOM_GATE_RISK_OBSERVE:          return "observe";
        case KERNEL_ROOM_GATE_RISK_BOUNDED_CONTROL:  return "bounded-control";
        case KERNEL_ROOM_GATE_RISK_BOUNDED_DATA:     return "bounded-data";
        case KERNEL_ROOM_GATE_RISK_IO_PATH:          return "io-path";
        default:                                     return "unknown";
    }
}

aios_status_t kernel_room_snapshot_read(kernel_room_snapshot_t *out) {
    kernel_health_summary_t health;
    driver_stack_snapshot_t driver_stack;
    slm_hw_snapshot_t slm_snapshot;
    ai_ring_runtime_snapshot_t ring_runtime;
    const memory_fabric_profile_t *fabric = memory_fabric_profile();
    user_mode_scaffold_info_t user_mode;

    if (!out) {
        return AIOS_ERR_INVAL;
    }

    memset(out, 0, sizeof(*out));
    out->ts_ns = kernel_time_monotonic_ns();

    kernel_health_get_summary(&health);
    out->stability = health.level;
    out->autonomy_allowed = health.autonomy_allowed;
    out->risky_io_allowed = health.risky_io_allowed;
    out->ok_subsystems = health.ok_count;
    out->degraded_subsystems = health.degraded_count;
    out->failed_subsystems = health.failed_count;
    out->unknown_subsystems = health.unknown_count;

    out->memory_domains = memory_fabric_domain_count();
    out->memory_windows = memory_fabric_window_count();
    out->memory_topology = fabric ? (uint8_t)fabric->topology : 0;

    if (driver_model_snapshot_read(&driver_stack) == AIOS_OK) {
        out->driver_entries = driver_stack.count;
        out->driver_ready = driver_stack.ready_count;
        out->driver_degraded = driver_stack.degraded_count;
        out->merged_queue_depth_hint = driver_stack.merged_policy.queue_depth_hint;
        out->merged_poll_budget_hint = driver_stack.merged_policy.poll_budget_hint;
        out->merged_dma_window_kib_hint = driver_stack.merged_policy.dma_window_kib_hint;
    }

    if (slm_snapshot_read(&slm_snapshot) == AIOS_OK) {
        out->agent_nodes = slm_snapshot.agent_tree_nodes;
        out->pipeline_submit_entries = slm_snapshot.pipeline_profile.recommended_submit_ring_entries;
        out->pipeline_completion_entries = slm_snapshot.pipeline_profile.recommended_completion_ring_entries;
    }

    ai_infer_ring_runtime(&ring_runtime);
    out->registered_rings = ring_runtime.registered_rings;
    out->active_rings = ring_runtime.active_rings;
    out->ring_notifies = ring_runtime.total_notifies;

    if (user_mode_scaffold_info(&user_mode) == AIOS_OK) {
        out->user_mode_ready = user_mode.ready;
    } else {
        out->user_mode_ready = user_mode_scaffold_ready();
    }

    out->slm_plan_count = count_slm_plans();
    out->gate_count = KERNEL_ROOM_GATE_COUNT;
    return AIOS_OK;
}

void kernel_room_dump(void) {
    kernel_room_snapshot_t snapshot;
    uint32_t gate_count = 0;
    const kernel_room_gate_descriptor_t *gates = kernel_room_gate_descriptors(&gate_count);
    uint32_t stable_only = 0;
    uint32_t completion = 0;
    uint32_t shared = 0;
    uint32_t risky_io = 0;
    uint32_t observe = 0;
    uint32_t control = 0;
    uint32_t data = 0;

    if (kernel_room_snapshot_read(&snapshot) != AIOS_OK) {
        return;
    }

    for (uint32_t i = 0; i < gate_count; i++) {
        const kernel_room_gate_descriptor_t *gate = &gates[i];
        if (gate->min_stability == KERNEL_STABILITY_STABLE) {
            stable_only++;
        }
        if (gate->completion_ready) {
            completion++;
        }
        if (gate->shared_memory_ready) {
            shared++;
        }
        if (gate->risky_io) {
            risky_io++;
        }
        switch (gate->risk) {
            case KERNEL_ROOM_GATE_RISK_OBSERVE:
                observe++;
                break;
            case KERNEL_ROOM_GATE_RISK_BOUNDED_CONTROL:
                control++;
                break;
            case KERNEL_ROOM_GATE_RISK_BOUNDED_DATA:
                data++;
                break;
            case KERNEL_ROOM_GATE_RISK_IO_PATH:
            default:
                break;
        }
    }

    kprintf("\n=== Kernel Room ===\n");
    kprintf("Snapshot: stability=%s ok=%u degraded=%u failed=%u unknown=%u topology=%s domains=%u windows=%u drivers=%u/%u plans=%u nodes=%u rings=%u active=%u user=%u\n",
        (uint64_t)(uintptr_t)kernel_stability_name(snapshot.stability),
        (uint64_t)snapshot.ok_subsystems,
        (uint64_t)snapshot.degraded_subsystems,
        (uint64_t)snapshot.failed_subsystems,
        (uint64_t)snapshot.unknown_subsystems,
        (uint64_t)(uintptr_t)memory_fabric_topology_name((memory_fabric_topology_t)snapshot.memory_topology),
        (uint64_t)snapshot.memory_domains,
        (uint64_t)snapshot.memory_windows,
        (uint64_t)snapshot.driver_ready,
        (uint64_t)snapshot.driver_entries,
        (uint64_t)snapshot.slm_plan_count,
        (uint64_t)snapshot.agent_nodes,
        (uint64_t)snapshot.registered_rings,
        (uint64_t)snapshot.active_rings,
        (uint64_t)snapshot.user_mode_ready);
    kprintf("Gates: total=%u stable_only=%u completion=%u shared=%u risky_io=%u observe=%u control=%u data=%u\n",
        (uint64_t)gate_count,
        (uint64_t)stable_only,
        (uint64_t)completion,
        (uint64_t)shared,
        (uint64_t)risky_io,
        (uint64_t)observe,
        (uint64_t)control,
        (uint64_t)data);
    kprintf("===================\n");

    serial_printf("[ROOM] snapshot stability=%s ok=%u degraded=%u failed=%u unknown=%u topology=%s domains=%u windows=%u drivers=%u/%u plans=%u nodes=%u rings=%u active=%u user=%u\n",
        (uint64_t)(uintptr_t)kernel_stability_name(snapshot.stability),
        (uint64_t)snapshot.ok_subsystems,
        (uint64_t)snapshot.degraded_subsystems,
        (uint64_t)snapshot.failed_subsystems,
        (uint64_t)snapshot.unknown_subsystems,
        (uint64_t)(uintptr_t)memory_fabric_topology_name((memory_fabric_topology_t)snapshot.memory_topology),
        (uint64_t)snapshot.memory_domains,
        (uint64_t)snapshot.memory_windows,
        (uint64_t)snapshot.driver_ready,
        (uint64_t)snapshot.driver_entries,
        (uint64_t)snapshot.slm_plan_count,
        (uint64_t)snapshot.agent_nodes,
        (uint64_t)snapshot.registered_rings,
        (uint64_t)snapshot.active_rings,
        (uint64_t)snapshot.user_mode_ready);
    serial_printf("[ROOM] gates total=%u stable_only=%u completion=%u shared=%u risky_io=%u observe=%u control=%u data=%u\n",
        (uint64_t)gate_count,
        (uint64_t)stable_only,
        (uint64_t)completion,
        (uint64_t)shared,
        (uint64_t)risky_io,
        (uint64_t)observe,
        (uint64_t)control,
        (uint64_t)data);
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
