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

typedef struct {
    heap_stats_t heap;
    mem_stats_t tensor;
    memory_fabric_pressure_snapshot_t fabric;
    ai_ring_runtime_snapshot_t rings;
    ai_sched_queue_snapshot_t sched;
} ai_resource_sources_t;

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

static void resource_snapshot_build(
    ai_resource_snapshot_t *out,
    const ai_resource_sources_t *sources,
    uint64_t sampled_at_ns,
    uint64_t sample_sequence
) {
    const uint32_t limit_used_flags =
        AI_RESOURCE_ENTRY_LIMIT_VALID | AI_RESOURCE_ENTRY_USED_VALID;

    memset(out, 0, sizeof(*out));
    out->schema_version = AI_RESOURCE_SCHEMA_VERSION;
    out->struct_size = (uint32_t)sizeof(*out);
    out->observation_only = 1U;
    out->kind_count = AI_RESOURCE_KIND_COUNT;
    out->unit_count = AI_RESOURCE_UNIT_COUNT;
    out->entry_count = AI_RESOURCE_KIND_COUNT;
    out->entry_capacity = AI_RESOURCE_LEDGER_CAPACITY;
    out->source_flags = AI_RESOURCE_SOURCE_ALL_CURRENT;
    out->sampled_at_ns = sampled_at_ns;
    out->sample_sequence = sample_sequence;

    resource_entry_set(
        &out->entries[AI_RESOURCE_KIND_KERNEL_HEAP],
        AI_RESOURCE_KIND_KERNEL_HEAP,
        AI_RESOURCE_UNIT_BYTES,
        limit_used_flags,
        (uint64_t)sources->heap.total,
        (uint64_t)sources->heap.used,
        0,
        sampled_at_ns
    );
    resource_entry_set(
        &out->entries[AI_RESOURCE_KIND_TENSOR_POOL],
        AI_RESOURCE_KIND_TENSOR_POOL,
        AI_RESOURCE_UNIT_BYTES,
        limit_used_flags | AI_RESOURCE_ENTRY_HIGH_WATER_VALID,
        sources->tensor.total_memory,
        sources->tensor.used_memory,
        sources->tensor.peak_usage,
        sampled_at_ns
    );
    resource_entry_set(
        &out->entries[AI_RESOURCE_KIND_MEMORY_FABRIC_WINDOWS],
        AI_RESOURCE_KIND_MEMORY_FABRIC_WINDOWS,
        AI_RESOURCE_UNIT_ITEMS,
        limit_used_flags,
        MEMORY_FABRIC_MAX_WINDOWS,
        sources->fabric.active_windows,
        0,
        sampled_at_ns
    );
    resource_entry_set(
        &out->entries[AI_RESOURCE_KIND_INFERENCE_RING_REGISTRATIONS],
        AI_RESOURCE_KIND_INFERENCE_RING_REGISTRATIONS,
        AI_RESOURCE_UNIT_ITEMS,
        limit_used_flags,
        AI_INFER_RING_CAPACITY,
        sources->rings.registered_rings,
        0,
        sampled_at_ns
    );
    resource_entry_set(
        &out->entries[AI_RESOURCE_KIND_SCHEDULER_RUNNABLE],
        AI_RESOURCE_KIND_SCHEDULER_RUNNABLE,
        AI_RESOURCE_UNIT_ITEMS,
        limit_used_flags,
        MAX_AI_TASKS,
        sources->sched.runnable_tasks,
        0,
        sampled_at_ns
    );
}

static aios_status_t resource_sources_read(ai_resource_snapshot_t *out) {
    ai_resource_sources_t sources;
    uint64_t sampled_at_ns;

    memset(&sources, 0, sizeof(sources));
    if (memory_fabric_pressure_read(&sources.fabric) != AIOS_OK ||
        ai_sched_queue_snapshot(&sources.sched) != AIOS_OK) {
        return AIOS_ERR_IO;
    }

    heap_get_stats(&sources.heap);
    tensor_mm_stats(&sources.tensor);
    ai_infer_ring_runtime(&sources.rings);
    sampled_at_ns = kernel_time_monotonic_ns();
    resource_snapshot_build(
        out,
        &sources,
        sampled_at_ns,
        __atomic_add_fetch(&g_sample_sequence, 1ULL, __ATOMIC_RELAXED)
    );
    return AIOS_OK;
}

static bool resource_entry_owner_contract_valid(
    const ai_resource_entry_t *entry
) {
    uint32_t owner_valid_flags =
        entry->valid_flags & AI_RESOURCE_ENTRY_OWNER_VALID_MASK;

    if ((entry->valid_flags & ~AI_RESOURCE_ENTRY_ALL_FLAGS) != 0U ||
        ((entry->valid_flags & AI_RESOURCE_ENTRY_OWNER_UNATTRIBUTED) != 0U &&
         owner_valid_flags != 0U)) {
        return false;
    }
    if ((entry->valid_flags & AI_RESOURCE_ENTRY_NODE_ID_VALID) == 0U &&
        entry->node_id != AI_RESOURCE_OWNER_NONE) {
        return false;
    }
    if ((entry->valid_flags & AI_RESOURCE_ENTRY_TASK_ID_VALID) == 0U &&
        entry->task_id != (task_id_t)AI_RESOURCE_OWNER_NONE) {
        return false;
    }
    if ((entry->valid_flags & AI_RESOURCE_ENTRY_MODEL_ID_VALID) == 0U &&
        entry->model_id != (model_id_t)AI_RESOURCE_OWNER_NONE) {
        return false;
    }
    if ((entry->valid_flags & AI_RESOURCE_ENTRY_RING_ID_VALID) == 0U &&
        entry->ring_id != AI_RESOURCE_OWNER_NONE) {
        return false;
    }
    return true;
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
        snapshot->sampled_at_ns == 0U ||
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
            entry->reserved0 != 0U ||
            entry->last_observed_ns != snapshot->sampled_at_ns ||
            !resource_entry_owner_contract_valid(entry)) {
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
            if (entry->high_water < entry->used ||
                entry->high_water > entry->limit) {
                return false;
            }
        } else if (entry->high_water != 0U) {
            return false;
        }
        if ((entry->valid_flags & AI_RESOURCE_ENTRY_DENIED_VALID) != 0) {
            denied_kinds++;
        } else if (entry->denied != 0U) {
            return false;
        }
    }

    {
        ai_resource_entry_t empty_entry;
        memset(&empty_entry, 0, sizeof(empty_entry));
        for (uint32_t i = snapshot->entry_count;
             i < snapshot->entry_capacity; i++) {
            if (memcmp(&snapshot->entries[i], &empty_entry,
                       sizeof(empty_entry)) != 0) {
                return false;
            }
        }
    }

    return limit_kinds == 5U &&
           used_kinds == 5U &&
           high_water_kinds == 1U &&
           denied_kinds == 0U &&
           !ai_resource_kind_valid(AI_RESOURCE_KIND_COUNT) &&
           !ai_resource_unit_valid(AI_RESOURCE_UNIT_COUNT);
}

static bool resource_snapshot_matches_sources(
    const ai_resource_snapshot_t *snapshot,
    const ai_resource_sources_t *sources
) {
    const ai_resource_entry_t *heap =
        &snapshot->entries[AI_RESOURCE_KIND_KERNEL_HEAP];
    const ai_resource_entry_t *tensor =
        &snapshot->entries[AI_RESOURCE_KIND_TENSOR_POOL];
    const ai_resource_entry_t *fabric =
        &snapshot->entries[AI_RESOURCE_KIND_MEMORY_FABRIC_WINDOWS];
    const ai_resource_entry_t *rings =
        &snapshot->entries[AI_RESOURCE_KIND_INFERENCE_RING_REGISTRATIONS];
    const ai_resource_entry_t *sched =
        &snapshot->entries[AI_RESOURCE_KIND_SCHEDULER_RUNNABLE];

    return heap->limit == (uint64_t)sources->heap.total &&
           heap->used == (uint64_t)sources->heap.used &&
           tensor->limit == sources->tensor.total_memory &&
           tensor->used == sources->tensor.used_memory &&
           tensor->high_water == sources->tensor.peak_usage &&
           fabric->limit == MEMORY_FABRIC_MAX_WINDOWS &&
           fabric->used == sources->fabric.active_windows &&
           rings->limit == AI_INFER_RING_CAPACITY &&
           rings->used == sources->rings.registered_rings &&
           sched->limit == MAX_AI_TASKS &&
           sched->used == sources->sched.runnable_tasks;
}

aios_status_t ai_resource_init(void) {
    ai_resource_snapshot_t snapshot;
    ai_resource_snapshot_t invalid_snapshot;
    ai_resource_snapshot_t mapping_snapshot;
    ai_resource_sources_t synthetic_sources;
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

    memset(&synthetic_sources, 0, sizeof(synthetic_sources));
    synthetic_sources.heap.total = 1009U;
    synthetic_sources.heap.used = 109U;
    synthetic_sources.tensor.total_memory = 2003U;
    synthetic_sources.tensor.used_memory = 211U;
    synthetic_sources.tensor.peak_usage = 307U;
    synthetic_sources.fabric.active_windows = 3U;
    synthetic_sources.rings.registered_rings = 7U;
    synthetic_sources.sched.runnable_tasks = 11U;
    resource_snapshot_build(
        &mapping_snapshot, &synthetic_sources, 1234567U, 41U
    );
    if (!resource_snapshot_contract_valid(&mapping_snapshot) ||
        !resource_snapshot_matches_sources(
            &mapping_snapshot, &synthetic_sources
        )) {
        g_resource_ready = false;
        serial_write("[RESOURCE] ledger selftest FAIL mapping=0\n");
        return AIOS_ERR_IO;
    }

    invalid_snapshot = snapshot;
    invalid_snapshot.entry_count = AI_RESOURCE_LEDGER_CAPACITY + 1U;
    if (resource_snapshot_contract_valid(&invalid_snapshot)) {
        g_resource_ready = false;
        serial_write("[RESOURCE] ledger selftest FAIL bounds=0\n");
        return AIOS_ERR_IO;
    }

    invalid_snapshot = snapshot;
    invalid_snapshot.entries[AI_RESOURCE_KIND_KERNEL_HEAP].valid_flags |=
        AI_RESOURCE_ENTRY_NODE_ID_VALID;
    if (resource_snapshot_contract_valid(&invalid_snapshot)) {
        g_resource_ready = false;
        serial_write("[RESOURCE] ledger selftest FAIL owner_flags=0\n");
        return AIOS_ERR_IO;
    }

    invalid_snapshot = snapshot;
    invalid_snapshot.entries[AI_RESOURCE_KIND_TENSOR_POOL].high_water =
        invalid_snapshot.entries[AI_RESOURCE_KIND_TENSOR_POOL].limit + 1U;
    if (resource_snapshot_contract_valid(&invalid_snapshot)) {
        g_resource_ready = false;
        serial_write("[RESOURCE] ledger selftest FAIL high_water=0\n");
        return AIOS_ERR_IO;
    }

    invalid_snapshot = snapshot;
    invalid_snapshot.entries[AI_RESOURCE_KIND_COUNT].used = 1U;
    if (resource_snapshot_contract_valid(&invalid_snapshot)) {
        g_resource_ready = false;
        serial_write("[RESOURCE] ledger selftest FAIL tail_zero=0\n");
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
