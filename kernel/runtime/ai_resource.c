/*
 * AIOS Kernel - Read-Only AI Resource Ledger
 *
 * CURRENT scope: five unattributed aggregate rows sourced from existing
 * kernel observers. No control path consumes this snapshot.
 */

#include <runtime/ai_resource.h>
#include <runtime/ai_ring.h>
#include <drivers/serial.h>
#include <drivers/vga.h>
#include <kernel/time.h>
#include <lib/string.h>
#include <mm/heap.h>
#include <mm/memory_fabric.h>
#include <mm/tensor_mm.h>
#include <sched/ai_sched.h>

static bool g_resource_ready = false;
static uint64_t g_sample_sequence = 0;

static void resource_entry_set(
    ai_resource_entry_t *entry,
    ai_resource_kind_t kind,
    ai_resource_unit_t unit,
    uint32_t valid_flags,
    uint64_t limit,
    uint64_t used,
    uint64_t high_water,
    uint64_t sampled_at_ns
) {
    memset(entry, 0, sizeof(*entry));
    entry->kind = (uint32_t)kind;
    entry->unit = (uint32_t)unit;
    entry->valid_flags = valid_flags |
        AI_RESOURCE_ENTRY_OWNER_UNATTRIBUTED;
    entry->node_id = AI_RESOURCE_OWNER_NONE;
    entry->task_id = (task_id_t)AI_RESOURCE_OWNER_NONE;
    entry->model_id = (model_id_t)AI_RESOURCE_OWNER_NONE;
    entry->ring_id = AI_RESOURCE_OWNER_NONE;
    entry->limit = limit;
    entry->used = used;
    entry->high_water = high_water;
    entry->denied = 0;
    entry->last_observed_ns = sampled_at_ns;
}

static aios_status_t resource_sources_read(ai_resource_snapshot_t *out) {
    heap_stats_t heap;
    mem_stats_t tensor;
    memory_fabric_pressure_snapshot_t fabric;
    ai_ring_runtime_snapshot_t rings;
    ai_sched_queue_snapshot_t sched;
    uint64_t sampled_at_ns;
    const uint32_t limit_used_flags =
        AI_RESOURCE_ENTRY_LIMIT_VALID | AI_RESOURCE_ENTRY_USED_VALID;

    if (memory_fabric_pressure_read(&fabric) != AIOS_OK ||
        ai_sched_queue_snapshot(&sched) != AIOS_OK) {
        return AIOS_ERR_IO;
    }

    heap_get_stats(&heap);
    tensor_mm_stats(&tensor);
    ai_infer_ring_runtime(&rings);
    sampled_at_ns = kernel_time_monotonic_ns();

    memset(out, 0, sizeof(*out));
    out->schema_version = AI_RESOURCE_SCHEMA_VERSION;
    out->struct_size = (uint32_t)sizeof(*out);
    out->observation_only = 1U;
    out->kind_count = AI_RESOURCE_KIND_COUNT;
    out->unit_count = AI_RESOURCE_UNIT_COUNT;
    out->entry_count = AI_RESOURCE_KIND_COUNT;
    out->entry_capacity = AI_RESOURCE_LEDGER_CAPACITY;
    out->source_flags = AI_RESOURCE_SOURCE_HEAP_EXACT |
                        AI_RESOURCE_SOURCE_TENSOR_SINGLE_BSP |
                        AI_RESOURCE_SOURCE_FABRIC_EXACT |
                        AI_RESOURCE_SOURCE_RING_EXACT |
                        AI_RESOURCE_SOURCE_SCHED_SINGLE_BSP;
    out->sampled_at_ns = sampled_at_ns;
    out->sample_sequence = __atomic_add_fetch(
        &g_sample_sequence, 1ULL, __ATOMIC_RELAXED
    );

    resource_entry_set(
        &out->entries[AI_RESOURCE_KIND_KERNEL_HEAP],
        AI_RESOURCE_KIND_KERNEL_HEAP,
        AI_RESOURCE_UNIT_BYTES,
        limit_used_flags,
        (uint64_t)heap.total,
        (uint64_t)heap.used,
        0,
        sampled_at_ns
    );
    resource_entry_set(
        &out->entries[AI_RESOURCE_KIND_TENSOR_POOL],
        AI_RESOURCE_KIND_TENSOR_POOL,
        AI_RESOURCE_UNIT_BYTES,
        limit_used_flags | AI_RESOURCE_ENTRY_HIGH_WATER_VALID,
        tensor.total_memory,
        tensor.used_memory,
        tensor.peak_usage,
        sampled_at_ns
    );
    resource_entry_set(
        &out->entries[AI_RESOURCE_KIND_MEMORY_FABRIC_WINDOWS],
        AI_RESOURCE_KIND_MEMORY_FABRIC_WINDOWS,
        AI_RESOURCE_UNIT_ITEMS,
        limit_used_flags,
        MEMORY_FABRIC_MAX_WINDOWS,
        fabric.active_windows,
        0,
        sampled_at_ns
    );
    resource_entry_set(
        &out->entries[AI_RESOURCE_KIND_INFERENCE_RING_REGISTRATIONS],
        AI_RESOURCE_KIND_INFERENCE_RING_REGISTRATIONS,
        AI_RESOURCE_UNIT_ITEMS,
        limit_used_flags,
        AI_INFER_RING_CAPACITY,
        rings.registered_rings,
        0,
        sampled_at_ns
    );
    resource_entry_set(
        &out->entries[AI_RESOURCE_KIND_SCHEDULER_RUNNABLE],
        AI_RESOURCE_KIND_SCHEDULER_RUNNABLE,
        AI_RESOURCE_UNIT_ITEMS,
        limit_used_flags,
        MAX_AI_TASKS,
        sched.runnable_tasks,
        0,
        sampled_at_ns
    );
    return AIOS_OK;
}

static bool resource_snapshot_contract_valid(
    const ai_resource_snapshot_t *snapshot
) {
    uint32_t limit_kinds = 0;
    uint32_t used_kinds = 0;
    uint32_t high_water_kinds = 0;
    uint32_t denied_kinds = 0;

    if (!snapshot ||
        snapshot->schema_version != AI_RESOURCE_SCHEMA_VERSION ||
        snapshot->struct_size != sizeof(*snapshot) ||
        snapshot->observation_only != 1U ||
        snapshot->kind_count != AI_RESOURCE_KIND_COUNT ||
        snapshot->unit_count != AI_RESOURCE_UNIT_COUNT ||
        snapshot->entry_count != AI_RESOURCE_KIND_COUNT ||
        snapshot->entry_capacity != AI_RESOURCE_LEDGER_CAPACITY ||
        snapshot->source_flags != AI_RESOURCE_SOURCE_ALL_CURRENT ||
        snapshot->sample_sequence == 0U) {
        return false;
    }

    for (uint32_t i = 0; i < snapshot->entry_count; i++) {
        const ai_resource_entry_t *entry = &snapshot->entries[i];
        uint32_t expected_unit = i <= AI_RESOURCE_KIND_TENSOR_POOL
            ? AI_RESOURCE_UNIT_BYTES : AI_RESOURCE_UNIT_ITEMS;
        uint32_t expected_valid_flags =
            AI_RESOURCE_ENTRY_LIMIT_VALID |
            AI_RESOURCE_ENTRY_USED_VALID |
            AI_RESOURCE_ENTRY_OWNER_UNATTRIBUTED;
        if (i == AI_RESOURCE_KIND_TENSOR_POOL) {
            expected_valid_flags |= AI_RESOURCE_ENTRY_HIGH_WATER_VALID;
        }
        if (entry->kind != i ||
            !ai_resource_kind_valid(entry->kind) ||
            !ai_resource_unit_valid(entry->unit) ||
            entry->unit != expected_unit ||
            entry->valid_flags != expected_valid_flags ||
            entry->node_id != AI_RESOURCE_OWNER_NONE ||
            entry->task_id != AI_RESOURCE_OWNER_NONE ||
            entry->model_id != AI_RESOURCE_OWNER_NONE ||
            entry->ring_id != AI_RESOURCE_OWNER_NONE) {
            return false;
        }
        if ((entry->valid_flags & AI_RESOURCE_ENTRY_LIMIT_VALID) != 0) {
            limit_kinds++;
        }
        if ((entry->valid_flags & AI_RESOURCE_ENTRY_USED_VALID) != 0) {
            used_kinds++;
        }
        if ((entry->valid_flags &
             (AI_RESOURCE_ENTRY_LIMIT_VALID | AI_RESOURCE_ENTRY_USED_VALID)) ==
            (AI_RESOURCE_ENTRY_LIMIT_VALID | AI_RESOURCE_ENTRY_USED_VALID) &&
            entry->used > entry->limit) {
            return false;
        }
        if ((entry->valid_flags & AI_RESOURCE_ENTRY_HIGH_WATER_VALID) != 0) {
            high_water_kinds++;
            if (entry->high_water < entry->used) {
                return false;
            }
        }
        if ((entry->valid_flags & AI_RESOURCE_ENTRY_DENIED_VALID) != 0) {
            denied_kinds++;
        }
    }

    return limit_kinds == 5U &&
           used_kinds == 5U &&
           high_water_kinds == 1U &&
           denied_kinds == 0U &&
           !ai_resource_kind_valid(AI_RESOURCE_KIND_COUNT) &&
           !ai_resource_unit_valid(AI_RESOURCE_UNIT_COUNT);
}

aios_status_t ai_resource_init(void) {
    ai_resource_snapshot_t snapshot;
    ai_resource_snapshot_t invalid_snapshot;
    aios_status_t status;

    g_resource_ready = false;
    g_sample_sequence = 0;

    if (ai_resource_read(NULL) != AIOS_ERR_INVAL ||
        ai_resource_read(&snapshot) != AIOS_ERR_INVAL) {
        serial_write("[RESOURCE] ledger selftest FAIL precondition=0\n");
        return AIOS_ERR_IO;
    }

    g_resource_ready = true;
    status = ai_resource_read(&snapshot);
    if (status != AIOS_OK || !resource_snapshot_contract_valid(&snapshot)) {
        g_resource_ready = false;
        serial_write("[RESOURCE] ledger selftest FAIL invariant=0\n");
        return status == AIOS_OK ? AIOS_ERR_IO : status;
    }

    invalid_snapshot = snapshot;
    invalid_snapshot.entry_count = AI_RESOURCE_LEDGER_CAPACITY + 1U;
    if (resource_snapshot_contract_valid(&invalid_snapshot)) {
        g_resource_ready = false;
        serial_write("[RESOURCE] ledger selftest FAIL bounds=0\n");
        return AIOS_ERR_IO;
    }

    serial_write("[RESOURCE] ledger selftest PASS schema=1 kinds=5 units=2 entries=5 capacity=8 source_flags=31 limit_kinds=5 used_kinds=5 high_water_kinds=1 denied_kinds=0 owners_unattributed=1 observation_only=1\n");
    kprintf("[RESOURCE] ledger ready schema=1 observation_only=1\n");
    return AIOS_OK;
}

bool ai_resource_ready(void) {
    return g_resource_ready;
}

aios_status_t ai_resource_read(ai_resource_snapshot_t *out) {
    if (!out || !g_resource_ready) {
        return AIOS_ERR_INVAL;
    }
    return resource_sources_read(out);
}

const char *ai_resource_kind_name(uint32_t kind) {
    switch (kind) {
        case AI_RESOURCE_KIND_KERNEL_HEAP:       return "kernel-heap";
        case AI_RESOURCE_KIND_TENSOR_POOL:       return "tensor-pool";
        case AI_RESOURCE_KIND_MEMORY_FABRIC_WINDOWS:
            return "memory-fabric-windows";
        case AI_RESOURCE_KIND_INFERENCE_RING_REGISTRATIONS:
            return "inference-ring-registrations";
        case AI_RESOURCE_KIND_SCHEDULER_RUNNABLE:
            return "scheduler-runnable";
        default:                                 return "unknown";
    }
}

const char *ai_resource_unit_name(uint32_t unit) {
    switch (unit) {
        case AI_RESOURCE_UNIT_BYTES: return "bytes";
        case AI_RESOURCE_UNIT_ITEMS: return "count";
        default:                     return "unknown";
    }
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
