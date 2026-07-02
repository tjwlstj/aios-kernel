/*
 * AIOS Kernel - Node Pipeline Orchestration
 * AI-Native Operating System
 *
 * See include/runtime/node_pipeline.h for the design contract.
 * Thread safety: registry mutations are protected by g_lock.
 */

#include <runtime/node_pipeline.h>
#include <runtime/nodebit.h>
#include <hal/accel_hal.h>
#include <kernel/time.h>
#include <kernel/spinlock.h>
#include <drivers/serial.h>
#include <drivers/vga.h>
#include <lib/string.h>

/* -------------------------------------------------------------------------
 * State
 * ---------------------------------------------------------------------- */

static node_pipeline_t  g_pipelines[NODE_PIPELINE_MAX];
static spinlock_t       g_lock = SPINLOCK_INIT;
static bool             g_ready = false;
static uint32_t         g_next_id = 1;

static uint64_t         g_total_executions = 0;
static uint64_t         g_total_stage_runs = 0;
static uint32_t         g_denied_count = 0;
static aios_status_t    g_last_status = AIOS_OK;

/* -------------------------------------------------------------------------
 * Internal helpers
 * ---------------------------------------------------------------------- */

static node_pipeline_t *find_pipeline_locked(uint32_t pipeline_id) {
    if (pipeline_id == 0) {
        return NULL;
    }
    for (uint32_t i = 0; i < NODE_PIPELINE_MAX; i++) {
        if (g_pipelines[i].state != NODE_PIPELINE_STATE_FREE &&
            g_pipelines[i].pipeline_id == pipeline_id) {
            return &g_pipelines[i];
        }
    }
    return NULL;
}

static node_pipeline_t *find_free_slot_locked(void) {
    for (uint32_t i = 0; i < NODE_PIPELINE_MAX; i++) {
        if (g_pipelines[i].state == NODE_PIPELINE_STATE_FREE) {
            return &g_pipelines[i];
        }
    }
    return NULL;
}

static void copy_label(char *dst, const char *src) {
    uint32_t i = 0;
    while (src[i] && i < NODE_PIPELINE_LABEL_MAX - 1) {
        dst[i] = src[i];
        i++;
    }
    dst[i] = '\0';
}

/* NodeBit gate: pipeline control requires an explicit PERMIT. */
static aios_status_t gate_check(uint16_t node_id) {
    nodebit_decision_t decision;
    aios_status_t status = nodebit_evaluate(node_id, NODEBIT_CAP_PIPELINE,
                                            &decision);

    if (status != AIOS_OK || decision.action != NODEBIT_ACTION_PERMIT) {
        spinlock_lock(&g_lock);
        g_denied_count++;
        spinlock_unlock(&g_lock);
        return AIOS_ERR_PERM;
    }
    return AIOS_OK;
}

static bool stage_valid(const pipeline_stage_t *stage) {
    /* Accelerator must exist; model/tensor binding is validated at
     * execution time once the inference runtime lands. */
    return stage->accel_id < accel_get_count();
}

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

aios_status_t node_pipeline_init(void) {
    memset(g_pipelines, 0, sizeof(g_pipelines));
    g_next_id = 1;
    g_total_executions = 0;
    g_total_stage_runs = 0;
    g_denied_count = 0;
    g_last_status = AIOS_OK;
    g_ready = true;

    serial_printf("[PIPE] Node pipeline ready slots=%u stages_max=%u\n",
        (uint64_t)NODE_PIPELINE_MAX, (uint64_t)NODE_PIPELINE_MAX_STAGES);
    return AIOS_OK;
}

aios_status_t node_pipeline_create(uint16_t node_id, const char *label,
                                   uint32_t *pipeline_id_out) {
    node_pipeline_t *slot;
    aios_status_t status;

    if (!g_ready || !label || !pipeline_id_out) {
        return AIOS_ERR_INVAL;
    }

    status = gate_check(node_id);
    if (status != AIOS_OK) {
        return status;
    }

    spinlock_lock(&g_lock);
    slot = find_free_slot_locked();
    if (!slot) {
        spinlock_unlock(&g_lock);
        return AIOS_ERR_NOMEM;
    }

    memset(slot, 0, sizeof(*slot));
    slot->pipeline_id = g_next_id++;
    slot->owner_node = node_id;
    slot->state = NODE_PIPELINE_STATE_OPEN;
    slot->created_ts_ns = kernel_time_monotonic_ns();
    slot->last_status = AIOS_OK;
    copy_label(slot->label, label);
    *pipeline_id_out = slot->pipeline_id;
    spinlock_unlock(&g_lock);

    serial_printf("[PIPE] Created pipeline=%u node=%u label=%s\n",
        (uint64_t)*pipeline_id_out, (uint64_t)node_id, label);
    return AIOS_OK;
}

aios_status_t node_pipeline_add_stage(uint16_t node_id, uint32_t pipeline_id,
                                      const pipeline_stage_t *stage) {
    node_pipeline_t *pipeline;
    aios_status_t status;

    if (!g_ready || !stage) {
        return AIOS_ERR_INVAL;
    }
    if (!stage_valid(stage)) {
        return AIOS_ERR_ACCEL;
    }

    status = gate_check(node_id);
    if (status != AIOS_OK) {
        return status;
    }

    spinlock_lock(&g_lock);
    pipeline = find_pipeline_locked(pipeline_id);
    if (!pipeline) {
        spinlock_unlock(&g_lock);
        return AIOS_ERR_NODEV;
    }
    if (pipeline->owner_node != node_id) {
        g_denied_count++;
        spinlock_unlock(&g_lock);
        return AIOS_ERR_PERM;
    }
    if (pipeline->stage_count >= NODE_PIPELINE_MAX_STAGES) {
        spinlock_unlock(&g_lock);
        return AIOS_ERR_NOMEM;
    }

    pipeline->stages[pipeline->stage_count++] = *stage;
    pipeline->state = NODE_PIPELINE_STATE_READY;
    spinlock_unlock(&g_lock);

    return AIOS_OK;
}

aios_status_t node_pipeline_execute(uint16_t node_id, uint32_t pipeline_id,
                                    node_pipeline_exec_result_t *result_out) {
    node_pipeline_t *pipeline;
    aios_status_t status;
    uint64_t start_ns;
    uint64_t duration_ns;
    uint32_t stage_count;
    uint32_t executed = 0;
    pipeline_stage_t stages[NODE_PIPELINE_MAX_STAGES];

    if (!g_ready) {
        return AIOS_ERR_INVAL;
    }

    status = gate_check(node_id);
    if (status != AIOS_OK) {
        return status;
    }

    spinlock_lock(&g_lock);
    pipeline = find_pipeline_locked(pipeline_id);
    if (!pipeline) {
        spinlock_unlock(&g_lock);
        return AIOS_ERR_NODEV;
    }
    if (pipeline->owner_node != node_id) {
        g_denied_count++;
        spinlock_unlock(&g_lock);
        return AIOS_ERR_PERM;
    }
    if (pipeline->state != NODE_PIPELINE_STATE_READY ||
        pipeline->stage_count == 0) {
        spinlock_unlock(&g_lock);
        return AIOS_ERR_BUSY;   /* no stages to run yet */
    }
    stage_count = pipeline->stage_count;
    for (uint32_t i = 0; i < stage_count; i++) {
        stages[i] = pipeline->stages[i];
    }
    spinlock_unlock(&g_lock);

    /* Control-plane execution pass: walk the stage chain outside the lock,
     * re-checking accelerator bounds (topology may have changed since add).
     * The real per-stage inference dispatch replaces this walk when the
     * model runtime lands; timing and stats shape stay identical. */
    start_ns = kernel_time_monotonic_ns();
    for (uint32_t i = 0; i < stage_count; i++) {
        if (!stage_valid(&stages[i])) {
            break;
        }
        executed++;
    }
    duration_ns = kernel_time_monotonic_ns() - start_ns;

    if (executed != stage_count) {
        spinlock_lock(&g_lock);
        pipeline = find_pipeline_locked(pipeline_id);
        if (pipeline) {
            pipeline->last_status = AIOS_ERR_ACCEL;
        }
        g_last_status = AIOS_ERR_ACCEL;
        spinlock_unlock(&g_lock);
        if (result_out) {
            result_out->executed_stages = executed;
            result_out->duration_ns = duration_ns;
            result_out->status = AIOS_ERR_ACCEL;
        }
        return AIOS_ERR_ACCEL;
    }

    spinlock_lock(&g_lock);
    pipeline = find_pipeline_locked(pipeline_id);  /* revalidate after unlock */
    if (pipeline) {
        pipeline->executions++;
        pipeline->last_status = AIOS_OK;
        pipeline->last_exec_ns = duration_ns;
    }
    g_total_executions++;
    g_total_stage_runs += stage_count;
    g_last_status = AIOS_OK;
    spinlock_unlock(&g_lock);

    /* Attribute the run to the owning node's observation record. */
    nodebit_observe_work(node_id, duration_ns);

    if (result_out) {
        result_out->executed_stages = stage_count;
        result_out->duration_ns = duration_ns;
        result_out->status = AIOS_OK;
    }

    serial_printf("[PIPE] Executed pipeline=%u node=%u stages=%u dur_ns=%u\n",
        (uint64_t)pipeline_id, (uint64_t)node_id,
        (uint64_t)stage_count, duration_ns);
    return AIOS_OK;
}

aios_status_t node_pipeline_destroy(uint16_t node_id, uint32_t pipeline_id) {
    node_pipeline_t *pipeline;
    aios_status_t status;

    if (!g_ready) {
        return AIOS_ERR_INVAL;
    }

    status = gate_check(node_id);
    if (status != AIOS_OK) {
        return status;
    }

    spinlock_lock(&g_lock);
    pipeline = find_pipeline_locked(pipeline_id);
    if (!pipeline) {
        spinlock_unlock(&g_lock);
        return AIOS_ERR_NODEV;
    }
    if (pipeline->owner_node != node_id) {
        g_denied_count++;
        spinlock_unlock(&g_lock);
        return AIOS_ERR_PERM;
    }
    memset(pipeline, 0, sizeof(*pipeline));
    spinlock_unlock(&g_lock);

    serial_printf("[PIPE] Destroyed pipeline=%u node=%u\n",
        (uint64_t)pipeline_id, (uint64_t)node_id);
    return AIOS_OK;
}

aios_status_t node_pipeline_get(uint32_t pipeline_id, node_pipeline_t *out) {
    const node_pipeline_t *pipeline;

    if (!g_ready || !out) {
        return AIOS_ERR_INVAL;
    }

    spinlock_lock(&g_lock);
    pipeline = find_pipeline_locked(pipeline_id);
    if (!pipeline) {
        spinlock_unlock(&g_lock);
        return AIOS_ERR_NODEV;
    }
    *out = *pipeline;
    spinlock_unlock(&g_lock);
    return AIOS_OK;
}

void node_pipeline_get_snapshot(node_pipeline_snapshot_t *out) {
    uint32_t active = 0;

    if (!out) {
        return;
    }

    spinlock_lock(&g_lock);
    for (uint32_t i = 0; i < NODE_PIPELINE_MAX; i++) {
        if (g_pipelines[i].state != NODE_PIPELINE_STATE_FREE) {
            active++;
        }
    }
    out->active_count = active;
    out->max_pipelines = NODE_PIPELINE_MAX;
    out->total_executions = g_total_executions;
    out->total_stage_runs = g_total_stage_runs;
    out->denied_count = g_denied_count;
    out->last_status = g_last_status;
    spinlock_unlock(&g_lock);
}

/* -------------------------------------------------------------------------
 * Boot selftest: exercises the full policy chain end to end
 * ---------------------------------------------------------------------- */

#define PIPE_SELFTEST_NODE       40U
#define PIPE_SELFTEST_BAD_NODE   41U

aios_status_t node_pipeline_selftest(void) {
    uint32_t pipeline_id = 0;
    node_pipeline_exec_result_t result = {0};
    node_pipeline_snapshot_t snap;
    pipeline_stage_t stage = {
        .model_id = 0,
        .input_tensor = 0,
        .output_tensor = 0,
        .accel_id = 0,   /* CPU SIMD fallback always exists */
    };
    pipeline_stage_t bad_stage = stage;

    /* Unregistered node must be denied before touching the registry. */
    if (node_pipeline_create(PIPE_SELFTEST_BAD_NODE, "pipe-deny",
                             &pipeline_id) != AIOS_ERR_PERM) {
        return AIOS_ERR_IO;
    }

    /* Register the smoke node with pipeline capability and build a chain. */
    if (nodebit_register(PIPE_SELFTEST_NODE, "pipe-smoke",
                         NODEBIT_CAP_OBSERVE | NODEBIT_CAP_PIPELINE,
                         false) != AIOS_OK) {
        return AIOS_ERR_IO;
    }

    if (node_pipeline_create(PIPE_SELFTEST_NODE, "boot-smoke",
                             &pipeline_id) != AIOS_OK || pipeline_id == 0) {
        return AIOS_ERR_IO;
    }

    /* Empty pipeline must refuse to execute. */
    if (node_pipeline_execute(PIPE_SELFTEST_NODE, pipeline_id, &result) !=
        AIOS_ERR_BUSY) {
        return AIOS_ERR_IO;
    }

    if (node_pipeline_add_stage(PIPE_SELFTEST_NODE, pipeline_id, &stage) !=
        AIOS_OK ||
        node_pipeline_add_stage(PIPE_SELFTEST_NODE, pipeline_id, &stage) !=
        AIOS_OK) {
        return AIOS_ERR_IO;
    }

    /* Out-of-range accelerator must be rejected at add time. */
    bad_stage.accel_id = 0xFFFFu;
    if (node_pipeline_add_stage(PIPE_SELFTEST_NODE, pipeline_id, &bad_stage) !=
        AIOS_ERR_ACCEL) {
        return AIOS_ERR_IO;
    }

    /* Foreign node must not execute or destroy someone else's pipeline. */
    if (node_pipeline_execute(PIPE_SELFTEST_BAD_NODE, pipeline_id, NULL) !=
        AIOS_ERR_PERM) {
        return AIOS_ERR_IO;
    }

    if (node_pipeline_execute(PIPE_SELFTEST_NODE, pipeline_id, &result) !=
        AIOS_OK || result.executed_stages != 2 || result.status != AIOS_OK) {
        return AIOS_ERR_IO;
    }

    node_pipeline_t lookup;
    if (node_pipeline_destroy(PIPE_SELFTEST_NODE, pipeline_id) != AIOS_OK ||
        node_pipeline_get(pipeline_id, &lookup) != AIOS_ERR_NODEV ||
        node_pipeline_destroy(PIPE_SELFTEST_NODE, pipeline_id) !=
        AIOS_ERR_NODEV) {
        return AIOS_ERR_IO;
    }

    node_pipeline_get_snapshot(&snap);
    if (snap.active_count != 0 || snap.total_executions != 1 ||
        snap.total_stage_runs != 2 || snap.denied_count < 2) {
        return AIOS_ERR_IO;
    }

    /* Per-node observation: the smoke node must have accumulated timed
     * gate decisions (create + empty-execute + 2 add-stage + execute +
     * destroy + retry-destroy = 7 permits; the gate clears the retry
     * before the registry reports NODEV) and one attributed work item. */
    nodebit_node_stats_t nstats;
    if (nodebit_stats_lookup(PIPE_SELFTEST_NODE, &nstats) != AIOS_OK ||
        nstats.evaluations != 7 || nstats.permits != 7 ||
        nstats.denies != 0 || nstats.work_count != 1 ||
        nstats.last_decision_ns == 0 ||
        nstats.eval_max_ns < nstats.eval_min_ns ||
        nstats.eval_total_ns < nstats.eval_max_ns) {
        return AIOS_ERR_IO;
    }
    if (nodebit_stats_lookup(PIPE_SELFTEST_BAD_NODE, &nstats) !=
        AIOS_ERR_NODEV ||
        nodebit_observe_work(PIPE_SELFTEST_BAD_NODE, 1) != AIOS_ERR_NODEV) {
        return AIOS_ERR_IO;
    }

    serial_printf("[PIPE] selftest PASS executions=%u stage_runs=%u denied=%u\n",
        snap.total_executions, snap.total_stage_runs,
        (uint64_t)snap.denied_count);
    return AIOS_OK;
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
