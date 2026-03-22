/*
 * AIOS Kernel - Autonomy Control Plane Interface
 * AI-Native Operating System
 *
 * Provides telemetry collection, policy action queueing, and guardian
 * enforcement primitives used by autonomous optimization agents.
 */

#ifndef _AIOS_AUTONOMY_H
#define _AIOS_AUTONOMY_H

#include <kernel/types.h>
#include <mm/tensor_mm.h>
#include <sched/ai_sched.h>

#define AUTONOMY_TELEMETRY_CAP   512
#define AUTONOMY_ACTION_CAP      128

typedef enum {
    AUTONOMY_TARGET_MEM = 0,
    AUTONOMY_TARGET_SCHED = 1,
    AUTONOMY_TARGET_ACCEL = 2,
    AUTONOMY_TARGET_INFER = 3,
    AUTONOMY_TARGET_UNKNOWN = 255,
} autonomy_target_t;

typedef enum {
    ACTION_STATE_PROPOSED = 0,
    ACTION_STATE_APPROVED = 1,
    ACTION_STATE_COMMITTED = 2,
    ACTION_STATE_ROLLED_BACK = 3,
    ACTION_STATE_REJECTED = 4,
} policy_action_state_t;

typedef struct {
    uint64_t ts_ns;
    mem_stats_t mem;
    sched_stats_t sched;
    uint32_t active_models;
    uint32_t active_accels;
} telemetry_frame_t;

typedef struct {
    uint32_t action_id;
    uint32_t risk_level;      /* 0..3 */
    uint32_t target_subsys;   /* autonomy_target_t */
    int64_t  delta_value;
    uint64_t ts_ns;
    policy_action_state_t state;
} policy_action_t;

typedef struct {
    uint64_t before_score;
    uint64_t after_score;
    bool rollback_triggered;
} policy_eval_t;

typedef struct {
    uint64_t telemetry_samples;
    uint64_t actions_proposed;
    uint64_t actions_approved;
    uint64_t actions_committed;
    uint64_t actions_rejected;
    uint64_t rollbacks;
    bool observation_only;
    bool safe_mode;
} autonomy_stats_t;

aios_status_t autonomy_init(void);
void autonomy_collect_telemetry(uint32_t active_models, uint32_t active_accels);
aios_status_t autonomy_action_propose(const policy_action_t *action);
aios_status_t autonomy_action_commit_next(policy_eval_t *eval);
aios_status_t autonomy_action_rollback_last(void);
void autonomy_set_observation_only(bool enabled);
void autonomy_set_safe_mode(bool enabled);
void autonomy_stats(autonomy_stats_t *out);
void autonomy_dump(void);

#endif /* _AIOS_AUTONOMY_H */
