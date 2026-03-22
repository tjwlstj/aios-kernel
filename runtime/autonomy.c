/*
 * AIOS Kernel - Autonomy Control Plane
 * AI-Native Operating System
 */

#include <runtime/autonomy.h>
#include <drivers/vga.h>

static telemetry_frame_t telemetry_ring[AUTONOMY_TELEMETRY_CAP];
static uint32_t telemetry_head = 0;
static uint32_t telemetry_count = 0;

static policy_action_t action_queue[AUTONOMY_ACTION_CAP];
static uint32_t action_head = 0;
static uint32_t action_tail = 0;
static uint32_t action_count = 0;
static policy_action_t last_committed;
static bool has_last_committed = false;

static autonomy_stats_t stats;
static uint64_t monotonic_tick = 0;

static uint64_t autonomy_now_ns(void) {
    monotonic_tick += 1000000ULL; /* 1ms coarse monotonic clock */
    return monotonic_tick;
}

static bool target_allowed(uint32_t target_subsys) {
    return target_subsys == AUTONOMY_TARGET_MEM ||
           target_subsys == AUTONOMY_TARGET_SCHED ||
           target_subsys == AUTONOMY_TARGET_ACCEL ||
           target_subsys == AUTONOMY_TARGET_INFER;
}

static bool risk_allowed(uint32_t risk_level) {
    if (risk_level > 3) return false;
    if (stats.safe_mode && risk_level > 0) return false;
    if (stats.observation_only) return false;
    return true;
}

static bool delta_within_bounds(const policy_action_t *action) {
    if (!action) return false;

    /* Bounded autonomy: hard-limit adjustment magnitude */
    switch (action->target_subsys) {
        case AUTONOMY_TARGET_MEM:
            return (action->delta_value >= -256 && action->delta_value <= 256);
        case AUTONOMY_TARGET_SCHED:
            return (action->delta_value >= -64 && action->delta_value <= 64);
        case AUTONOMY_TARGET_ACCEL:
            return (action->delta_value >= -8 && action->delta_value <= 8);
        case AUTONOMY_TARGET_INFER:
            return (action->delta_value >= -32 && action->delta_value <= 32);
        default:
            return false;
    }
}

aios_status_t autonomy_init(void) {
    telemetry_head = 0;
    telemetry_count = 0;
    action_head = 0;
    action_tail = 0;
    action_count = 0;
    has_last_committed = false;

    stats.telemetry_samples = 0;
    stats.actions_proposed = 0;
    stats.actions_approved = 0;
    stats.actions_committed = 0;
    stats.actions_rejected = 0;
    stats.rollbacks = 0;
    stats.observation_only = true; /* default safe behavior */
    stats.safe_mode = false;
    monotonic_tick = 0;

    kprintf("\n");
    kprintf("    Autonomy Control Plane initialized:\n");
    kprintf("    Telemetry Ring: %u\n", (uint64_t)AUTONOMY_TELEMETRY_CAP);
    kprintf("    Action Queue:   %u\n", (uint64_t)AUTONOMY_ACTION_CAP);
    kprintf("    Mode: Observation-only (default)\n");

    return AIOS_OK;
}

void autonomy_collect_telemetry(uint32_t active_models, uint32_t active_accels) {
    telemetry_frame_t frame;
    frame.ts_ns = autonomy_now_ns();
    tensor_mm_stats(&frame.mem);
    ai_sched_stats(&frame.sched);
    frame.active_models = active_models;
    frame.active_accels = active_accels;

    telemetry_ring[telemetry_head] = frame;
    telemetry_head = (telemetry_head + 1) % AUTONOMY_TELEMETRY_CAP;
    if (telemetry_count < AUTONOMY_TELEMETRY_CAP) {
        telemetry_count++;
    }

    stats.telemetry_samples++;
}

aios_status_t autonomy_action_propose(const policy_action_t *action) {
    if (!action) return AIOS_ERR_INVAL;
    if (action_count >= AUTONOMY_ACTION_CAP) return AIOS_ERR_BUSY;

    stats.actions_proposed++;

    policy_action_t copy = *action;
    copy.ts_ns = autonomy_now_ns();
    copy.state = ACTION_STATE_PROPOSED;

    /* Guardian checks */
    if (!target_allowed(copy.target_subsys) || !delta_within_bounds(&copy)) {
        stats.actions_rejected++;
        copy.state = ACTION_STATE_REJECTED;
        return AIOS_ERR_INVAL;
    }

    if (!risk_allowed(copy.risk_level)) {
        stats.actions_rejected++;
        copy.state = ACTION_STATE_REJECTED;
        return AIOS_ERR_PERM;
    }

    copy.state = ACTION_STATE_APPROVED;
    action_queue[action_tail] = copy;
    action_tail = (action_tail + 1) % AUTONOMY_ACTION_CAP;
    action_count++;
    stats.actions_approved++;

    return AIOS_OK;
}

aios_status_t autonomy_action_commit_next(policy_eval_t *eval) {
    if (action_count == 0) return AIOS_ERR_INVAL;

    policy_action_t action = action_queue[action_head];
    action_head = (action_head + 1) % AUTONOMY_ACTION_CAP;
    action_count--;

    /* In MVP we don't directly mutate subsystems yet; this is a validated
     * control-plane commit point with auditable metadata. */
    action.state = ACTION_STATE_COMMITTED;
    last_committed = action;
    has_last_committed = true;
    stats.actions_committed++;

    if (eval) {
        eval->before_score = 0;
        eval->after_score = 0;
        eval->rollback_triggered = false;
    }

    return AIOS_OK;
}

aios_status_t autonomy_action_rollback_last(void) {
    if (!has_last_committed) return AIOS_ERR_INVAL;

    last_committed.state = ACTION_STATE_ROLLED_BACK;
    has_last_committed = false;
    stats.rollbacks++;

    return AIOS_OK;
}

void autonomy_set_observation_only(bool enabled) {
    stats.observation_only = enabled;
}

void autonomy_set_safe_mode(bool enabled) {
    stats.safe_mode = enabled;
}

void autonomy_stats(autonomy_stats_t *out) {
    if (!out) return;
    *out = stats;
}

void autonomy_dump(void) {
    kprintf("\n=== Autonomy Control Plane ===\n");
    kprintf("Telemetry samples: %u\n", stats.telemetry_samples);
    kprintf("Actions: proposed=%u approved=%u committed=%u rejected=%u\n",
            stats.actions_proposed,
            stats.actions_approved,
            stats.actions_committed,
            stats.actions_rejected);
    kprintf("Rollbacks: %u\n", stats.rollbacks);
    kprintf("Mode: observation_only=%u safe_mode=%u\n",
            (uint64_t)stats.observation_only,
            (uint64_t)stats.safe_mode);
    kprintf("Queue depth: %u\n", (uint64_t)action_count);
    if (has_last_committed) {
        kprintf("Last committed action: id=%u target=%u risk=%u delta=%u\n",
                (uint64_t)last_committed.action_id,
                (uint64_t)last_committed.target_subsys,
                (uint64_t)last_committed.risk_level,
                (uint64_t)last_committed.delta_value);
    }
    kprintf("==============================\n");
}
