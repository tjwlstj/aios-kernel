/*
 * AIOS Kernel - AI System Call Interface Implementation
 * AI-Native Operating System
 *
 * This module implements the AI-native system call interface.
 * It provides the bridge between user-space AI applications
 * and kernel-level AI services.
 *
 * The syscall dispatcher routes incoming requests to the
 * appropriate kernel subsystem (memory manager, scheduler,
 * accelerator HAL, or model registry).
 */

#include <runtime/ai_syscall.h>
#include <drivers/vga.h>
#include <runtime/autonomy.h>

/* ============================================================
 * Model Registry
 * ============================================================ */

#define MAX_MODELS_REGISTRY 64

static model_info_t model_registry[MAX_MODELS_REGISTRY];
static uint32_t model_count = 0;
static model_id_t next_model_id = 1;

/* Syscall statistics */
static struct {
    uint64_t total_calls;
    uint64_t model_calls;
    uint64_t tensor_calls;
    uint64_t infer_calls;
    uint64_t train_calls;
    uint64_t accel_calls;
    uint64_t pipe_calls;
    uint64_t info_calls;
    uint64_t invalid_calls;
} syscall_stats;

/* ============================================================
 * Internal Helpers
 * ============================================================ */

static model_info_t *find_model(model_id_t id) {
    for (uint32_t i = 0; i < model_count; i++) {
        if (model_registry[i].id == id) {
            return &model_registry[i];
        }
    }
    return NULL;
}

static void copy_str(char *dst, const char *src, uint32_t max_len) {
    uint32_t i = 0;
    while (src[i] && i < max_len - 1) {
        dst[i] = src[i];
        i++;
    }
    dst[i] = '\0';
}

/* ============================================================
 * Public API Implementation
 * ============================================================ */

aios_status_t ai_syscall_init(void) {
    /* Initialize model registry */
    model_count = 0;
    next_model_id = 1;

    /* Clear statistics */
    syscall_stats.total_calls = 0;
    syscall_stats.model_calls = 0;
    syscall_stats.tensor_calls = 0;
    syscall_stats.infer_calls = 0;
    syscall_stats.train_calls = 0;
    syscall_stats.accel_calls = 0;
    syscall_stats.pipe_calls = 0;
    syscall_stats.info_calls = 0;
    syscall_stats.invalid_calls = 0;

    kprintf("\n");
    kprintf("    AI System Call Interface initialized:\n");
    kprintf("    Syscall ranges:\n");
    kprintf("      Model Management:  0x100 - 0x1FF\n");
    kprintf("      Tensor Operations: 0x200 - 0x2FF\n");
    kprintf("      Inference:         0x300 - 0x3FF\n");
    kprintf("      Training:          0x400 - 0x4FF\n");
    kprintf("      Accelerator:       0x500 - 0x5FF\n");
    kprintf("      Pipeline:          0x600 - 0x6FF\n");
    kprintf("      System Info:       0x700 - 0x7FF\n");
    kprintf("    Max Models: %u\n", (uint64_t)MAX_MODELS_REGISTRY);

    return AIOS_OK;
}

/* Main syscall dispatcher - called from interrupt handler */
int64_t ai_syscall_dispatch(uint64_t syscall_num, uint64_t arg1,
                            uint64_t arg2, uint64_t arg3,
                            uint64_t arg4, uint64_t arg5) {
    syscall_stats.total_calls++;

    /* Continuous telemetry feed for autonomous control loop */
    autonomy_collect_telemetry(model_count, accel_get_count());

    /* Route based on syscall number range */
    uint32_t category = (syscall_num >> 8) & 0xFF;

    switch (category) {
        case 0x01: /* Model management */
            syscall_stats.model_calls++;
            switch (syscall_num) {
                case SYS_MODEL_LOAD:
                    return (int64_t)sys_model_load((syscall_model_load_t *)arg1);
                case SYS_MODEL_UNLOAD:
                    return (int64_t)sys_model_unload((model_id_t)arg1);
                case SYS_MODEL_INFO:
                    return (int64_t)sys_model_info((model_id_t)arg1,
                                                    (model_info_t *)arg2);
                default:
                    break;
            }
            break;

        case 0x02: /* Tensor operations */
            syscall_stats.tensor_calls++;
            switch (syscall_num) {
                case SYS_TENSOR_CREATE: {
                    tensor_shape_t *shape = (tensor_shape_t *)arg1;
                    mem_region_type_t region = (mem_region_type_t)arg2;
                    tensor_alloc_t *out = (tensor_alloc_t *)arg3;
                    return (int64_t)tensor_alloc(shape, region, out);
                }
                case SYS_TENSOR_DESTROY:
                    return (int64_t)tensor_free((tensor_id_t)arg1);
                default:
                    break;
            }
            break;

        case 0x03: /* Inference */
            syscall_stats.infer_calls++;
            switch (syscall_num) {
                case SYS_INFER_SUBMIT:
                    return (int64_t)sys_infer_submit((syscall_infer_t *)arg1);
                case SYS_INFER_WAIT:
                    return (int64_t)sys_infer_wait((task_id_t)arg1);
                case SYS_INFER_CANCEL:
                    return (int64_t)sys_infer_cancel((task_id_t)arg1);
                default:
                    break;
            }
            break;

        case 0x04: /* Training */
            syscall_stats.train_calls++;
            switch (syscall_num) {
                case SYS_TRAIN_FORWARD:
                    return (int64_t)sys_train_forward((syscall_train_t *)arg1);
                case SYS_TRAIN_BACKWARD:
                    return (int64_t)sys_train_backward((model_id_t)arg1);
                default:
                    break;
            }
            break;

        case 0x05: /* Accelerator */
            syscall_stats.accel_calls++;
            switch (syscall_num) {
                case SYS_ACCEL_LIST: {
                    uint32_t count = accel_get_count();
                    return (int64_t)count;
                }
                case SYS_ACCEL_SYNC:
                    return (int64_t)accel_sync((accel_id_t)arg1);
                default:
                    break;
            }
            break;

        case 0x07: /* System info */
            syscall_stats.info_calls++;
            switch (syscall_num) {
                case SYS_INFO_MEMORY: {
                    mem_stats_t *stats = (mem_stats_t *)arg1;
                    tensor_mm_stats(stats);
                    return AIOS_OK;
                }
                case SYS_INFO_SCHEDULER: {
                    sched_stats_t *stats = (sched_stats_t *)arg1;
                    ai_sched_stats(stats);
                    return AIOS_OK;
                }
                case SYS_INFO_SYSTEM:
                    sys_info_dump();
                    return AIOS_OK;
                default:
                    break;
            }
            break;

        default:
            syscall_stats.invalid_calls++;
            return (int64_t)AIOS_ERR_NOSYS;
    }

    syscall_stats.invalid_calls++;
    return (int64_t)AIOS_ERR_NOSYS;
}

/* ============================================================
 * Model Management Implementation
 * ============================================================ */

aios_status_t sys_model_load(syscall_model_load_t *req) {
    if (!req || !req->path || !req->model_id_out) return AIOS_ERR_INVAL;
    if (model_count >= MAX_MODELS_REGISTRY) return AIOS_ERR_NOMEM;

    model_info_t *model = &model_registry[model_count];
    model->id = next_model_id++;
    copy_str(model->name, req->path, 64);
    copy_str(model->arch, "transformer", 32); /* Default architecture */
    model->param_count = 0;
    model->memory_usage = 0;
    model->dtype = req->dtype;
    model->accel_id = req->target_accel;
    model->loaded = true;
    model->inference_count = 0;
    model->avg_latency_ns = 0;

    /* Allocate memory for model weights */
    tensor_alloc_t alloc;
    aios_status_t status = model_alloc(model->id, MB(64), &alloc);
    if (status != AIOS_OK) {
        return status;
    }
    model->memory_usage = alloc.size;

    *req->model_id_out = model->id;
    model_count++;

    kprintf("[SYSCALL] Model loaded: '%s' (ID=%u, mem=%u MB)\n",
        model->name, (uint64_t)model->id,
        model->memory_usage / MB(1));

    return AIOS_OK;
}

aios_status_t sys_model_unload(model_id_t id) {
    model_info_t *model = find_model(id);
    if (!model || !model->loaded) return AIOS_ERR_INVAL;

    model_free(id);
    model->loaded = false;
    model->memory_usage = 0;

    kprintf("[SYSCALL] Model unloaded: '%s' (ID=%u)\n",
        model->name, (uint64_t)model->id);

    return AIOS_OK;
}

aios_status_t sys_model_info(model_id_t id, model_info_t *info) {
    if (!info) return AIOS_ERR_INVAL;

    model_info_t *model = find_model(id);
    if (!model) return AIOS_ERR_INVAL;

    *info = *model;
    return AIOS_OK;
}

/* ============================================================
 * Inference Implementation
 * ============================================================ */

aios_status_t sys_infer_submit(syscall_infer_t *req) {
    if (!req) return AIOS_ERR_INVAL;

    model_info_t *model = find_model(req->model_id);
    if (!model || !model->loaded) return AIOS_ERR_INVAL;

    /* Create an AI task for this inference */
    sched_params_t params;
    ai_sched_default_params(SCHED_POLICY_INFERENCE, &params);
    params.affinity.preferred_accel = model->accel_id;

    ai_task_t *task;
    aios_status_t status = ai_task_create("inference",
        WORKLOAD_INFERENCE, &params, &task);
    if (status != AIOS_OK) return status;

    task->model_id = req->model_id;

    /* Submit task to scheduler */
    status = ai_task_submit(task->id);
    if (status != AIOS_OK) return status;

    model->inference_count++;

    kprintf("[SYSCALL] Inference submitted: model=%u task=%u\n",
        (uint64_t)req->model_id, (uint64_t)task->id);

    return AIOS_OK;
}

aios_status_t sys_infer_wait(task_id_t task_id) {
    /* In a full implementation, this would block until task completes */
    kprintf("[SYSCALL] Waiting for inference task %u\n", (uint64_t)task_id);
    return AIOS_OK;
}

aios_status_t sys_infer_cancel(task_id_t task_id) {
    return ai_task_destroy(task_id);
}

/* ============================================================
 * Training Implementation
 * ============================================================ */

aios_status_t sys_train_forward(syscall_train_t *req) {
    if (!req) return AIOS_ERR_INVAL;

    model_info_t *model = find_model(req->model_id);
    if (!model || !model->loaded) return AIOS_ERR_INVAL;

    /* Create training task */
    sched_params_t params;
    ai_sched_default_params(SCHED_POLICY_TRAINING, &params);
    params.batch_size = req->batch_size;

    ai_task_t *task;
    aios_status_t status = ai_task_create("train_forward",
        WORKLOAD_TRAINING, &params, &task);
    if (status != AIOS_OK) return status;

    task->model_id = req->model_id;
    status = ai_task_submit(task->id);

    kprintf("[SYSCALL] Training forward pass: model=%u batch=%u\n",
        (uint64_t)req->model_id, (uint64_t)req->batch_size);

    return status;
}

aios_status_t sys_train_backward(model_id_t model_id) {
    model_info_t *model = find_model(model_id);
    if (!model || !model->loaded) return AIOS_ERR_INVAL;

    sched_params_t params;
    ai_sched_default_params(SCHED_POLICY_TRAINING, &params);

    ai_task_t *task;
    aios_status_t status = ai_task_create("train_backward",
        WORKLOAD_TRAINING, &params, &task);
    if (status != AIOS_OK) return status;

    task->model_id = model_id;
    status = ai_task_submit(task->id);

    kprintf("[SYSCALL] Training backward pass: model=%u\n",
        (uint64_t)model_id);

    return status;
}

aios_status_t sys_train_step(model_id_t model_id, float learning_rate) {
    (void)learning_rate;
    kprintf("[SYSCALL] Optimizer step: model=%u lr=...\n",
        (uint64_t)model_id);
    return AIOS_OK;
}

/* ============================================================
 * System Info
 * ============================================================ */

void sys_info_dump(void) {
    kprintf("\n=== AIOS System Information ===\n");
    kprintf("Syscall Statistics:\n");
    kprintf("  Total calls:    %u\n", syscall_stats.total_calls);
    kprintf("  Model calls:    %u\n", syscall_stats.model_calls);
    kprintf("  Tensor calls:   %u\n", syscall_stats.tensor_calls);
    kprintf("  Inference calls: %u\n", syscall_stats.infer_calls);
    kprintf("  Training calls: %u\n", syscall_stats.train_calls);
    kprintf("  Accel calls:    %u\n", syscall_stats.accel_calls);
    kprintf("  Pipeline calls: %u\n", syscall_stats.pipe_calls);
    kprintf("  Info calls:     %u\n", syscall_stats.info_calls);
    kprintf("  Invalid calls:  %u\n", syscall_stats.invalid_calls);

    kprintf("\nLoaded Models: %u\n", (uint64_t)model_count);
    for (uint32_t i = 0; i < model_count; i++) {
        if (model_registry[i].loaded) {
            kprintf("  [%u] %s (%s) - %u MB, %u inferences\n",
                (uint64_t)model_registry[i].id,
                model_registry[i].name,
                model_registry[i].arch,
                model_registry[i].memory_usage / MB(1),
                model_registry[i].inference_count);
        }
    }
    kprintf("================================\n");

    /* Also dump subsystem info */
    tensor_mm_dump();
    ai_sched_dump();
    accel_hal_dump();
    autonomy_dump();
}
