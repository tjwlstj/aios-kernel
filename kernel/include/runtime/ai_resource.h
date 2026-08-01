/*
 * AIOS Kernel - Read-Only AI Resource Ledger
 *
 * Versioned aggregate resource observations. This surface does not reserve,
 * release, throttle, or otherwise alter allocator/scheduler policy.
 */

#ifndef _AIOS_RUNTIME_AI_RESOURCE_H
#define _AIOS_RUNTIME_AI_RESOURCE_H

#include <kernel/types.h>

#define AI_RESOURCE_SCHEMA_VERSION   1U
#define AI_RESOURCE_LEDGER_CAPACITY  8U
#define AI_RESOURCE_OWNER_NONE       0U

#define AI_RESOURCE_SOURCE_HEAP_EXACT          ((uint32_t)BIT(0))
#define AI_RESOURCE_SOURCE_TENSOR_SINGLE_BSP   ((uint32_t)BIT(1))
#define AI_RESOURCE_SOURCE_FABRIC_EXACT        ((uint32_t)BIT(2))
#define AI_RESOURCE_SOURCE_RING_EXACT          ((uint32_t)BIT(3))
#define AI_RESOURCE_SOURCE_SCHED_SINGLE_BSP    ((uint32_t)BIT(4))
#define AI_RESOURCE_SOURCE_ALL_CURRENT       ((uint32_t)0x1FU)

AIOS_STATIC_ASSERT(
    (AI_RESOURCE_SOURCE_HEAP_EXACT |
     AI_RESOURCE_SOURCE_TENSOR_SINGLE_BSP |
     AI_RESOURCE_SOURCE_FABRIC_EXACT |
     AI_RESOURCE_SOURCE_RING_EXACT |
     AI_RESOURCE_SOURCE_SCHED_SINGLE_BSP) == AI_RESOURCE_SOURCE_ALL_CURRENT,
    "Current resource source flags must stay explicit and complete");

#define AI_RESOURCE_ENTRY_LIMIT_VALID        ((uint32_t)BIT(0))
#define AI_RESOURCE_ENTRY_USED_VALID         ((uint32_t)BIT(1))
#define AI_RESOURCE_ENTRY_HIGH_WATER_VALID   ((uint32_t)BIT(2))
#define AI_RESOURCE_ENTRY_DENIED_VALID       ((uint32_t)BIT(3))
#define AI_RESOURCE_ENTRY_OWNER_UNATTRIBUTED ((uint32_t)BIT(4))
#define AI_RESOURCE_ENTRY_ALL_FLAGS          ((uint32_t)0x1FU)

/* Public IDs are append-only. Only currently observed sources receive IDs. */
typedef enum {
    AI_RESOURCE_KIND_KERNEL_HEAP = 0,
    AI_RESOURCE_KIND_TENSOR_POOL = 1,
    AI_RESOURCE_KIND_MEMORY_FABRIC_WINDOWS = 2,
    AI_RESOURCE_KIND_INFERENCE_RING_REGISTRATIONS = 3,
    AI_RESOURCE_KIND_SCHEDULER_RUNNABLE = 4,
    AI_RESOURCE_KIND_COUNT = 5,
} ai_resource_kind_t;

AIOS_STATIC_ASSERT(AI_RESOURCE_KIND_COUNT == 5,
    "AI resource kind IDs are append-only; update all consumers together");
AIOS_STATIC_ASSERT(AI_RESOURCE_KIND_COUNT <= AI_RESOURCE_LEDGER_CAPACITY,
    "AI resource kinds must fit the fixed ledger capacity");

typedef enum {
    AI_RESOURCE_UNIT_BYTES = 0,
    AI_RESOURCE_UNIT_ITEMS = 1,
    AI_RESOURCE_UNIT_COUNT = 2,
} ai_resource_unit_t;

AIOS_STATIC_ASSERT(AI_RESOURCE_UNIT_COUNT == 2,
    "AI resource unit IDs are append-only; update all consumers together");

typedef struct {
    uint32_t kind;
    uint32_t unit;
    uint32_t valid_flags;
    uint32_t reserved0;

    /* Zero means NONE/UNATTRIBUTED. Current rows are aggregate-only. */
    uint32_t node_id;
    task_id_t task_id;
    model_id_t model_id;
    uint32_t ring_id;

    uint64_t limit;
    uint64_t used;
    uint64_t high_water;
    uint64_t denied;
    uint64_t last_observed_ns;
} ai_resource_entry_t;

/*
 * Heap, fabric, and ring readers are internally synchronized; scheduler state
 * is copied with local IRQs masked, and tensor statistics rely on the current
 * single-BSP execution model. All five sources are sampled sequentially, so
 * the combined snapshot is cross-source best-effort, not an atomic transaction.
 */
typedef struct {
    uint32_t schema_version;
    uint32_t struct_size;
    uint32_t observation_only;
    uint32_t kind_count;
    uint32_t unit_count;
    uint32_t entry_count;
    uint32_t entry_capacity;
    uint32_t source_flags;
    uint64_t sampled_at_ns;
    uint64_t sample_sequence;
    ai_resource_entry_t entries[AI_RESOURCE_LEDGER_CAPACITY];
} ai_resource_snapshot_t;

AIOS_STATIC_ASSERT(sizeof(ai_resource_snapshot_t) <= 1024U,
    "Resource snapshot must remain bounded; version before expanding it");

static inline bool ai_resource_kind_valid(uint32_t kind) {
    return kind < AI_RESOURCE_KIND_COUNT;
}

static inline bool ai_resource_unit_valid(uint32_t unit) {
    return unit < AI_RESOURCE_UNIT_COUNT;
}

aios_status_t ai_resource_init(void);
bool ai_resource_ready(void);
aios_status_t ai_resource_read(ai_resource_snapshot_t *out);
const char *ai_resource_kind_name(uint32_t kind);
const char *ai_resource_unit_name(uint32_t unit);

#endif /* _AIOS_RUNTIME_AI_RESOURCE_H */
