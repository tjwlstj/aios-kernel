/*
 * AIOS Kernel - AI Pressure Tracker
 *
 * Versioned, read-only pressure evidence. This surface ranks observable
 * bottlenecks; it does not migrate tasks, alter budgets, or bypass gates.
 */

#ifndef _AIOS_RUNTIME_AI_PRESSURE_H
#define _AIOS_RUNTIME_AI_PRESSURE_H

#include <kernel/types.h>
#include <sched/ai_sched.h>

#define AI_PRESSURE_SCHEMA_VERSION 1U
#define AI_PRESSURE_SCORE_SCALE    1024U
#define AI_PRESSURE_MAX_LEVELS     4U
#define AI_PRESSURE_ACTIVE_LEVELS  2U
#define AI_PRESSURE_PLANE_NONE     0xFFFFFFFFU

#define AI_PRESSURE_SOURCE_SCHED_EXACT       ((uint32_t)BIT(0))
#define AI_PRESSURE_SOURCE_MEMORY_EXACT      ((uint32_t)BIT(1))
#define AI_PRESSURE_SOURCE_POLICY_CUMULATIVE ((uint32_t)BIT(2))

/*
 * Public IDs are append-only. The first slice activates system -> plane;
 * domain -> entity cells fit inside MAX_LEVELS but remain future work.
 */
typedef enum {
    AI_PRESSURE_PLANE_SCHED = 0,
    AI_PRESSURE_PLANE_MEMORY = 1,
    AI_PRESSURE_PLANE_POLICY = 2,
    AI_PRESSURE_PLANE_COUNT = 3,
} ai_pressure_plane_t;

AIOS_STATIC_ASSERT(AI_PRESSURE_PLANE_COUNT == 3,
    "AI pressure plane IDs are append-only; update all consumers together");

/*
 * Gate eligibility remains a bitmap intersection performed after pressure
 * observation/ranking. Keeping this separate prevents a denied destination
 * from being disguised as a low-load destination.
 */
typedef struct {
    uint64_t online_mask;
    uint64_t affinity_mask;
    uint64_t policy_gate_mask;
    uint64_t health_mask;
    uint64_t budget_mask;
} ai_pressure_gate_masks_t;

typedef struct {
    uint32_t schema_version;
    uint32_t struct_size;
    uint32_t observation_only;
    uint32_t gate_filter_separate;
    uint32_t max_levels;
    uint32_t active_levels;
    uint32_t plane_count;
    uint32_t source_flags;
    uint64_t sampled_at_ns;
    uint64_t sample_sequence;

    uint32_t plane_score_q10[AI_PRESSURE_PLANE_COUNT];
    uint32_t hotspot_plane;
    uint32_t hotspot_valid;
    uint32_t hotspot_score_q10;
    uint32_t plane_concentration_q10;

    uint32_t scheduler_queue_concentration_q10;
    uint32_t queued_tasks;
    uint32_t runnable_tasks;
    uint32_t largest_queue;
    uint32_t largest_queue_policy;
    uint32_t queue_sizes[SCHED_POLICY_COUNT];

    uint32_t active_domains;
    uint32_t active_windows;
    uint32_t shared_windows;
    uint32_t participant_links;
    uint32_t writer_pairs;
    uint32_t read_write_pairs;
    uint32_t max_fanout;
    uint64_t total_budget_bytes;
    uint64_t shared_bytes;
    uint64_t weighted_shared_bytes;

    uint32_t active_nodes;
    uint32_t gate_evaluations;
    uint32_t gate_denies;
    uint32_t gate_health_blocks;
} ai_pressure_snapshot_t;

AIOS_STATIC_ASSERT(AI_PRESSURE_ACTIVE_LEVELS <= AI_PRESSURE_MAX_LEVELS,
    "Active pressure hierarchy cannot exceed its fixed expansion depth");
AIOS_STATIC_ASSERT(sizeof(ai_pressure_snapshot_t) <= 512U,
    "Pressure snapshot must remain bounded; version before expanding it");

aios_status_t ai_pressure_init(void);
bool ai_pressure_ready(void);
aios_status_t ai_pressure_read(ai_pressure_snapshot_t *out);
uint64_t ai_pressure_eligible_mask(const ai_pressure_gate_masks_t *masks);
const char *ai_pressure_plane_name(uint32_t plane_id);

#endif /* _AIOS_RUNTIME_AI_PRESSURE_H */
