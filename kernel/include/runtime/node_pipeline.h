/*
 * AIOS Kernel - Node Pipeline Orchestration
 * AI-Native Operating System
 *
 * Backs the Pipeline syscall group (SYS_PIPE_* 0x600-0x603) with a real
 * kernel object: an ordered chain of stages owned by a NodeBit node.
 * Userspace agents build pipelines stage-by-stage, then execute them as
 * one policy-gated unit.
 *
 * Policy model: every mutating operation (create / add-stage / execute /
 * destroy) must pass the NodeBit gate with NODEBIT_CAP_PIPELINE for the
 * owning node, and destroy/execute additionally require the caller's
 * node_id to match the pipeline owner. This keeps the store/autonomy
 * invariant: no control action without a NodeBit PERMIT.
 *
 * Execution model (current stage): stages are validated (accelerator
 * bounds) and accounted with monotonic timestamps. Real model execution
 * plugs in once the inference runtime lands; the object lifecycle, the
 * policy chain, and the stats surface are already the final ABI shape.
 */

#ifndef _AIOS_RUNTIME_NODE_PIPELINE_H
#define _AIOS_RUNTIME_NODE_PIPELINE_H

#include <kernel/types.h>

/* -------------------------------------------------------------------------
 * Configuration
 * ---------------------------------------------------------------------- */

#define NODE_PIPELINE_MAX         16U
#define NODE_PIPELINE_MAX_STAGES  8U
#define NODE_PIPELINE_LABEL_MAX   32U

/* -------------------------------------------------------------------------
 * Types
 * ---------------------------------------------------------------------- */

/* Pipeline stage (moved from ai_syscall.h; part of the syscall ABI) */
typedef struct {
    model_id_t      model_id;       /* Model for this stage */
    tensor_id_t     input_tensor;   /* Input tensor */
    tensor_id_t     output_tensor;  /* Output tensor */
    accel_id_t      accel_id;       /* Accelerator for this stage */
} pipeline_stage_t;

typedef enum {
    NODE_PIPELINE_STATE_FREE  = 0,
    NODE_PIPELINE_STATE_OPEN  = 1,  /* created, accepting stages */
    NODE_PIPELINE_STATE_READY = 2,  /* has >= 1 stage, executable */
} node_pipeline_state_t;

typedef struct {
    uint32_t              pipeline_id;   /* 0 = invalid */
    uint16_t              owner_node;    /* NodeBit node id */
    node_pipeline_state_t state;
    char                  label[NODE_PIPELINE_LABEL_MAX];
    uint32_t              stage_count;
    pipeline_stage_t      stages[NODE_PIPELINE_MAX_STAGES];
    uint64_t              executions;
    aios_status_t         last_status;
    uint64_t              last_exec_ns;  /* duration of last execute */
    uint64_t              created_ts_ns;
} node_pipeline_t;

typedef struct {
    uint32_t      executed_stages;
    uint64_t      duration_ns;
    aios_status_t status;
} node_pipeline_exec_result_t;

typedef struct {
    uint32_t      active_count;
    uint32_t      max_pipelines;
    uint64_t      total_executions;
    uint64_t      total_stage_runs;
    uint32_t      denied_count;    /* NodeBit / ownership refusals */
    aios_status_t last_status;
} node_pipeline_snapshot_t;

/* -------------------------------------------------------------------------
 * Syscall argument structures (SYS_PIPE_*)
 * ---------------------------------------------------------------------- */

typedef struct {
    uint16_t  node_id;                          /* owning NodeBit node */
    char      label[NODE_PIPELINE_LABEL_MAX];
    uint32_t *pipeline_id_out;                  /* user pointer */
} syscall_pipe_create_t;

typedef struct {
    uint32_t         pipeline_id;
    uint16_t         node_id;                   /* must match owner */
    pipeline_stage_t stage;
} syscall_pipe_add_stage_t;

typedef struct {
    uint32_t                      pipeline_id;
    uint16_t                      node_id;      /* must match owner */
    node_pipeline_exec_result_t  *result_out;   /* user pointer, optional */
} syscall_pipe_execute_t;

typedef struct {
    uint32_t  pipeline_id;
    uint16_t  node_id;                          /* must match owner */
} syscall_pipe_destroy_t;

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

aios_status_t node_pipeline_init(void);
aios_status_t node_pipeline_create(uint16_t node_id, const char *label,
                                   uint32_t *pipeline_id_out);
aios_status_t node_pipeline_add_stage(uint16_t node_id, uint32_t pipeline_id,
                                      const pipeline_stage_t *stage);
aios_status_t node_pipeline_execute(uint16_t node_id, uint32_t pipeline_id,
                                    node_pipeline_exec_result_t *result_out);
aios_status_t node_pipeline_destroy(uint16_t node_id, uint32_t pipeline_id);
aios_status_t node_pipeline_get(uint32_t pipeline_id, node_pipeline_t *out);
void          node_pipeline_get_snapshot(node_pipeline_snapshot_t *out);
aios_status_t node_pipeline_selftest(void);

#endif /* _AIOS_RUNTIME_NODE_PIPELINE_H */
