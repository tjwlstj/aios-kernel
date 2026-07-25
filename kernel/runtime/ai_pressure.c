/*
 * AIOS Kernel - AI Pressure Tracker
 *
 * CURRENT scope:
 * - exact AI workload queue occupancy on the current single BSP
 * - exact Memory Fabric reader/writer overlap from fixed arrays
 * - cumulative NodeBit denial ratio
 * - a two-level system -> plane bottleneck summary
 *
 * No apply path consumes this snapshot yet.
 */

#include <runtime/ai_pressure.h>
#include <runtime/nodebit.h>
#include <mm/memory_fabric.h>
#include <kernel/time.h>
#include <drivers/serial.h>
#include <drivers/vga.h>
#include <lib/string.h>

typedef struct {
    uint32_t hotspot_plane;
    uint32_t hotspot_valid;
    uint32_t hotspot_score_q10;
    uint32_t concentration_q10;
} pressure_vector_summary_t;

static bool g_pressure_ready = false;
static uint64_t g_sample_sequence = 0;

/*
 * Return floor(numerator / denominator * 1024) without overflowing a 64-bit
 * intermediate. SCORE_SCALE is 2^10, so ten binary long-division steps are
 * sufficient after clamping ratios >= 1.
 */
static uint32_t ratio_q10(uint64_t numerator, uint64_t denominator) {
    uint32_t result = 0;
    uint64_t remainder;

    if (denominator == 0 || numerator == 0) {
        return 0;
    }
    if (numerator >= denominator) {
        return AI_PRESSURE_SCORE_SCALE;
    }

    remainder = numerator;
    for (uint32_t bit = 0; bit < 10U; bit++) {
        result <<= 1;
        if (remainder >= denominator - remainder) {
            remainder -= denominator - remainder;
            result |= 1U;
        } else {
            remainder += remainder;
        }
    }
    return result;
}

static void summarize_vector(
    const uint32_t scores[AI_PRESSURE_PLANE_COUNT],
    pressure_vector_summary_t *out
) {
    uint64_t total = 0;
    uint32_t max_score = 0;
    uint32_t max_plane = AI_PRESSURE_PLANE_NONE;

    memset(out, 0, sizeof(*out));
    out->hotspot_plane = AI_PRESSURE_PLANE_NONE;
    for (uint32_t i = 0; i < AI_PRESSURE_PLANE_COUNT; i++) {
        total += scores[i];
        if (scores[i] > max_score) {
            max_score = scores[i];
            max_plane = i;
        }
    }

    if (total == 0) {
        return;
    }
    out->hotspot_plane = max_plane;
    out->hotspot_valid = 1U;
    out->hotspot_score_q10 = max_score;
    out->concentration_q10 = ratio_q10(max_score, total);
}

uint64_t ai_pressure_eligible_mask(const ai_pressure_gate_masks_t *masks) {
    if (!masks) {
        return 0;
    }
    return masks->online_mask &
           masks->affinity_mask &
           masks->policy_gate_mask &
           masks->health_mask &
           masks->budget_mask;
}

static bool pressure_selftest_vectors(void) {
    const uint32_t balanced_scores[AI_PRESSURE_PLANE_COUNT] = {
        256U, 256U, 256U
    };
    const uint32_t hotspot_scores[AI_PRESSURE_PLANE_COUNT] = {
        64U, 900U, 64U
    };
    pressure_vector_summary_t summary;

    summarize_vector(balanced_scores, &summary);
    if (!summary.hotspot_valid ||
        summary.hotspot_score_q10 != 256U ||
        summary.concentration_q10 != 341U) {
        return false;
    }

    summarize_vector(hotspot_scores, &summary);
    return summary.hotspot_valid &&
           summary.hotspot_plane == AI_PRESSURE_PLANE_MEMORY &&
           summary.hotspot_score_q10 == 900U &&
           summary.concentration_q10 > 800U;
}

static bool pressure_selftest_gate_mask(void) {
    const ai_pressure_gate_masks_t masks = {
        .online_mask = 0xFULL,
        .affinity_mask = 0xDULL,
        .policy_gate_mask = 0x7ULL,
        .health_mask = 0xBULL,
        .budget_mask = 0xFULL,
    };
    return ai_pressure_eligible_mask(&masks) == 0x1ULL &&
           ai_pressure_eligible_mask(NULL) == 0;
}

aios_status_t ai_pressure_init(void) {
    ai_sched_queue_snapshot_t sched;
    memory_fabric_pressure_snapshot_t memory;
    bool vectors_ok;
    bool overlap_ok;
    bool gate_mask_ok;

    g_pressure_ready = false;
    g_sample_sequence = 0;

    vectors_ok = pressure_selftest_vectors();
    overlap_ok = memory_fabric_pressure_selftest() == AIOS_OK;
    gate_mask_ok = pressure_selftest_gate_mask();
    if (ai_sched_queue_snapshot(&sched) != AIOS_OK ||
        sched.queue_count != SCHED_POLICY_COUNT ||
        memory_fabric_pressure_read(&memory) != AIOS_OK) {
        serial_write("[PRESSURE] tracker selftest FAIL source-read=0\n");
        return AIOS_ERR_IO;
    }
    if (!vectors_ok || !overlap_ok || !gate_mask_ok ||
        memory.active_domains == 0U) {
        serial_write("[PRESSURE] tracker selftest FAIL invariant=0\n");
        return AIOS_ERR_IO;
    }

    g_pressure_ready = true;
    serial_write("[PRESSURE] tracker selftest PASS schema=1 planes=3 max_levels=4 active_levels=2 balanced=1 hotspot=1 overlap=1 gate_mask=1 observation_only=1\n");
    kprintf("[PRESSURE] tracker ready schema=1 observation_only=1\n");
    return AIOS_OK;
}

bool ai_pressure_ready(void) {
    return g_pressure_ready;
}

aios_status_t ai_pressure_read(ai_pressure_snapshot_t *out) {
    ai_sched_queue_snapshot_t sched;
    memory_fabric_pressure_snapshot_t memory;
    nodebit_stats_summary_t policy;
    pressure_vector_summary_t summary;
    uint32_t sched_capacity_q10;
    uint32_t memory_density_q10;
    uint32_t memory_window_q10;

    if (!out || !g_pressure_ready) {
        return AIOS_ERR_INVAL;
    }
    if (ai_sched_queue_snapshot(&sched) != AIOS_OK ||
        memory_fabric_pressure_read(&memory) != AIOS_OK) {
        return AIOS_ERR_IO;
    }
    nodebit_stats_summary(&policy);

    memset(out, 0, sizeof(*out));
    out->schema_version = AI_PRESSURE_SCHEMA_VERSION;
    out->struct_size = (uint32_t)sizeof(*out);
    out->observation_only = 1U;
    out->gate_filter_separate = 1U;
    out->max_levels = AI_PRESSURE_MAX_LEVELS;
    out->active_levels = AI_PRESSURE_ACTIVE_LEVELS;
    out->plane_count = AI_PRESSURE_PLANE_COUNT;
    out->source_flags = AI_PRESSURE_SOURCE_SCHED_EXACT |
                        AI_PRESSURE_SOURCE_MEMORY_EXACT |
                        AI_PRESSURE_SOURCE_POLICY_CUMULATIVE;
    out->sampled_at_ns = kernel_time_monotonic_ns();
    out->sample_sequence = __atomic_add_fetch(
        &g_sample_sequence, 1ULL, __ATOMIC_RELAXED
    );

    sched_capacity_q10 = ratio_q10(sched.runnable_tasks, MAX_AI_TASKS);
    out->scheduler_queue_concentration_q10 =
        ratio_q10(sched.largest_queue, sched.queued_tasks);
    out->plane_score_q10[AI_PRESSURE_PLANE_SCHED] = sched_capacity_q10;
    out->queued_tasks = sched.queued_tasks;
    out->runnable_tasks = sched.runnable_tasks;
    out->largest_queue = sched.largest_queue;
    out->largest_queue_policy = (uint32_t)sched.largest_queue_policy;
    for (uint32_t i = 0; i < SCHED_POLICY_COUNT; i++) {
        out->queue_sizes[i] = sched.queue_sizes[i];
    }

    memory_density_q10 = ratio_q10(
        memory.weighted_shared_bytes, memory.total_budget_bytes
    );
    memory_window_q10 = ratio_q10(
        memory.active_windows, MEMORY_FABRIC_MAX_WINDOWS
    );
    out->plane_score_q10[AI_PRESSURE_PLANE_MEMORY] =
        MAX(memory_density_q10, memory_window_q10);
    out->active_domains = memory.active_domains;
    out->active_windows = memory.active_windows;
    out->shared_windows = memory.shared_windows;
    out->participant_links = memory.participant_links;
    out->writer_pairs = memory.writer_pairs;
    out->read_write_pairs = memory.read_write_pairs;
    out->max_fanout = memory.max_fanout;
    out->total_budget_bytes = memory.total_budget_bytes;
    out->shared_bytes = memory.shared_bytes;
    out->weighted_shared_bytes = memory.weighted_shared_bytes;

    out->plane_score_q10[AI_PRESSURE_PLANE_POLICY] =
        ratio_q10(policy.denies, policy.evaluations);
    out->active_nodes = policy.active_nodes;
    out->gate_evaluations = policy.evaluations;
    out->gate_denies = policy.denies;
    out->gate_health_blocks = policy.health_blocks;

    summarize_vector(out->plane_score_q10, &summary);
    out->hotspot_plane = summary.hotspot_plane;
    out->hotspot_valid = summary.hotspot_valid;
    out->hotspot_score_q10 = summary.hotspot_score_q10;
    out->plane_concentration_q10 = summary.concentration_q10;
    return AIOS_OK;
}

const char *ai_pressure_plane_name(uint32_t plane_id) {
    switch (plane_id) {
        case AI_PRESSURE_PLANE_SCHED:  return "sched";
        case AI_PRESSURE_PLANE_MEMORY: return "memory";
        case AI_PRESSURE_PLANE_POLICY: return "policy";
        case AI_PRESSURE_PLANE_NONE:   return "none";
        default:                       return "unknown";
    }
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
