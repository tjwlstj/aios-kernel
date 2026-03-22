/*
 * AIOS Kernel - AI Workload Scheduler
 * AI-Native Operating System
 *
 * A scheduler designed specifically for AI workloads with:
 * - Priority classes for different AI task types (training, inference, preprocessing)
 * - Deadline-aware scheduling for real-time inference
 * - GPU/Accelerator affinity tracking
 * - Batch scheduling for training workloads
 * - Preemption support for latency-critical inference
 * - Fair-share scheduling across multiple models
 */

#ifndef _AIOS_AI_SCHED_H
#define _AIOS_AI_SCHED_H

#include <kernel/types.h>

/* Scheduling policy types */
typedef enum {
    SCHED_POLICY_REALTIME   = 0,    /* Real-time inference (lowest latency) */
    SCHED_POLICY_INFERENCE  = 1,    /* Standard inference tasks */
    SCHED_POLICY_TRAINING   = 2,    /* Training workloads (batch-oriented) */
    SCHED_POLICY_PREPROCESS = 3,    /* Data preprocessing / tokenization */
    SCHED_POLICY_BACKGROUND = 4,    /* Background tasks (model loading, etc.) */
    SCHED_POLICY_IDLE       = 5,    /* Idle task */
} sched_policy_t;

/* Task states */
typedef enum {
    TASK_STATE_READY        = 0,    /* Ready to run */
    TASK_STATE_RUNNING      = 1,    /* Currently executing */
    TASK_STATE_WAITING_GPU  = 2,    /* Waiting for GPU/accelerator */
    TASK_STATE_WAITING_MEM  = 3,    /* Waiting for memory allocation */
    TASK_STATE_WAITING_IO   = 4,    /* Waiting for I/O (model loading) */
    TASK_STATE_WAITING_DATA = 5,    /* Waiting for input data */
    TASK_STATE_SUSPENDED    = 6,    /* Suspended by user/system */
    TASK_STATE_COMPLETED    = 7,    /* Task completed */
    TASK_STATE_FAILED       = 8,    /* Task failed */
} task_state_t;

/* AI workload type */
typedef enum {
    WORKLOAD_INFERENCE      = 0,    /* Model inference */
    WORKLOAD_TRAINING       = 1,    /* Model training */
    WORKLOAD_FINE_TUNING    = 2,    /* Fine-tuning / transfer learning */
    WORKLOAD_TOKENIZE       = 3,    /* Tokenization */
    WORKLOAD_EMBEDDING      = 4,    /* Embedding generation */
    WORKLOAD_ATTENTION      = 5,    /* Attention computation */
    WORKLOAD_MATMUL         = 6,    /* Matrix multiplication */
    WORKLOAD_CONV           = 7,    /* Convolution */
    WORKLOAD_CUSTOM         = 8,    /* Custom kernel */
} workload_type_t;

/* Accelerator affinity */
typedef struct {
    uint32_t    preferred_accel;    /* Preferred accelerator ID */
    uint32_t    accel_mask;         /* Bitmask of acceptable accelerators */
    bool        strict_affinity;    /* Must run on preferred accelerator */
} accel_affinity_t;

/* Scheduling parameters */
typedef struct {
    sched_policy_t      policy;         /* Scheduling policy */
    priority_t          priority;       /* Priority (0=highest, 255=lowest) */
    uint64_t            deadline_ns;    /* Deadline in nanoseconds (0=none) */
    uint64_t            time_slice_ns;  /* Time slice in nanoseconds */
    uint32_t            batch_size;     /* Batch size for batched scheduling */
    accel_affinity_t    affinity;       /* Accelerator affinity */
} sched_params_t;

/* AI Task Control Block (TCB) */
typedef struct ai_task {
    /* Identity */
    task_id_t           id;             /* Unique task ID */
    pid_t               pid;            /* Process ID */
    const char          *name;          /* Task name */
    
    /* State */
    task_state_t        state;          /* Current state */
    workload_type_t     workload;       /* Workload type */
    
    /* Scheduling */
    sched_params_t      sched;          /* Scheduling parameters */
    uint64_t            vruntime;       /* Virtual runtime (for fair scheduling) */
    uint64_t            exec_time;      /* Total execution time */
    uint64_t            wait_time;      /* Total wait time */
    uint64_t            start_time;     /* Task start timestamp */
    uint64_t            last_scheduled; /* Last time this task was scheduled */
    
    /* Resource tracking */
    model_id_t          model_id;       /* Associated model */
    accel_id_t          accel_id;       /* Assigned accelerator */
    uint64_t            mem_usage;      /* Current memory usage */
    uint64_t            compute_ops;    /* Estimated compute operations */
    
    /* Batch scheduling */
    uint32_t            current_batch;  /* Current batch index */
    uint32_t            total_batches;  /* Total batches */
    
    /* Linked list for run queues */
    struct ai_task      *next;
    struct ai_task      *prev;
} ai_task_t;

/* Scheduler statistics */
typedef struct {
    uint64_t    total_tasks;        /* Total tasks created */
    uint64_t    active_tasks;       /* Currently active tasks */
    uint64_t    completed_tasks;    /* Completed tasks */
    uint64_t    failed_tasks;       /* Failed tasks */
    uint64_t    context_switches;   /* Total context switches */
    uint64_t    preemptions;        /* Total preemptions */
    uint64_t    avg_latency_ns;     /* Average scheduling latency */
    uint64_t    avg_wait_ns;        /* Average wait time */
    uint64_t    gpu_utilization;    /* GPU utilization percentage */
} sched_stats_t;

/* ============================================================
 * AI Scheduler API
 * ============================================================ */

/* Initialization */
aios_status_t ai_sched_init(void);

/* Task management */
aios_status_t ai_task_create(const char *name, workload_type_t workload,
                             sched_params_t *params, ai_task_t **out);
aios_status_t ai_task_destroy(task_id_t id);
aios_status_t ai_task_submit(task_id_t id);
aios_status_t ai_task_suspend(task_id_t id);
aios_status_t ai_task_resume(task_id_t id);
aios_status_t ai_task_set_priority(task_id_t id, priority_t priority);

/* Scheduling operations */
ai_task_t *ai_sched_pick_next(void);
void ai_sched_tick(void);
void ai_sched_yield(void);
void ai_sched_preempt(task_id_t id);

/* Batch scheduling */
aios_status_t ai_sched_batch_submit(ai_task_t **tasks, uint32_t count);

/* Statistics */
void ai_sched_stats(sched_stats_t *stats);
void ai_sched_dump(void);

/* Default scheduling parameters */
void ai_sched_default_params(sched_policy_t policy, sched_params_t *params);

#endif /* _AIOS_AI_SCHED_H */
