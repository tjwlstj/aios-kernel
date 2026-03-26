/*
 * AIOS Kernel - Autonomy Control Plane
 * AI-Native Operating System
 */

#include <runtime/autonomy.h>
#include <kernel/time.h>
#include <hal/accel_hal.h>
#include <drivers/vga.h>

static telemetry_frame_t telemetry_ring[AUTONOMY_TELEMETRY_CAP];
static uint32_t telemetry_head = 0;
static uint32_t telemetry_count = 0;

static policy_action_t action_queue[AUTONOMY_ACTION_CAP];
static uint32_t action_head = 0;
static uint32_t action_tail = 0;
static uint32_t action_count = 0;

static autonomy_event_t event_log[AUTONOMY_EVENT_CAP];
static uint32_t event_head = 0;
static uint32_t event_count = 0;

static policy_action_t last_committed;
static bool has_last_committed = false;

/* Rollback context (MVP: scheduler priority change only) */
static struct {
    bool valid;
    uint32_t target_subsys;
    uint32_t action_id;
    priority_t prev_priority;
} rollback_ctx;

static autonomy_stats_t stats;
static uint64_t autonomy_now_ns(void) {
    return kernel_time_monotonic_ns();
}

static void collect_live_telemetry(telemetry_frame_t *out) {
    if (!out) return;

    out->ts_ns = autonomy_now_ns();
    tensor_mm_stats(&out->mem);
    ai_sched_stats(&out->sched);
    out->active_models = 0;
    out->active_accels = accel_get_count();
}

static uint64_t compute_eval_score(const telemetry_frame_t *frame) {
    if (!frame) return 0;

    uint64_t score = 1000000ULL;

    score -= MIN(frame->mem.fragmentation * 500ULL, 250000ULL);
    score -= MIN(frame->sched.failed_tasks * 10000ULL, 250000ULL);
    score -= MIN(frame->sched.avg_wait_ns / 1000ULL, 250000ULL);
    score -= MIN(frame->sched.active_tasks * 1000ULL, 100000ULL);

    return score;
}

static void log_event(const policy_action_t *action) {
    if (!action) return;

    autonomy_event_t ev;
    ev.ts_ns = action->ts_ns;
    ev.action_id = action->action_id;
    ev.target_subsys = action->target_subsys;
    ev.state = action->state;
    ev.reason = action->reason;

    event_log[event_head] = ev;
    event_head = (event_head + 1) % AUTONOMY_EVENT_CAP;
    if (event_count < AUTONOMY_EVENT_CAP) {
        event_count++;
    }
}

static bool target_supported(uint32_t target_subsys) {
    /* MVP actuator exists for scheduler target only. */
    return target_subsys == AUTONOMY_TARGET_SCHED;
}

static bool risk_level_valid(uint32_t risk_level) {
    return risk_level <= 3;
}

static bool risk_allowed(uint32_t risk_level) {
    if (stats.safe_mode && risk_level > 0) return false;
    if (stats.observation_only) return false;
    return true;
}

static bool delta_within_bounds(const policy_action_t *action) {
    if (!action) return false;

    /* Bounded autonomy: hard-limit adjustment magnitude */
    switch (action->target_subsys) {
        case AUTONOMY_TARGET_SCHED:
            return (action->delta_value >= -32 && action->delta_value <= 32);
        default:
            return false;
    }
}

static priority_t clamp_priority(int64_t value) {
    if (value < 0) return 0;
    if (value > 255) return 255;
    return (priority_t)value;
}

static aios_status_t apply_sched_action(policy_action_t *action) {
    priority_t current;
    aios_status_t st = ai_task_get_priority((task_id_t)action->action_id, &current);
    if (st != AIOS_OK) return st;

    int64_t new_priority_raw = (int64_t)current + action->delta_value;
    priority_t new_priority = clamp_priority(new_priority_raw);

    rollback_ctx.valid = true;
    rollback_ctx.target_subsys = action->target_subsys;
    rollback_ctx.action_id = action->action_id;
    rollback_ctx.prev_priority = current;

    return ai_task_set_priority((task_id_t)action->action_id, new_priority);
}

static aios_status_t apply_action(policy_action_t *action) {
    if (!action) return AIOS_ERR_INVAL;

    switch (action->target_subsys) {
        case AUTONOMY_TARGET_SCHED:
            return apply_sched_action(action);
        default:
            return AIOS_ERR_NOSYS;
    }
}

aios_status_t autonomy_init(void) {
    telemetry_head = 0;
    telemetry_count = 0;
    action_head = 0;
    action_tail = 0;
    action_count = 0;
    event_head = 0;
    event_count = 0;
    has_last_committed = false;

    rollback_ctx.valid = false;
    rollback_ctx.target_subsys = AUTONOMY_TARGET_UNKNOWN;
    rollback_ctx.action_id = 0;
    rollback_ctx.prev_priority = 0;

    stats.telemetry_samples = 0;
    stats.actions_proposed = 0;
    stats.actions_approved = 0;
    stats.actions_committed = 0;
    stats.actions_rejected = 0;
    stats.rollbacks = 0;
    stats.observation_only = true; /* default safe behavior */
    stats.safe_mode = false;
    stats.action_queue_depth = 0;
    stats.event_log_depth = 0;
    kprintf("\n");
    kprintf("    Autonomy Control Plane initialized:\n");
    kprintf("    Telemetry Ring: %u\n", (uint64_t)AUTONOMY_TELEMETRY_CAP);
    kprintf("    Action Queue:   %u\n", (uint64_t)AUTONOMY_ACTION_CAP);
    kprintf("    Event Log:      %u\n", (uint64_t)AUTONOMY_EVENT_CAP);
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

aios_status_t autonomy_get_latest_telemetry(telemetry_frame_t *out) {
    if (!out) return AIOS_ERR_INVAL;
    if (telemetry_count == 0) return AIOS_ERR_INVAL;

    uint32_t idx = (telemetry_head == 0) ? (AUTONOMY_TELEMETRY_CAP - 1)
                                         : (telemetry_head - 1);
    *out = telemetry_ring[idx];
    return AIOS_OK;
}

aios_status_t autonomy_action_propose(const policy_action_t *action) {
    if (!action) return AIOS_ERR_INVAL;
    if (action_count >= AUTONOMY_ACTION_CAP) return AIOS_ERR_BUSY;

    stats.actions_proposed++;

    policy_action_t copy = *action;
    copy.ts_ns = autonomy_now_ns();
    copy.state = ACTION_STATE_PROPOSED;
    copy.reason = AUTONOMY_REASON_OK;

    if (!risk_level_valid(copy.risk_level)) {
        copy.state = ACTION_STATE_REJECTED;
        copy.reason = AUTONOMY_REASON_BAD_RISK;
        stats.actions_rejected++;
        log_event(&copy);
        return AIOS_ERR_INVAL;
    }

    if (!target_supported(copy.target_subsys)) {
        copy.state = ACTION_STATE_REJECTED;
        copy.reason = AUTONOMY_REASON_UNSUPPORTED_TARGET;
        stats.actions_rejected++;
        log_event(&copy);
        return AIOS_ERR_NOSYS;
    }

    if (!delta_within_bounds(&copy)) {
        copy.state = ACTION_STATE_REJECTED;
        copy.reason = AUTONOMY_REASON_BAD_DELTA;
        stats.actions_rejected++;
        log_event(&copy);
        return AIOS_ERR_INVAL;
    }

    if (!risk_allowed(copy.risk_level)) {
        copy.state = ACTION_STATE_REJECTED;
        copy.reason = AUTONOMY_REASON_MODE_BLOCKED;
        stats.actions_rejected++;
        log_event(&copy);
        return AIOS_ERR_PERM;
    }

    copy.state = ACTION_STATE_APPROVED;
    copy.reason = AUTONOMY_REASON_OK;
    action_queue[action_tail] = copy;
    action_tail = (action_tail + 1) % AUTONOMY_ACTION_CAP;
    action_count++;
    stats.actions_approved++;
    stats.action_queue_depth = action_count;

    log_event(&copy);

    return AIOS_OK;
}

aios_status_t autonomy_action_propose_req(const autonomy_action_req_t *req) {
    if (!req) return AIOS_ERR_INVAL;

    policy_action_t action;
    action.action_id = req->action_id;
    action.risk_level = req->risk_level;
    action.target_subsys = req->target_subsys;
    action.delta_value = req->delta_value;
    action.ts_ns = 0;
    action.state = ACTION_STATE_PROPOSED;
    action.reason = AUTONOMY_REASON_OK;

    return autonomy_action_propose(&action);
}

aios_status_t autonomy_action_commit_next(policy_eval_t *eval) {
    if (action_count == 0) return AIOS_ERR_INVAL;

    policy_action_t action = action_queue[action_head];
    action_head = (action_head + 1) % AUTONOMY_ACTION_CAP;
    action_count--;
    stats.action_queue_depth = action_count;

    telemetry_frame_t before_frame;
    telemetry_frame_t after_frame;
    collect_live_telemetry(&before_frame);

    aios_status_t apply_status = apply_action(&action);
    if (apply_status != AIOS_OK) {
        action.state = ACTION_STATE_REJECTED;
        action.reason = AUTONOMY_REASON_UNSUPPORTED_TARGET;
        stats.actions_rejected++;
        log_event(&action);
        return apply_status;
    }

    action.state = ACTION_STATE_COMMITTED;
    action.reason = AUTONOMY_REASON_OK;
    action.ts_ns = autonomy_now_ns();

    last_committed = action;
    has_last_committed = true;
    stats.actions_committed++;

    collect_live_telemetry(&after_frame);

    if (eval) {
        eval->before_score = compute_eval_score(&before_frame);
        eval->after_score = compute_eval_score(&after_frame);
        eval->rollback_triggered = false;
    }

    log_event(&action);

    if (eval && eval->after_score + 5000ULL < eval->before_score) {
        if (autonomy_action_rollback_last() == AIOS_OK) {
            eval->rollback_triggered = true;
        }
    }

    return AIOS_OK;
}

aios_status_t autonomy_action_rollback_last(void) {
    if (!has_last_committed) return AIOS_ERR_INVAL;

    if (!rollback_ctx.valid) return AIOS_ERR_INVAL;

    aios_status_t st = AIOS_ERR_NOSYS;
    if (rollback_ctx.target_subsys == AUTONOMY_TARGET_SCHED) {
        st = ai_task_set_priority((task_id_t)rollback_ctx.action_id,
                                  rollback_ctx.prev_priority);
    }

    if (st != AIOS_OK) return st;

    last_committed.state = ACTION_STATE_ROLLED_BACK;
    last_committed.reason = AUTONOMY_REASON_OK;
    last_committed.ts_ns = autonomy_now_ns();
    stats.rollbacks++;

    log_event(&last_committed);

    rollback_ctx.valid = false;
    has_last_committed = false;
    return AIOS_OK;
}

aios_status_t autonomy_get_last_event(autonomy_event_t *out) {
    if (!out) return AIOS_ERR_INVAL;
    if (event_count == 0) return AIOS_ERR_INVAL;

    uint32_t idx = (event_head == 0) ? (AUTONOMY_EVENT_CAP - 1)
                                     : (event_head - 1);
    *out = event_log[idx];
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
    out->telemetry_samples = stats.telemetry_samples;
    out->actions_proposed = stats.actions_proposed;
    out->actions_approved = stats.actions_approved;
    out->actions_committed = stats.actions_committed;
    out->actions_rejected = stats.actions_rejected;
    out->rollbacks = stats.rollbacks;
    out->observation_only = stats.observation_only;
    out->safe_mode = stats.safe_mode;
    out->action_queue_depth = action_count;
    out->event_log_depth = event_count;
}

void autonomy_dump(void) {
    autonomy_event_t last_event;
    bool has_event = (autonomy_get_last_event(&last_event) == AIOS_OK);

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
    kprintf("Queue depth: %u | Event depth: %u\n",
            (uint64_t)action_count,
            (uint64_t)event_count);

    if (has_last_committed) {
        kprintf("Last committed action: id=%u target=%u risk=%u\n",
                (uint64_t)last_committed.action_id,
                (uint64_t)last_committed.target_subsys,
                (uint64_t)last_committed.risk_level);
    }

    if (has_event) {
        kprintf("Last event: action=%u state=%u reason=%u\n",
                (uint64_t)last_event.action_id,
                (uint64_t)last_event.state,
                (uint64_t)last_event.reason);
    }

    kprintf("==============================\n");
}
