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
#include <drivers/pci_core.h>
#include <kernel/acpi.h>
#include <kernel/health.h>
#include <kernel/time.h>
#include <mm/memory_fabric.h>
#include <runtime/autonomy.h>
#include <runtime/slm_orchestrator.h>

/* ============================================================
 * Model Registry
 * ============================================================ */

#define MAX_MODELS_REGISTRY 64
#define MAX_INFER_RINGS     16

static model_info_t model_registry[MAX_MODELS_REGISTRY];
static uint32_t model_count = 0;
static model_id_t next_model_id = 1;

typedef struct {
    bool                    registered;
    uint32_t                ring_id;
    uint32_t                notify_count;
    uint32_t                last_submit_tail;
    uint64_t                last_notify_ns;
    ai_ring_registration_t  registration;
} infer_ring_state_t;

static infer_ring_state_t infer_ring_table[MAX_INFER_RINGS];
static uint32_t infer_ring_count = 0;

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

static infer_ring_state_t *find_infer_ring(uint32_t ring_id) {
    for (uint32_t i = 0; i < infer_ring_count; i++) {
        if (infer_ring_table[i].registered && infer_ring_table[i].ring_id == ring_id) {
            return &infer_ring_table[i];
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
    infer_ring_count = 0;
    for (uint32_t i = 0; i < MAX_INFER_RINGS; i++) {
        infer_ring_table[i].registered = false;
        infer_ring_table[i].ring_id = 0;
        infer_ring_table[i].notify_count = 0;
        infer_ring_table[i].last_submit_tail = 0;
        infer_ring_table[i].last_notify_ns = 0;
    }

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
                case SYS_INFER_RING_SETUP:
                    return (int64_t)sys_infer_ring_setup((syscall_infer_ring_setup_t *)arg1);
                case SYS_INFER_RING_NOTIFY:
                    return (int64_t)sys_infer_ring_notify((syscall_infer_ring_notify_t *)arg1);
                case SYS_INFER_RING_WAIT_CQ:
                    return (int64_t)sys_infer_ring_wait_cq((syscall_infer_ring_wait_t *)arg1);
                case SYS_INFER_RING_STATUS:
                    return (int64_t)sys_infer_ring_status((uint32_t)arg1,
                                                          (syscall_infer_ring_status_t *)arg2);
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
                case SYS_INFO_HEALTH:
                    return (int64_t)sys_info_health((kernel_health_summary_t *)arg1);
                case SYS_AUTONOMY_ACTION_PROPOSE:
                    return (int64_t)sys_autonomy_action_propose((autonomy_action_req_t *)arg1);
                case SYS_AUTONOMY_ACTION_COMMIT:
                    return (int64_t)sys_autonomy_action_commit((policy_eval_t *)arg1);
                case SYS_AUTONOMY_ROLLBACK_LAST:
                    return (int64_t)sys_autonomy_rollback_last();
                case SYS_AUTONOMY_STATS:
                    return (int64_t)sys_autonomy_stats((autonomy_stats_t *)arg1);
                case SYS_AUTONOMY_MODE_SET:
                    return (int64_t)sys_autonomy_mode_set((syscall_autonomy_mode_t *)arg1);
                case SYS_AUTONOMY_TELEMETRY_LAST:
                    return (int64_t)sys_autonomy_telemetry_last((telemetry_frame_t *)arg1);
                case SYS_SLM_HW_SNAPSHOT:
                    return (int64_t)sys_slm_hw_snapshot((slm_hw_snapshot_t *)arg1);
                case SYS_SLM_PLAN_SUBMIT:
                    return (int64_t)sys_slm_plan_submit((slm_plan_request_t *)arg1,
                                                        (uint32_t *)arg2);
                case SYS_SLM_PLAN_APPLY:
                    return (int64_t)sys_slm_plan_apply((uint32_t)arg1);
                case SYS_SLM_PLAN_STATUS:
                    return (int64_t)sys_slm_plan_status((uint32_t)arg1,
                                                        (slm_plan_t *)arg2);
                case SYS_SLM_PLAN_LIST:
                    return (int64_t)sys_slm_plan_list((slm_plan_list_t *)arg1);
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

aios_status_t sys_infer_ring_setup(syscall_infer_ring_setup_t *req) {
    infer_ring_state_t *ring;

    if (!req || !req->ring_id_out) return AIOS_ERR_INVAL;
    if (req->registration.abi_version != AI_RING_ABI_VERSION) return AIOS_ERR_INVAL;
    if (!ai_ring_valid_entries(req->registration.submit_entries)) return AIOS_ERR_INVAL;
    if (!ai_ring_valid_entries(req->registration.completion_entries)) return AIOS_ERR_INVAL;
    if (infer_ring_count >= MAX_INFER_RINGS) return AIOS_ERR_NOMEM;

    ring = &infer_ring_table[infer_ring_count];
    ring->registered = true;
    ring->ring_id = infer_ring_count + 1;
    ring->notify_count = 0;
    ring->last_submit_tail = 0;
    ring->last_notify_ns = 0;
    ring->registration = req->registration;
    infer_ring_count++;

    *req->ring_id_out = ring->ring_id;

    kprintf("[SYSCALL] Inference ring registered: id=%u sq=%u cq=%u flags=%u\n",
        (uint64_t)ring->ring_id,
        (uint64_t)ring->registration.submit_entries,
        (uint64_t)ring->registration.completion_entries,
        (uint64_t)ring->registration.flags);

    return AIOS_OK;
}

aios_status_t sys_infer_ring_notify(syscall_infer_ring_notify_t *req) {
    infer_ring_state_t *ring;

    if (!req) return AIOS_ERR_INVAL;

    ring = find_infer_ring(req->ring_id);
    if (!ring) return AIOS_ERR_INVAL;

    ring->notify_count++;
    ring->last_submit_tail = req->submit_tail;
    ring->last_notify_ns = kernel_time_monotonic_ns();

    kprintf("[SYSCALL] Inference ring notify: id=%u tail=%u flags=%u\n",
        (uint64_t)req->ring_id,
        (uint64_t)req->submit_tail,
        (uint64_t)req->flags);

    return AIOS_OK;
}

aios_status_t sys_infer_ring_wait_cq(syscall_infer_ring_wait_t *req) {
    if (!req) return AIOS_ERR_INVAL;
    if (!find_infer_ring(req->ring_id)) return AIOS_ERR_INVAL;

    kprintf("[SYSCALL] Inference ring wait pending implementation: id=%u timeout=%u ns\n",
        (uint64_t)req->ring_id,
        req->timeout_ns);

    return AIOS_ERR_NOSYS;
}

aios_status_t sys_infer_ring_status(uint32_t ring_id, syscall_infer_ring_status_t *out) {
    infer_ring_state_t *ring;

    if (!out) return AIOS_ERR_INVAL;

    ring = find_infer_ring(ring_id);
    if (!ring) return AIOS_ERR_INVAL;

    out->ring_id = ring->ring_id;
    out->registered = ring->registered;
    out->notify_count = ring->notify_count;
    out->registration = ring->registration;

    return AIOS_OK;
}

void ai_infer_ring_runtime(ai_ring_runtime_snapshot_t *out) {
    uint32_t total_notifies = 0;
    uint32_t max_submit_tail = 0;
    uint16_t active = 0;
    uint16_t last_ring_id = 0;
    uint64_t last_notify_ns = 0;
    bool any_event = false;
    bool any_shared_kv = false;

    if (!out) {
        return;
    }

    out->registered_rings = 0;
    out->active_rings = 0;
    out->total_notifies = 0;
    out->max_submit_tail_observed = 0;
    out->last_ring_id = 0;
    out->last_notify_ns = 0;
    out->any_event_notify = false;
    out->any_shared_kv = false;

    for (uint32_t i = 0; i < infer_ring_count; i++) {
        infer_ring_state_t *ring = &infer_ring_table[i];
        if (!ring->registered) {
            continue;
        }

        out->registered_rings++;
        total_notifies += ring->notify_count;
        if (ring->notify_count > 0) {
            active++;
        }
        if (ring->last_submit_tail > max_submit_tail) {
            max_submit_tail = ring->last_submit_tail;
        }
        if (ring->last_notify_ns >= last_notify_ns) {
            last_notify_ns = ring->last_notify_ns;
            last_ring_id = (uint16_t)ring->ring_id;
        }
        if ((ring->registration.flags & AI_RING_REG_F_EVENT_NOTIFY) != 0) {
            any_event = true;
        }
        if ((ring->registration.flags & AI_RING_REG_F_SHARED_KV) != 0) {
            any_shared_kv = true;
        }
    }

    out->active_rings = active;
    out->total_notifies = total_notifies;
    out->max_submit_tail_observed = max_submit_tail;
    out->last_ring_id = last_ring_id;
    out->last_notify_ns = last_notify_ns;
    out->any_event_notify = any_event;
    out->any_shared_kv = any_shared_kv;
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
 * Autonomy Control
 * ============================================================ */

aios_status_t sys_autonomy_action_propose(autonomy_action_req_t *req) {
    if (!req) return AIOS_ERR_INVAL;
    return autonomy_action_propose_req(req);
}

aios_status_t sys_autonomy_action_commit(policy_eval_t *eval) {
    return autonomy_action_commit_next(eval);
}

aios_status_t sys_autonomy_rollback_last(void) {
    return autonomy_action_rollback_last();
}

aios_status_t sys_autonomy_stats(autonomy_stats_t *out) {
    if (!out) return AIOS_ERR_INVAL;
    autonomy_stats(out);
    return AIOS_OK;
}

aios_status_t sys_autonomy_mode_set(syscall_autonomy_mode_t *mode) {
    if (!mode) return AIOS_ERR_INVAL;
    autonomy_set_safe_mode(mode->safe_mode);
    autonomy_set_observation_only(mode->observation_only);
    return AIOS_OK;
}

aios_status_t sys_autonomy_telemetry_last(telemetry_frame_t *out) {
    return autonomy_get_latest_telemetry(out);
}

aios_status_t sys_slm_hw_snapshot(slm_hw_snapshot_t *out) {
    return slm_snapshot_read(out);
}

aios_status_t sys_slm_plan_submit(slm_plan_request_t *req, uint32_t *plan_id_out) {
    if (!req) return AIOS_ERR_INVAL;
    return slm_plan_submit(req, plan_id_out);
}

aios_status_t sys_slm_plan_apply(uint32_t plan_id) {
    return slm_plan_apply(plan_id);
}

aios_status_t sys_slm_plan_status(uint32_t plan_id, slm_plan_t *out) {
    return slm_plan_get(plan_id, out);
}

aios_status_t sys_slm_plan_list(slm_plan_list_t *out) {
    return slm_plan_list(out);
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
    kprintf("  Registered rings: %u\n", (uint64_t)infer_ring_count);
    {
        ai_ring_runtime_snapshot_t ring_runtime;
        ai_infer_ring_runtime(&ring_runtime);
        kprintf("  Ring runtime: registered=%u active=%u notify=%u max_tail=%u last_ring=%u event=%u shared_kv=%u\n",
            (uint64_t)ring_runtime.registered_rings,
            (uint64_t)ring_runtime.active_rings,
            (uint64_t)ring_runtime.total_notifies,
            (uint64_t)ring_runtime.max_submit_tail_observed,
            (uint64_t)ring_runtime.last_ring_id,
            (uint64_t)ring_runtime.any_event_notify,
            (uint64_t)ring_runtime.any_shared_kv);
    }

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
    memory_fabric_dump();
    ai_sched_dump();
    accel_hal_dump();
    acpi_dump();
    pci_core_dump();
    kernel_health_dump();
    autonomy_dump();
    slm_orchestrator_dump();
}

aios_status_t sys_info_health(kernel_health_summary_t *out) {
    if (!out) {
        return AIOS_ERR_INVAL;
    }

    kernel_health_get_summary(out);
    return AIOS_OK;
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
