/*
 * AIOS Kernel - AI System Call Interface
 * AI-Native Operating System
 *
 * Defines the system call interface for AI applications running on AIOS.
 * Unlike traditional syscalls (read, write, fork, exec), AIOS provides
 * AI-native syscalls for:
 * - Model lifecycle management (load, unload, query)
 * - Tensor operations (create, transform, compute)
 * - Inference execution (submit, wait, cancel)
 * - Training operations (forward, backward, optimize)
 * - Accelerator management (select, configure, monitor)
 * - Pipeline orchestration (chain, parallel, reduce)
 */

#ifndef _AIOS_AI_SYSCALL_H
#define _AIOS_AI_SYSCALL_H

#include <kernel/types.h>
#include <mm/tensor_mm.h>
#include <sched/ai_sched.h>
#include <hal/accel_hal.h>
#include <runtime/autonomy.h>
#include <runtime/slm_orchestrator.h>

/* ============================================================
 * System Call Numbers
 * ============================================================ */

/* Model management syscalls (0x100 - 0x1FF) */
#define SYS_MODEL_LOAD          0x100   /* Load model from storage */
#define SYS_MODEL_UNLOAD        0x101   /* Unload model */
#define SYS_MODEL_INFO          0x102   /* Query model information */
#define SYS_MODEL_LIST          0x103   /* List loaded models */
#define SYS_MODEL_OPTIMIZE      0x104   /* Optimize model (quantize, prune) */

/* Tensor syscalls (0x200 - 0x2FF) */
#define SYS_TENSOR_CREATE       0x200   /* Create tensor */
#define SYS_TENSOR_DESTROY      0x201   /* Destroy tensor */
#define SYS_TENSOR_RESHAPE      0x202   /* Reshape tensor */
#define SYS_TENSOR_COPY         0x203   /* Copy tensor data */
#define SYS_TENSOR_FILL         0x204   /* Fill tensor with value */
#define SYS_TENSOR_INFO         0x205   /* Query tensor info */

/* Inference syscalls (0x300 - 0x3FF) */
#define SYS_INFER_SUBMIT        0x300   /* Submit inference request */
#define SYS_INFER_WAIT          0x301   /* Wait for inference result */
#define SYS_INFER_CANCEL        0x302   /* Cancel inference request */
#define SYS_INFER_BATCH         0x303   /* Submit batch inference */
#define SYS_INFER_STREAM        0x304   /* Streaming inference (token-by-token) */

/* Training syscalls (0x400 - 0x4FF) */
#define SYS_TRAIN_FORWARD       0x400   /* Forward pass */
#define SYS_TRAIN_BACKWARD      0x401   /* Backward pass */
#define SYS_TRAIN_STEP          0x402   /* Optimizer step */
#define SYS_TRAIN_CHECKPOINT    0x403   /* Save checkpoint */
#define SYS_TRAIN_RESTORE       0x404   /* Restore from checkpoint */

/* Accelerator syscalls (0x500 - 0x5FF) */
#define SYS_ACCEL_LIST          0x500   /* List accelerators */
#define SYS_ACCEL_SELECT        0x501   /* Select accelerator for task */
#define SYS_ACCEL_SYNC          0x502   /* Synchronize accelerator */
#define SYS_ACCEL_MEMCPY        0x503   /* Device memory copy */
#define SYS_ACCEL_STATS         0x504   /* Get accelerator stats */

/* Pipeline syscalls (0x600 - 0x6FF) */
#define SYS_PIPE_CREATE         0x600   /* Create inference pipeline */
#define SYS_PIPE_ADD_STAGE      0x601   /* Add stage to pipeline */
#define SYS_PIPE_EXECUTE        0x602   /* Execute pipeline */
#define SYS_PIPE_DESTROY        0x603   /* Destroy pipeline */

/* System info syscalls (0x700 - 0x7FF) */
#define SYS_INFO_MEMORY         0x700   /* Get memory statistics */
#define SYS_INFO_SCHEDULER      0x701   /* Get scheduler statistics */
#define SYS_INFO_SYSTEM         0x702   /* Get system information */

/* Autonomy control syscalls (0x710 - 0x71F) */
#define SYS_AUTONOMY_ACTION_PROPOSE 0x710  /* Propose policy action */
#define SYS_AUTONOMY_ACTION_COMMIT  0x711  /* Commit next approved action */
#define SYS_AUTONOMY_ROLLBACK_LAST  0x712  /* Rollback last committed action */
#define SYS_AUTONOMY_STATS          0x713  /* Read autonomy statistics */
#define SYS_AUTONOMY_MODE_SET       0x714  /* Set observation/safe mode */
#define SYS_AUTONOMY_TELEMETRY_LAST 0x715  /* Read latest telemetry frame */

/* SLM hardware orchestration syscalls (0x720 - 0x72F) */
#define SYS_SLM_HW_SNAPSHOT         0x720  /* Read hardware snapshot for SLM */
#define SYS_SLM_PLAN_SUBMIT         0x721  /* Submit hardware driver plan */
#define SYS_SLM_PLAN_APPLY          0x722  /* Apply validated hardware plan */
#define SYS_SLM_PLAN_STATUS         0x723  /* Query plan state */
#define SYS_SLM_PLAN_LIST           0x724  /* Enumerate queued plans */

/* ============================================================
 * Syscall Argument Structures
 * ============================================================ */

/* Model load request */
typedef struct {
    const char      *path;          /* Model file path */
    model_id_t      *model_id_out;  /* Output: assigned model ID */
    accel_id_t      target_accel;   /* Target accelerator */
    tensor_dtype_t  dtype;          /* Desired precision */
    bool            quantize;       /* Auto-quantize on load */
} syscall_model_load_t;

/* Inference request */
typedef struct {
    model_id_t      model_id;       /* Model to use */
    tensor_id_t     input_tensor;   /* Input tensor ID */
    tensor_id_t     *output_tensor; /* Output: result tensor ID */
    uint32_t        max_tokens;     /* Max output tokens (for LLM) */
    float           temperature;    /* Sampling temperature */
    float           top_p;          /* Top-p sampling */
    bool            stream;         /* Enable streaming output */
} syscall_infer_t;

/* Training step request */
typedef struct {
    model_id_t      model_id;       /* Model to train */
    tensor_id_t     input_tensor;   /* Training input */
    tensor_id_t     label_tensor;   /* Training labels */
    float           learning_rate;  /* Learning rate */
    uint32_t        batch_size;     /* Batch size */
} syscall_train_t;

/* Pipeline stage */
typedef struct {
    model_id_t      model_id;       /* Model for this stage */
    tensor_id_t     input_tensor;   /* Input tensor */
    tensor_id_t     output_tensor;  /* Output tensor */
    accel_id_t      accel_id;       /* Accelerator for this stage */
} pipeline_stage_t;

/* Autonomy mode control */
typedef struct {
    bool observation_only;
    bool safe_mode;
} syscall_autonomy_mode_t;

/* ============================================================
 * Model Registry
 * ============================================================ */

/* Model information */
typedef struct {
    model_id_t      id;             /* Model ID */
    char            name[64];       /* Model name */
    char            arch[32];       /* Architecture (transformer, cnn, etc.) */
    uint64_t        param_count;    /* Number of parameters */
    uint64_t        memory_usage;   /* Current memory usage */
    tensor_dtype_t  dtype;          /* Current precision */
    accel_id_t      accel_id;       /* Loaded on which accelerator */
    bool            loaded;         /* Whether model is loaded */
    uint64_t        inference_count;/* Total inferences performed */
    uint64_t        avg_latency_ns; /* Average inference latency */
} model_info_t;

/* ============================================================
 * AI Syscall Interface API
 * ============================================================ */

/* Initialization */
aios_status_t ai_syscall_init(void);

/* Syscall dispatcher */
int64_t ai_syscall_dispatch(uint64_t syscall_num, uint64_t arg1,
                            uint64_t arg2, uint64_t arg3,
                            uint64_t arg4, uint64_t arg5);

/* Model management */
aios_status_t sys_model_load(syscall_model_load_t *req);
aios_status_t sys_model_unload(model_id_t id);
aios_status_t sys_model_info(model_id_t id, model_info_t *info);

/* Inference */
aios_status_t sys_infer_submit(syscall_infer_t *req);
aios_status_t sys_infer_wait(task_id_t task_id);
aios_status_t sys_infer_cancel(task_id_t task_id);

/* Training */
aios_status_t sys_train_forward(syscall_train_t *req);
aios_status_t sys_train_backward(model_id_t model_id);
aios_status_t sys_train_step(model_id_t model_id, float learning_rate);

/* System info */
void sys_info_dump(void);

/* Autonomy control */
aios_status_t sys_autonomy_action_propose(autonomy_action_req_t *req);
aios_status_t sys_autonomy_action_commit(policy_eval_t *eval);
aios_status_t sys_autonomy_rollback_last(void);
aios_status_t sys_autonomy_stats(autonomy_stats_t *out);
aios_status_t sys_autonomy_mode_set(syscall_autonomy_mode_t *mode);
aios_status_t sys_autonomy_telemetry_last(telemetry_frame_t *out);

/* SLM orchestration */
aios_status_t sys_slm_hw_snapshot(slm_hw_snapshot_t *out);
aios_status_t sys_slm_plan_submit(slm_plan_request_t *req, uint32_t *plan_id_out);
aios_status_t sys_slm_plan_apply(uint32_t plan_id);
aios_status_t sys_slm_plan_status(uint32_t plan_id, slm_plan_t *out);
aios_status_t sys_slm_plan_list(slm_plan_list_t *out);

#endif /* _AIOS_AI_SYSCALL_H */
