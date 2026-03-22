/*
 * AIOS Kernel - AI Workload Scheduler Implementation
 * AI-Native Operating System
 *
 * Multi-level feedback queue scheduler optimized for AI workloads:
 * - Level 0: Real-time inference (strict deadline, preemptive)
 * - Level 1: Standard inference (low latency, round-robin)
 * - Level 2: Training workloads (high throughput, batch-oriented)
 * - Level 3: Preprocessing (best effort, fair share)
 * - Level 4: Background tasks (lowest priority)
 *
 * Key features:
 * - Virtual runtime tracking for fair scheduling (CFS-inspired)
 * - Deadline-aware scheduling for real-time inference
 * - Accelerator affinity for GPU locality
 * - Dynamic batching for training efficiency
 * - Preemption support for latency-critical tasks
 */

#include <sched/ai_sched.h>
#include <drivers/vga.h>

/* ============================================================
 * Internal State
 * ============================================================ */

#define MAX_TASKS           MAX_AI_TASKS
#define NUM_PRIORITY_LEVELS 6
#define DEFAULT_TIME_SLICE  (10 * 1000000ULL)  /* 10ms in nanoseconds */
#define RT_TIME_SLICE       (1 * 1000000ULL)   /* 1ms for real-time */
#define TRAINING_TIME_SLICE (100 * 1000000ULL) /* 100ms for training */

/* Task pool */
static ai_task_t task_pool[MAX_TASKS];
static uint32_t task_pool_next = 0;
static task_id_t next_task_id = 1;

/* Multi-level run queues (one per priority level) */
static ai_task_t *run_queues[NUM_PRIORITY_LEVELS] = { NULL };
static uint32_t queue_sizes[NUM_PRIORITY_LEVELS] = { 0 };

/* Currently running task */
static ai_task_t *current_task = NULL;

/* Scheduler statistics */
static sched_stats_t sched_stats;

/* Simple tick counter (would be driven by timer interrupt) */
static uint64_t tick_count = 0;

/* ============================================================
 * Internal Helper Functions
 * ============================================================ */

static ai_task_t *alloc_task(void) {
    if (task_pool_next >= MAX_TASKS) return NULL;
    ai_task_t *task = &task_pool[task_pool_next++];
    task->next = NULL;
    task->prev = NULL;
    return task;
}

static ai_task_t *find_task(task_id_t id) {
    for (uint32_t i = 0; i < task_pool_next; i++) {
        if (task_pool[i].id == id) {
            return &task_pool[i];
        }
    }
    return NULL;
}

/* Enqueue task to appropriate run queue */
static void enqueue_task(ai_task_t *task) {
    uint32_t level = task->sched.policy;
    if (level >= NUM_PRIORITY_LEVELS) level = NUM_PRIORITY_LEVELS - 1;

    /* Insert sorted by virtual runtime (lowest vruntime first) */
    ai_task_t **head = &run_queues[level];

    if (*head == NULL || task->vruntime < (*head)->vruntime) {
        task->next = *head;
        task->prev = NULL;
        if (*head) (*head)->prev = task;
        *head = task;
    } else {
        ai_task_t *curr = *head;
        while (curr->next && curr->next->vruntime <= task->vruntime) {
            curr = curr->next;
        }
        task->next = curr->next;
        task->prev = curr;
        if (curr->next) curr->next->prev = task;
        curr->next = task;
    }

    queue_sizes[level]++;
    task->state = TASK_STATE_READY;
}

/* Dequeue task from run queue */
static void dequeue_task(ai_task_t *task) {
    uint32_t level = task->sched.policy;
    if (level >= NUM_PRIORITY_LEVELS) level = NUM_PRIORITY_LEVELS - 1;

    if (task->prev) {
        task->prev->next = task->next;
    } else {
        run_queues[level] = task->next;
    }
    if (task->next) {
        task->next->prev = task->prev;
    }

    task->next = NULL;
    task->prev = NULL;
    queue_sizes[level]--;
}

/* Calculate weight based on policy and priority */
static uint64_t calc_weight(sched_params_t *params) {
    /* Higher priority = lower weight = faster vruntime accumulation
     * This ensures high-priority tasks get more CPU time */
    uint64_t base_weight;
    switch (params->policy) {
        case SCHED_POLICY_REALTIME:   base_weight = 1;    break;
        case SCHED_POLICY_INFERENCE:  base_weight = 4;    break;
        case SCHED_POLICY_TRAINING:   base_weight = 16;   break;
        case SCHED_POLICY_PREPROCESS: base_weight = 32;   break;
        case SCHED_POLICY_BACKGROUND: base_weight = 64;   break;
        default:                      base_weight = 128;  break;
    }
    return base_weight + params->priority;
}

/* Update virtual runtime for a task */
static void update_vruntime(ai_task_t *task, uint64_t delta_ns) {
    uint64_t weight = calc_weight(&task->sched);
    /* vruntime increases slower for higher-weight (lower priority) tasks
     * This gives them proportionally less CPU time */
    task->vruntime += delta_ns / weight;
    task->exec_time += delta_ns;
}

/* ============================================================
 * Public API Implementation
 * ============================================================ */

aios_status_t ai_sched_init(void) {
    /* Initialize all run queues */
    for (uint32_t i = 0; i < NUM_PRIORITY_LEVELS; i++) {
        run_queues[i] = NULL;
        queue_sizes[i] = 0;
    }

    /* Initialize statistics */
    sched_stats.total_tasks = 0;
    sched_stats.active_tasks = 0;
    sched_stats.completed_tasks = 0;
    sched_stats.failed_tasks = 0;
    sched_stats.context_switches = 0;
    sched_stats.preemptions = 0;
    sched_stats.avg_latency_ns = 0;
    sched_stats.avg_wait_ns = 0;
    sched_stats.gpu_utilization = 0;

    current_task = NULL;
    tick_count = 0;

    kprintf("\n");
    kprintf("    AI Workload Scheduler initialized:\n");
    kprintf("    Priority Levels: %u\n", (uint64_t)NUM_PRIORITY_LEVELS);
    kprintf("    Max Tasks: %u\n", (uint64_t)MAX_TASKS);
    kprintf("    Policies: RT-Inference | Inference | Training | Preprocess | Background\n");
    kprintf("    Features: CFS-based fair scheduling, deadline-aware, GPU affinity\n");

    return AIOS_OK;
}

void ai_sched_default_params(sched_policy_t policy, sched_params_t *params) {
    if (!params) return;

    params->policy = policy;
    params->priority = 128;  /* Medium priority */
    params->deadline_ns = 0;
    params->batch_size = 1;
    params->affinity.preferred_accel = 0;
    params->affinity.accel_mask = 0xFFFFFFFF; /* Any accelerator */
    params->affinity.strict_affinity = false;

    switch (policy) {
        case SCHED_POLICY_REALTIME:
            params->priority = 0;
            params->time_slice_ns = RT_TIME_SLICE;
            params->deadline_ns = 5 * 1000000ULL; /* 5ms deadline */
            break;
        case SCHED_POLICY_INFERENCE:
            params->priority = 32;
            params->time_slice_ns = DEFAULT_TIME_SLICE;
            break;
        case SCHED_POLICY_TRAINING:
            params->priority = 128;
            params->time_slice_ns = TRAINING_TIME_SLICE;
            params->batch_size = 32;
            break;
        case SCHED_POLICY_PREPROCESS:
            params->priority = 192;
            params->time_slice_ns = DEFAULT_TIME_SLICE;
            break;
        case SCHED_POLICY_BACKGROUND:
            params->priority = 255;
            params->time_slice_ns = DEFAULT_TIME_SLICE * 2;
            break;
        default:
            params->time_slice_ns = DEFAULT_TIME_SLICE;
            break;
    }
}

aios_status_t ai_task_create(const char *name, workload_type_t workload,
                             sched_params_t *params, ai_task_t **out) {
    if (!params || !out) return AIOS_ERR_INVAL;

    ai_task_t *task = alloc_task();
    if (!task) return AIOS_ERR_NOMEM;

    task->id = next_task_id++;
    task->pid = task->id;  /* Simple 1:1 mapping for now */
    task->name = name;
    task->state = TASK_STATE_READY;
    task->workload = workload;
    task->sched = *params;
    task->vruntime = 0;
    task->exec_time = 0;
    task->wait_time = 0;
    task->start_time = tick_count;
    task->last_scheduled = 0;
    task->model_id = 0;
    task->accel_id = 0;
    task->mem_usage = 0;
    task->compute_ops = 0;
    task->current_batch = 0;
    task->total_batches = params->batch_size;

    sched_stats.total_tasks++;
    sched_stats.active_tasks++;

    *out = task;
    return AIOS_OK;
}

aios_status_t ai_task_destroy(task_id_t id) {
    ai_task_t *task = find_task(id);
    if (!task) return AIOS_ERR_INVAL;

    if (task->state == TASK_STATE_RUNNING) {
        current_task = NULL;
    } else if (task->state == TASK_STATE_READY) {
        dequeue_task(task);
    }

    task->state = TASK_STATE_COMPLETED;
    sched_stats.active_tasks--;
    sched_stats.completed_tasks++;

    return AIOS_OK;
}

aios_status_t ai_task_submit(task_id_t id) {
    ai_task_t *task = find_task(id);
    if (!task) return AIOS_ERR_INVAL;
    if (task->state != TASK_STATE_READY && task->state != TASK_STATE_SUSPENDED)
        return AIOS_ERR_INVAL;

    /* Set initial vruntime to minimum of current tasks in queue
     * to prevent starvation of new tasks */
    uint32_t level = task->sched.policy;
    if (run_queues[level]) {
        task->vruntime = run_queues[level]->vruntime;
    }

    enqueue_task(task);
    return AIOS_OK;
}

aios_status_t ai_task_suspend(task_id_t id) {
    ai_task_t *task = find_task(id);
    if (!task) return AIOS_ERR_INVAL;

    if (task->state == TASK_STATE_RUNNING) {
        current_task = NULL;
    } else if (task->state == TASK_STATE_READY) {
        dequeue_task(task);
    }

    task->state = TASK_STATE_SUSPENDED;
    return AIOS_OK;
}

aios_status_t ai_task_resume(task_id_t id) {
    ai_task_t *task = find_task(id);
    if (!task || task->state != TASK_STATE_SUSPENDED) return AIOS_ERR_INVAL;

    enqueue_task(task);
    return AIOS_OK;
}

aios_status_t ai_task_set_priority(task_id_t id, priority_t priority) {
    ai_task_t *task = find_task(id);
    if (!task) return AIOS_ERR_INVAL;

    bool was_queued = (task->state == TASK_STATE_READY);
    if (was_queued) dequeue_task(task);

    task->sched.priority = priority;

    if (was_queued) enqueue_task(task);
    return AIOS_OK;
}

aios_status_t ai_task_get_priority(task_id_t id, priority_t *out_priority) {
    if (!out_priority) return AIOS_ERR_INVAL;

    ai_task_t *task = find_task(id);
    if (!task) return AIOS_ERR_INVAL;

    *out_priority = task->sched.priority;
    return AIOS_OK;
}

ai_task_t *ai_sched_pick_next(void) {
    /* Priority-based selection: check queues from highest to lowest priority */
    for (uint32_t level = 0; level < NUM_PRIORITY_LEVELS; level++) {
        if (run_queues[level] != NULL) {
            ai_task_t *task = run_queues[level];

            /* For real-time tasks, check deadline */
            if (level == SCHED_POLICY_REALTIME && task->sched.deadline_ns > 0) {
                /* Check if deadline can be met */
                uint64_t remaining = task->sched.deadline_ns;
                if (task->exec_time > remaining) {
                    /* Deadline missed - mark as failed and try next */
                    dequeue_task(task);
                    task->state = TASK_STATE_FAILED;
                    sched_stats.failed_tasks++;
                    sched_stats.active_tasks--;
                    continue;
                }
            }

            dequeue_task(task);
            task->state = TASK_STATE_RUNNING;
            task->last_scheduled = tick_count;

            /* Track context switch */
            if (current_task != task) {
                sched_stats.context_switches++;
            }

            current_task = task;
            return task;
        }
    }

    return NULL; /* No tasks to run */
}

void ai_sched_tick(void) {
    tick_count++;

    if (!current_task) return;

    /* Update virtual runtime */
    update_vruntime(current_task, current_task->sched.time_slice_ns);

    /* Check if time slice expired */
    uint64_t elapsed = tick_count - current_task->last_scheduled;
    if (elapsed * 1000000ULL >= current_task->sched.time_slice_ns) {
        /* Time slice expired - preempt and reschedule */
        ai_task_t *task = current_task;
        current_task = NULL;
        enqueue_task(task);
    }

    /* Check for higher-priority tasks that should preempt */
    if (current_task) {
        uint32_t current_level = current_task->sched.policy;
        for (uint32_t level = 0; level < current_level; level++) {
            if (run_queues[level] != NULL) {
                /* Higher priority task waiting - preempt current */
                ai_task_t *preempted = current_task;
                current_task = NULL;
                enqueue_task(preempted);
                sched_stats.preemptions++;
                break;
            }
        }
    }
}

void ai_sched_yield(void) {
    if (!current_task) return;

    ai_task_t *task = current_task;
    current_task = NULL;
    enqueue_task(task);
}

void ai_sched_preempt(task_id_t id) {
    ai_task_t *task = find_task(id);
    if (!task || task->state != TASK_STATE_RUNNING) return;

    current_task = NULL;
    enqueue_task(task);
    sched_stats.preemptions++;
}

aios_status_t ai_sched_batch_submit(ai_task_t **tasks, uint32_t count) {
    if (!tasks || count == 0) return AIOS_ERR_INVAL;

    for (uint32_t i = 0; i < count; i++) {
        if (tasks[i]) {
            aios_status_t status = ai_task_submit(tasks[i]->id);
            if (status != AIOS_OK) return status;
        }
    }

    return AIOS_OK;
}

void ai_sched_stats(sched_stats_t *stats) {
    if (!stats) return;
    *stats = sched_stats;
}

void ai_sched_dump(void) {
    kprintf("\n=== AI Workload Scheduler Status ===\n");
    kprintf("Total Tasks:      %u\n", sched_stats.total_tasks);
    kprintf("Active Tasks:     %u\n", sched_stats.active_tasks);
    kprintf("Completed:        %u\n", sched_stats.completed_tasks);
    kprintf("Failed:           %u\n", sched_stats.failed_tasks);
    kprintf("Context Switches: %u\n", sched_stats.context_switches);
    kprintf("Preemptions:      %u\n", sched_stats.preemptions);

    kprintf("\nRun Queue Status:\n");
    const char *level_names[] = {
        "RT-Inference", "Inference", "Training",
        "Preprocess", "Background", "Idle"
    };
    for (uint32_t i = 0; i < NUM_PRIORITY_LEVELS; i++) {
        kprintf("  [%s]: %u tasks\n", level_names[i], (uint64_t)queue_sizes[i]);
    }

    if (current_task) {
        kprintf("\nCurrent Task: #%u \"%s\" (vruntime=%u)\n",
            (uint64_t)current_task->id, current_task->name,
            current_task->vruntime);
    } else {
        kprintf("\nCurrent Task: (idle)\n");
    }

    kprintf("====================================\n");
}
