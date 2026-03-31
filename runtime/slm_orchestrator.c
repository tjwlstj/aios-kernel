/*
 * AIOS Kernel - SLM Hardware Orchestrator
 * AI-Native Operating System
 */

#include <runtime/slm_orchestrator.h>
#include <drivers/pci_core.h>
#include <kernel/health.h>
#include <kernel/time.h>
#include <kernel/acpi.h>
#include <drivers/e1000.h>
#include <drivers/storage_host.h>
#include <drivers/usb_host.h>
#include <drivers/serial.h>
#include <drivers/vga.h>
#include <mm/memory_fabric.h>
#include <runtime/ai_syscall.h>
#include <lib/string.h>

static slm_plan_t plan_table[SLM_PLAN_CAP];
static uint32_t next_plan_id = 1;
static slm_learning_profile_t g_learning = {0};

static void compute_fabric_profile(slm_fabric_profile_t *out);
static void compute_agent_main_profile(agent_main_ai_profile_t *out,
    const memory_selftest_result_t *profile,
    const kernel_health_summary_t *health,
    const slm_fabric_profile_t *fabric,
    const slm_io_profile_t *io_profile);
static void compute_agent_pipeline_profile(agent_pipeline_profile_t *out,
    const agent_main_ai_profile_t *main_ai,
    const slm_io_profile_t *io_profile);
static void compute_agent_tree(agent_tree_node_t *nodes, uint32_t *count,
    const agent_main_ai_profile_t *main_ai,
    const agent_pipeline_profile_t *pipeline,
    const slm_io_profile_t *io_profile);

static bool device_is_io_kind(platform_device_kind_t kind) {
    return kind == PLATFORM_DEVICE_ETHERNET ||
           kind == PLATFORM_DEVICE_USB ||
           kind == PLATFORM_DEVICE_STORAGE;
}

static uint8_t clamp_u8(int32_t value) {
    if (value < 0) {
        return 0;
    }
    if (value > 100) {
        return 100;
    }
    return (uint8_t)value;
}

static int8_t clamp_i8(int32_t value, int32_t min_value, int32_t max_value) {
    if (value < min_value) {
        return (int8_t)min_value;
    }
    if (value > max_value) {
        return (int8_t)max_value;
    }
    return (int8_t)value;
}

static uint16_t clamp_u16(int32_t value, uint16_t min_value, uint16_t max_value) {
    if (value < min_value) {
        return min_value;
    }
    if (value > max_value) {
        return max_value;
    }
    return (uint16_t)value;
}

static uint32_t clamp_u32(int32_t value, uint32_t min_value, uint32_t max_value) {
    if (value < 0 || (uint32_t)value < min_value) {
        return min_value;
    }
    if ((uint32_t)value > max_value) {
        return max_value;
    }
    return value;
}

static const platform_device_t *find_first_device(platform_device_kind_t kind) {
    for (uint32_t i = 0; i < platform_probe_count(); i++) {
        const platform_device_t *dev = platform_probe_get(i);
        if (dev && dev->kind == kind) {
            return dev;
        }
    }
    return NULL;
}

static bool plan_target_present(const slm_plan_request_t *req) {
    if (!req) {
        return false;
    }
    if (req->template_id == SLM_TEMPLATE_DISCOVERY) {
        return true;
    }

    for (uint32_t i = 0; i < platform_probe_count(); i++) {
        const platform_device_t *dev = platform_probe_get(i);
        if (!dev) {
            continue;
        }
        if (req->target_kind != PLATFORM_DEVICE_UNKNOWN && dev->kind != req->target_kind) {
            continue;
        }
        if (req->target_vendor_id != 0 && dev->vendor_id != req->target_vendor_id) {
            continue;
        }
        if (req->target_device_id != 0 && dev->device_id != req->target_device_id) {
            continue;
        }
        return true;
    }

    return false;
}

static slm_learning_entry_t *learning_entry(slm_action_t action) {
    if (action < 0 || action >= SLM_ACTION_COUNT) {
        return NULL;
    }
    return &g_learning.actions[action];
}

static void learning_set_confidence(slm_action_t action, uint8_t confidence) {
    slm_learning_entry_t *entry = learning_entry(action);
    if (!entry) {
        return;
    }
    entry->confidence = confidence;
}

static uint8_t learning_get_confidence(slm_action_t action) {
    slm_learning_entry_t *entry = learning_entry(action);
    if (!entry) {
        return 0;
    }
    return entry->confidence;
}

static void learning_recompute_tuning(void) {
    int32_t queue_depth = 16 + (g_learning.io_aggressiveness_bias * 4);
    int32_t poll_budget = 512 + (g_learning.io_aggressiveness_bias * 256);
    int32_t dma_window_kib = 128 + (g_learning.io_aggressiveness_bias * 64);

    if (g_learning.applied_successes > g_learning.applied_failures + 1) {
        queue_depth += 4;
        poll_budget += 128;
        dma_window_kib += 32;
    } else if (g_learning.applied_failures > g_learning.applied_successes) {
        queue_depth -= 4;
        poll_budget -= 128;
        dma_window_kib -= 32;
    }

    if (g_learning.rejected_submissions > 2) {
        queue_depth -= 2;
        poll_budget -= 64;
    }

    g_learning.tuned_queue_depth = clamp_u16(queue_depth, 8, 48);
    g_learning.tuned_poll_budget = clamp_u16(poll_budget, 128, 2048);
    g_learning.tuned_dma_window_kib = clamp_u32(dma_window_kib, 32, 512);
}

static void learning_init(void) {
    for (uint32_t i = 0; i < SLM_ACTION_COUNT; i++) {
        g_learning.actions[i].confidence = 50;
        g_learning.actions[i].last_status = AIOS_OK;
    }

    g_learning.global_confidence = 50;
    g_learning.io_aggressiveness_bias = 0;
    learning_recompute_tuning();
}

static void learning_refresh_boot_observation(void) {
    const memory_selftest_result_t *profile = kernel_memory_selftest_last();
    const platform_probe_summary_t *summary = platform_probe_summary();
    const kernel_health_summary_t *health_ptr = NULL;
    kernel_health_summary_t health;
    slm_fabric_profile_t fabric;
    const platform_device_t *ethernet = find_first_device(PLATFORM_DEVICE_ETHERNET);
    const platform_device_t *usb = find_first_device(PLATFORM_DEVICE_USB);
    const platform_device_t *storage = find_first_device(PLATFORM_DEVICE_STORAGE);
    int32_t global_bias = 0;
    uint32_t ready = 0;

    kernel_health_get_summary(&health);
    health_ptr = &health;
    compute_fabric_profile(&fabric);

    ready += e1000_driver_ready() ? 1U : 0U;
    ready += usb_host_ready() ? 1U : 0U;
    ready += storage_host_ready() ? 1U : 0U;

    g_learning.boot_epoch++;
    g_learning.observed_ready_controllers = ready;
    g_learning.global_confidence = clamp_u8((int32_t)fabric.compatibility_score +
        ((health_ptr->level == KERNEL_STABILITY_STABLE) ? 10 : -10));

    if (profile->tier == BOOT_PERF_TIER_HIGH) {
        global_bias++;
    } else if (profile->tier == BOOT_PERF_TIER_LOW) {
        global_bias--;
    }
    if (!fabric.acpi_ready) {
        global_bias--;
    }
    if (!fabric.ecam_available && fabric.pci_total_functions > 4) {
        global_bias--;
    }
    if (health_ptr->level == KERNEL_STABILITY_STABLE) {
        global_bias++;
    }
    if (summary->matched_devices > ready) {
        global_bias--;
    }

    g_learning.io_aggressiveness_bias = clamp_i8(global_bias, -2, 2);
    learning_recompute_tuning();

    learning_set_confidence(SLM_ACTION_REPROBE_PCI,
        clamp_u8((int32_t)g_learning.global_confidence + (fabric.pci_total_functions > 0 ? 10 : -10)));
    learning_set_confidence(SLM_ACTION_CORE_AUDIT,
        clamp_u8((int32_t)g_learning.global_confidence + 15));
    learning_set_confidence(SLM_ACTION_IO_AUDIT,
        clamp_u8((int32_t)g_learning.global_confidence +
            ((summary->matched_devices > 0) ? 5 : -15)));

    learning_set_confidence(SLM_ACTION_BOOTSTRAP_E1000,
        clamp_u8(ethernet ? (e1000_driver_ready() ? 55 : 72) : 10));
    learning_set_confidence(SLM_ACTION_E1000_DUMP,
        clamp_u8(ethernet ? (e1000_driver_ready() ? 88 : 30) : 5));
    learning_set_confidence(SLM_ACTION_E1000_TX_SMOKE,
        clamp_u8((ethernet && e1000_driver_ready()) ? 78 : 15));

    learning_set_confidence(SLM_ACTION_BOOTSTRAP_USB,
        clamp_u8(usb ? (usb_host_ready() ? 58 : 68) : 10));
    learning_set_confidence(SLM_ACTION_USB_DUMP,
        clamp_u8(usb ? (usb_host_ready() ? 84 : 25) : 5));

    learning_set_confidence(SLM_ACTION_BOOTSTRAP_STORAGE,
        clamp_u8(storage ? (storage_host_ready() ? 58 : 70) : 10));
    learning_set_confidence(SLM_ACTION_STORAGE_DUMP,
        clamp_u8(storage ? (storage_host_ready() ? 84 : 25) : 5));
}

static void learning_record_submission(slm_action_t action, bool accepted) {
    slm_learning_entry_t *entry = learning_entry(action);
    if (!entry) {
        return;
    }

    if (!accepted) {
        entry->rejections++;
        entry->confidence = clamp_u8((int32_t)entry->confidence - 5);
        g_learning.rejected_submissions++;
        if (g_learning.global_confidence > 2) {
            g_learning.global_confidence -= 2;
        }
        learning_recompute_tuning();
    }
}

static void learning_record_result(slm_action_t action, aios_status_t status, uint64_t latency_ns) {
    slm_learning_entry_t *entry = learning_entry(action);
    if (!entry) {
        return;
    }

    entry->attempts++;
    entry->last_status = status;
    entry->last_latency_ns = latency_ns;

    if (status == AIOS_OK) {
        entry->successes++;
        entry->confidence = clamp_u8((int32_t)entry->confidence + 6);
        g_learning.applied_successes++;
        g_learning.global_confidence = clamp_u8((int32_t)g_learning.global_confidence + 2);
        if (g_learning.io_aggressiveness_bias < 2 &&
            (action == SLM_ACTION_E1000_TX_SMOKE ||
             action == SLM_ACTION_BOOTSTRAP_E1000 ||
             action == SLM_ACTION_BOOTSTRAP_USB ||
             action == SLM_ACTION_BOOTSTRAP_STORAGE)) {
            g_learning.io_aggressiveness_bias++;
        }
        learning_recompute_tuning();
        return;
    }

    entry->failures++;
    if (status == AIOS_ERR_TIMEOUT) {
        entry->timeouts++;
    }
    entry->confidence = clamp_u8((int32_t)entry->confidence - ((status == AIOS_ERR_TIMEOUT) ? 12 : 8));
    g_learning.applied_failures++;
    g_learning.global_confidence = clamp_u8((int32_t)g_learning.global_confidence - 4);
    if (g_learning.io_aggressiveness_bias > -2 &&
        (action == SLM_ACTION_E1000_TX_SMOKE ||
         action == SLM_ACTION_BOOTSTRAP_E1000 ||
         action == SLM_ACTION_BOOTSTRAP_USB ||
         action == SLM_ACTION_BOOTSTRAP_STORAGE)) {
        g_learning.io_aggressiveness_bias--;
    }
    learning_recompute_tuning();
}

static uint8_t compute_plan_confidence(const slm_plan_request_t *req) {
    int32_t confidence = 50;
    slm_learning_entry_t *entry;

    if (!req) {
        return 0;
    }

    entry = learning_entry(req->action);
    if (entry) {
        confidence = entry->confidence;
    }
    confidence += ((int32_t)g_learning.global_confidence - 50) / 2;
    confidence -= (int32_t)req->risk_level * 8;
    confidence += ((int32_t)g_learning.io_aggressiveness_bias * 4);

    if (!plan_target_present(req) && req->template_id != SLM_TEMPLATE_DISCOVERY) {
        confidence = 0;
    }

    return clamp_u8(confidence);
}

static uint8_t compute_compatibility_score(const acpi_info_t *acpi,
                                           const pci_core_summary_t *pci,
                                           const platform_probe_summary_t *summary) {
    int32_t score = 25;

    if (acpi_ready()) {
        score += 20;
    }
    if (acpi->xsdt_present) {
        score += 5;
    }
    if (acpi->mcfg_present) {
        score += 10;
    }
    if (acpi->madt_present) {
        score += 10;
    }
    if (pci->ecam_available) {
        score += 10;
    }
    if (pci->pcie_functions > 0) {
        score += 10;
    }
    if (pci->msi_capable_functions > 0) {
        score += 5;
    }
    if (pci->msix_capable_functions > 0) {
        score += 5;
    }
    if (summary->matched_devices > 0) {
        score += 10;
    }
    if (pci->legacy_irq_functions > 0 && pci->msi_capable_functions == 0) {
        score -= 10;
    }

    if (score < 0) {
        score = 0;
    }
    if (score > 100) {
        score = 100;
    }
    return (uint8_t)score;
}

static void compute_fabric_profile(slm_fabric_profile_t *out) {
    if (!out) {
        return;
    }

    const acpi_info_t *acpi = acpi_info();
    const pci_core_summary_t *pci = pci_core_summary();
    const platform_probe_summary_t *summary = platform_probe_summary();

    out->acpi_ready = acpi_ready();
    out->xsdt_present = acpi->xsdt_present;
    out->mcfg_present = acpi->mcfg_present;
    out->madt_present = acpi->madt_present;
    out->ecam_available = pci->ecam_available;
    out->pci_access_mode = pci->access_mode;
    out->pci_bus_start = pci->ecam_start_bus;
    out->pci_bus_end = pci->ecam_end_bus;
    out->pci_total_functions = pci->total_functions;
    out->pci_bridge_count = pci->bridges;
    out->pci_pcie_functions = pci->pcie_functions;
    out->pci_msi_capable_functions = pci->msi_capable_functions;
    out->pci_msix_capable_functions = pci->msix_capable_functions;
    out->compatibility_score = compute_compatibility_score(acpi, pci, summary);
}

static const char *io_mode_name(slm_io_mode_t mode) {
    switch (mode) {
        case SLM_IO_MODE_CONSERVATIVE: return "conservative";
        case SLM_IO_MODE_BALANCED:     return "balanced";
        case SLM_IO_MODE_AGGRESSIVE:   return "aggressive";
        default:                       return "unknown";
    }
}

static const char *agent_mode_name(agent_operator_mode_t mode) {
    switch (mode) {
        case AGENT_OPERATOR_MODE_STABILIZE: return "stabilize";
        case AGENT_OPERATOR_MODE_BALANCE:   return "balance";
        case AGENT_OPERATOR_MODE_EXPLORE:   return "explore";
        default:                            return "unknown";
    }
}

static const char *agent_role_name(agent_node_role_t role) {
    switch (role) {
        case AGENT_NODE_ROLE_MAIN:             return "main";
        case AGENT_NODE_ROLE_GUARDIAN:         return "guardian";
        case AGENT_NODE_ROLE_ROUTER:           return "router";
        case AGENT_NODE_ROLE_PLANNER:          return "planner";
        case AGENT_NODE_ROLE_CRITIC:           return "critic";
        case AGENT_NODE_ROLE_SUMMARIZER:       return "summarizer";
        case AGENT_NODE_ROLE_VERIFIER:         return "verifier";
        case AGENT_NODE_ROLE_MEMORY_DISTILLER: return "memory-distiller";
        case AGENT_NODE_ROLE_TOOL_WORKER:      return "tool-worker";
        case AGENT_NODE_ROLE_DEVICE:           return "device";
        case AGENT_NODE_ROLE_NONE:
        default:                               return "none";
    }
}

static void agent_tree_set_node(agent_tree_node_t *node, uint8_t node_id,
                                agent_node_role_t role, agent_model_class_t model_class,
                                uint8_t priority, uint8_t budget_share,
                                bool active, bool persistent, bool zero_copy_preferred) {
    if (!node) {
        return;
    }
    node->node_id = node_id;
    node->role = role;
    node->model_class = model_class;
    node->priority = priority;
    node->budget_share = budget_share;
    node->active = active;
    node->persistent = persistent;
    node->zero_copy_preferred = zero_copy_preferred;
}

static void compute_agent_main_profile(agent_main_ai_profile_t *out,
    const memory_selftest_result_t *profile,
    const kernel_health_summary_t *health,
    const slm_fabric_profile_t *fabric,
    const slm_io_profile_t *io_profile) {
    int32_t static_sum;
    int32_t chaos_sum;
    int32_t sco;

    if (!out || !profile || !health || !fabric || !io_profile) {
        return;
    }

    out->self_consistency = clamp_u8(
        (health->level == KERNEL_STABILITY_STABLE) ? 92 :
        (health->level == KERNEL_STABILITY_DEGRADED) ? 64 : 28);
    out->goal_continuity = clamp_u8(
        45 + (health->autonomy_allowed ? 30 : 0) +
        ((profile->tier == BOOT_PERF_TIER_HIGH) ? 10 : 0));
    out->memory_coherence = clamp_u8(40 + (int32_t)g_learning.global_confidence / 2);
    out->policy_stability = clamp_u8(
        88 - (int32_t)g_learning.rejected_submissions * 4 -
        (int32_t)g_learning.applied_failures * 3);
    out->resource_reserve = clamp_u8(
        ((profile->tier == BOOT_PERF_TIER_HIGH) ? 82 :
         (profile->tier == BOOT_PERF_TIER_LOW) ? 46 : 64) +
        (int32_t)io_profile->ready_controllers * 5 -
        (int32_t)io_profile->degraded_controllers * 10);
    out->safety_margin = clamp_u8(
        (health->level == KERNEL_STABILITY_STABLE) ? 90 :
        (health->level == KERNEL_STABILITY_DEGRADED) ? 58 : 18);

    out->novelty_pressure = clamp_u8(
        22 + (int32_t)fabric->pci_pcie_functions * 4 +
        (int32_t)io_profile->pcie_io_devices * 6);
    out->prediction_error = clamp_u8(
        18 + (int32_t)g_learning.applied_failures * 6 +
        (int32_t)g_learning.rejected_submissions * 4);
    out->unresolved_uncertainty = clamp_u8(
        (100 - (int32_t)fabric->compatibility_score) +
        (int32_t)io_profile->degraded_controllers * 5);
    out->opportunity_gain = clamp_u8(
        20 + (int32_t)io_profile->ready_controllers * 8 +
        ((profile->tier == BOOT_PERF_TIER_HIGH) ? 18 : 0));
    out->external_surprise = clamp_u8(
        12 + (int32_t)health->degraded_count * 7 +
        (int32_t)health->unknown_count * 4 +
        (int32_t)io_profile->degraded_controllers * 8);
    out->hypothesis_diversity_demand = clamp_u8(
        18 + (int32_t)fabric->pci_total_functions * 2 +
        (int32_t)io_profile->ready_controllers * 4);

    static_sum = (int32_t)out->self_consistency +
        (int32_t)out->goal_continuity +
        (int32_t)out->memory_coherence +
        (int32_t)out->policy_stability +
        (int32_t)out->resource_reserve +
        (int32_t)out->safety_margin;
    chaos_sum = (int32_t)out->novelty_pressure +
        (int32_t)out->prediction_error +
        (int32_t)out->unresolved_uncertainty +
        (int32_t)out->opportunity_gain +
        (int32_t)out->external_surprise +
        (int32_t)out->hypothesis_diversity_demand;

    out->static_score = clamp_u8(static_sum / 6);
    out->chaos_score = clamp_u8(chaos_sum / 6);
    sco = ((int32_t)out->chaos_score - (int32_t)out->static_score) * 100;
    out->sco_x100 = (int16_t)sco;

    if (out->sco_x100 <= -800) {
        out->mode = AGENT_OPERATOR_MODE_STABILIZE;
    } else if (out->sco_x100 >= 800) {
        out->mode = AGENT_OPERATOR_MODE_EXPLORE;
    } else {
        out->mode = AGENT_OPERATOR_MODE_BALANCE;
    }

    switch (out->mode) {
        case AGENT_OPERATOR_MODE_STABILIZE:
            out->recommended_max_active_workers = 3;
            out->memory_write_intensity_pct = 85;
            out->adapter_update_allowed = false;
            out->memory_only_bias = true;
            break;
        case AGENT_OPERATOR_MODE_EXPLORE:
            out->recommended_max_active_workers = 8;
            out->memory_write_intensity_pct = 40;
            out->adapter_update_allowed = health->autonomy_allowed;
            out->memory_only_bias = false;
            break;
        case AGENT_OPERATOR_MODE_BALANCE:
        default:
            out->recommended_max_active_workers = 5;
            out->memory_write_intensity_pct = 60;
            out->adapter_update_allowed = health->autonomy_allowed;
            out->memory_only_bias = !health->autonomy_allowed;
            break;
    }
}

static void compute_agent_pipeline_profile(agent_pipeline_profile_t *out,
    const agent_main_ai_profile_t *main_ai,
    const slm_io_profile_t *io_profile) {
    if (!out || !main_ai || !io_profile) {
        return;
    }

    out->recommended_worker_queue_depth = clamp_u16(
        (int32_t)io_profile->recommended_queue_depth / 2 +
        (main_ai->mode == AGENT_OPERATOR_MODE_EXPLORE ? 4 : 0),
        2, 32);
    out->recommended_token_pipeline_depth =
        (main_ai->mode == AGENT_OPERATOR_MODE_STABILIZE) ? 1 :
        (main_ai->mode == AGENT_OPERATOR_MODE_BALANCE) ? 2 : 3;
    out->recommended_planner_fanout =
        (main_ai->mode == AGENT_OPERATOR_MODE_STABILIZE) ? 1 :
        (main_ai->mode == AGENT_OPERATOR_MODE_BALANCE) ? 2 : 3;
    out->recommended_microbatch_tokens =
        (main_ai->mode == AGENT_OPERATOR_MODE_STABILIZE) ? 32 :
        (main_ai->mode == AGENT_OPERATOR_MODE_BALANCE) ? 64 : 96;
    out->recommended_summary_interval_ms =
        (main_ai->mode == AGENT_OPERATOR_MODE_STABILIZE) ? 260 :
        (main_ai->mode == AGENT_OPERATOR_MODE_BALANCE) ? 160 : 96;
    out->recommended_memory_journal_batch =
        (main_ai->mode == AGENT_OPERATOR_MODE_STABILIZE) ? 24 :
        (main_ai->mode == AGENT_OPERATOR_MODE_BALANCE) ? 16 : 8;
    out->recommended_submit_ring_entries = clamp_u16(
        (int32_t)out->recommended_worker_queue_depth * 8, 64, 1024);
    out->recommended_completion_ring_entries = clamp_u16(
        (int32_t)out->recommended_worker_queue_depth * 4, 64, 1024);
    out->recommended_zero_copy_window_kib = clamp_u32(
        (int32_t)io_profile->recommended_dma_window_kib * 2, 64, 2048);
    out->zero_copy_preferred = io_profile->ready_controllers > 0;
    out->shared_kv_preferred = main_ai->mode != AGENT_OPERATOR_MODE_STABILIZE;
    out->device_nodes_preferred = io_profile->pcie_io_devices > 0 ||
        io_profile->ready_controllers > 0;
    out->shared_infer_ring_preferred =
        out->zero_copy_preferred ||
        io_profile->recommended_queue_depth >= 16;
}

static void compute_agent_tree(agent_tree_node_t *nodes, uint32_t *count,
    const agent_main_ai_profile_t *main_ai,
    const agent_pipeline_profile_t *pipeline,
    const slm_io_profile_t *io_profile) {
    uint32_t next = 0;
    bool explore = false;
    bool stabilize = false;

    if (!nodes || !count || !main_ai || !pipeline || !io_profile) {
        return;
    }

    for (uint32_t i = 0; i < AGENT_TREE_MAX_NODES; i++) {
        agent_tree_set_node(&nodes[i], 0, AGENT_NODE_ROLE_NONE, AGENT_MODEL_CLASS_MICRO,
            0, 0, false, false, false);
    }

    explore = main_ai->mode == AGENT_OPERATOR_MODE_EXPLORE;
    stabilize = main_ai->mode == AGENT_OPERATOR_MODE_STABILIZE;

    agent_tree_set_node(&nodes[next++], 1, AGENT_NODE_ROLE_MAIN, AGENT_MODEL_CLASS_MAIN,
        100, 32, true, true, true);
    agent_tree_set_node(&nodes[next++], 2, AGENT_NODE_ROLE_GUARDIAN, AGENT_MODEL_CLASS_SMALL,
        96, 16, true, true, false);
    agent_tree_set_node(&nodes[next++], 3, AGENT_NODE_ROLE_ROUTER, AGENT_MODEL_CLASS_SMALL,
        90, 10, true, true, false);
    agent_tree_set_node(&nodes[next++], 4, AGENT_NODE_ROLE_PLANNER, AGENT_MODEL_CLASS_MEDIUM,
        86, explore ? 12 : 8, !stabilize, false, pipeline->zero_copy_preferred);
    agent_tree_set_node(&nodes[next++], 5, AGENT_NODE_ROLE_CRITIC, AGENT_MODEL_CLASS_MEDIUM,
        84, 8, !stabilize, false, pipeline->zero_copy_preferred);
    agent_tree_set_node(&nodes[next++], 6, AGENT_NODE_ROLE_SUMMARIZER, AGENT_MODEL_CLASS_SMALL,
        82, stabilize ? 12 : 8, true, false, false);
    agent_tree_set_node(&nodes[next++], 7, AGENT_NODE_ROLE_VERIFIER, AGENT_MODEL_CLASS_SMALL,
        88, 8, true, false, false);
    agent_tree_set_node(&nodes[next++], 8, AGENT_NODE_ROLE_MEMORY_DISTILLER, AGENT_MODEL_CLASS_SMALL,
        80, stabilize ? 8 : 6, true, false, false);
    agent_tree_set_node(&nodes[next++], 9, AGENT_NODE_ROLE_TOOL_WORKER, AGENT_MODEL_CLASS_MEDIUM,
        76, explore ? 10 : 6, !stabilize, false, pipeline->zero_copy_preferred);
    agent_tree_set_node(&nodes[next++], 10, AGENT_NODE_ROLE_DEVICE, AGENT_MODEL_CLASS_MICRO,
        74, pipeline->device_nodes_preferred ? 10 : 4,
        pipeline->device_nodes_preferred || io_profile->ready_controllers > 0,
        false, pipeline->zero_copy_preferred);

    *count = next;
}

static void compute_io_profile(slm_io_profile_t *out) {
    if (!out) {
        return;
    }

    const platform_probe_summary_t *summary = platform_probe_summary();
    const memory_selftest_result_t *profile = kernel_memory_selftest_last();
    slm_fabric_profile_t fabric;
    uint32_t total_io = summary->ethernet_count + summary->usb_count + summary->storage_count;
    uint32_t ready = 0;

    compute_fabric_profile(&fabric);

    ready += e1000_driver_ready() ? 1U : 0U;
    ready += usb_host_ready() ? 1U : 0U;
    ready += storage_host_ready() ? 1U : 0U;

    out->ready_controllers = (uint8_t)ready;
    out->degraded_controllers = (uint8_t)((total_io > ready) ? (total_io - ready) : 0);
    out->pcie_io_devices = 0;

    for (uint32_t i = 0; i < platform_probe_count(); i++) {
        const platform_device_t *dev = platform_probe_get(i);
        if (!dev || !dev->pcie_capable || !device_is_io_kind(dev->kind)) {
            continue;
        }
        out->pcie_io_devices++;
    }

    if (out->degraded_controllers > 0 ||
        profile->tier == BOOT_PERF_TIER_LOW ||
        !fabric.acpi_ready ||
        (!fabric.ecam_available && fabric.pci_total_functions > 4)) {
        out->mode = SLM_IO_MODE_CONSERVATIVE;
        out->recommended_queue_depth = 8;
        out->recommended_poll_budget = 256;
        out->recommended_dma_window_kib = 64;
    } else if (profile->tier == BOOT_PERF_TIER_HIGH &&
               out->ready_controllers >= 2 &&
               fabric.ecam_available &&
               fabric.compatibility_score >= 75 &&
               fabric.pci_msi_capable_functions > 0) {
        out->mode = SLM_IO_MODE_AGGRESSIVE;
        out->recommended_queue_depth = 32;
        out->recommended_poll_budget = 1024;
        out->recommended_dma_window_kib = 256;
    } else {
        out->mode = SLM_IO_MODE_BALANCED;
        out->recommended_queue_depth = 16;
        out->recommended_poll_budget = 512;
        out->recommended_dma_window_kib = 128;
    }

    if (g_learning.global_confidence != 0) {
        out->recommended_queue_depth = clamp_u16(
            (int32_t)out->recommended_queue_depth + g_learning.io_aggressiveness_bias * 4,
            8, 64);
        out->recommended_poll_budget = clamp_u16(
            (int32_t)out->recommended_poll_budget + g_learning.io_aggressiveness_bias * 128,
            128, 2048);
        out->recommended_dma_window_kib = clamp_u32(
            (int32_t)out->recommended_dma_window_kib + g_learning.io_aggressiveness_bias * 32,
            32, 512);

        if (g_learning.tuned_queue_depth != 0) {
            out->recommended_queue_depth = clamp_u16(g_learning.tuned_queue_depth, 8, 64);
        }
        if (g_learning.tuned_poll_budget != 0) {
            out->recommended_poll_budget = clamp_u16(g_learning.tuned_poll_budget, 128, 2048);
        }
        if (g_learning.tuned_dma_window_kib != 0) {
            out->recommended_dma_window_kib = clamp_u32(g_learning.tuned_dma_window_kib, 32, 512);
        }
    }
}

static void apply_io_defaults(slm_plan_request_t *req, const slm_io_profile_t *profile) {
    uint8_t confidence;

    if (!req || !profile) {
        return;
    }

    if (req->queue_depth_hint == 0) {
        req->queue_depth_hint = profile->recommended_queue_depth;
    }
    if (req->poll_budget_hint == 0) {
        req->poll_budget_hint = profile->recommended_poll_budget;
    }
    if (req->dma_window_kib_hint == 0) {
        req->dma_window_kib_hint = profile->recommended_dma_window_kib;
    }

    confidence = compute_plan_confidence(req);
    if (confidence < 40) {
        req->queue_depth_hint = clamp_u16(req->queue_depth_hint / 2, 4, 64);
        req->poll_budget_hint = clamp_u16(req->poll_budget_hint / 2, 64, 2048);
        req->dma_window_kib_hint = clamp_u32(req->dma_window_kib_hint / 2, 16, 512);
    } else if (confidence > 80 && g_learning.io_aggressiveness_bias > 0) {
        req->queue_depth_hint = clamp_u16(req->queue_depth_hint + 4, 8, 64);
        req->poll_budget_hint = clamp_u16(req->poll_budget_hint + 128, 128, 2048);
        req->dma_window_kib_hint = clamp_u32(req->dma_window_kib_hint + 32, 32, 512);
    }
}

static bool request_valid(const slm_plan_request_t *req) {
    kernel_health_summary_t health;
    uint8_t confidence;

    if (!req) {
        return false;
    }
    if (req->risk_level > 3) {
        return false;
    }
    if (req->queue_depth_hint > 256 || req->poll_budget_hint > 4096 ||
        req->dma_window_kib_hint > 4096) {
        return false;
    }
    if (!plan_target_present(req)) {
        return false;
    }

    kernel_health_get_summary(&health);
    if (health.level == KERNEL_STABILITY_UNSAFE &&
        req->action != SLM_ACTION_REPROBE_PCI &&
        req->action != SLM_ACTION_IO_AUDIT &&
        req->action != SLM_ACTION_CORE_AUDIT) {
        return false;
    }
    if (!health.risky_io_allowed &&
        req->risk_level > 0 &&
        (req->template_id == SLM_TEMPLATE_PCI_ETHERNET ||
         req->template_id == SLM_TEMPLATE_PCI_USB ||
         req->template_id == SLM_TEMPLATE_PCI_STORAGE)) {
        return false;
    }

    confidence = compute_plan_confidence(req);
    if (req->risk_level > 0 && confidence < 30) {
        return false;
    }

    switch (req->action) {
        case SLM_ACTION_REPROBE_PCI:
        case SLM_ACTION_IO_AUDIT:
        case SLM_ACTION_CORE_AUDIT:
            return req->template_id == SLM_TEMPLATE_DISCOVERY;
        case SLM_ACTION_BOOTSTRAP_E1000:
        case SLM_ACTION_E1000_TX_SMOKE:
        case SLM_ACTION_E1000_DUMP:
            return req->template_id == SLM_TEMPLATE_PCI_ETHERNET;
        case SLM_ACTION_BOOTSTRAP_USB:
        case SLM_ACTION_USB_DUMP:
            return req->template_id == SLM_TEMPLATE_PCI_USB;
        case SLM_ACTION_BOOTSTRAP_STORAGE:
        case SLM_ACTION_STORAGE_DUMP:
            return req->template_id == SLM_TEMPLATE_PCI_STORAGE;
        default:
            return false;
    }
}

static slm_plan_t *find_plan(uint32_t plan_id) {
    for (uint32_t i = 0; i < SLM_PLAN_CAP; i++) {
        if (plan_table[i].state != SLM_PLAN_EMPTY &&
            plan_table[i].plan_id == plan_id) {
            return &plan_table[i];
        }
    }
    return NULL;
}

static slm_plan_t *alloc_plan(void) {
    for (uint32_t i = 0; i < SLM_PLAN_CAP; i++) {
        if (plan_table[i].state == SLM_PLAN_EMPTY) {
            return &plan_table[i];
        }
    }
    return NULL;
}

static void seed_plan(const slm_plan_request_t *req, const char *label) {
    uint32_t plan_id = 0;
    aios_status_t status = slm_plan_submit(req, &plan_id);
    if (status != AIOS_OK) {
        serial_printf("[SLM] Seed plan skipped label=%s status=%d\n",
            label,
            (int64_t)status);
        return;
    }

    serial_printf("[SLM] Seeded plan %u label=%s action=%u risk=%u qd=%u poll=%u dma=%uKiB\n",
        (uint64_t)plan_id,
        label,
        (uint64_t)req->action,
        (uint64_t)req->risk_level,
        (uint64_t)req->queue_depth_hint,
        (uint64_t)req->poll_budget_hint,
        (uint64_t)req->dma_window_kib_hint);
}

static void seed_boot_plans(void) {
    slm_io_profile_t io_profile;
    compute_io_profile(&io_profile);

    slm_plan_request_t discovery = {
        .template_id = SLM_TEMPLATE_DISCOVERY,
        .action = SLM_ACTION_REPROBE_PCI,
        .target_vendor_id = 0,
        .target_device_id = 0,
        .target_kind = PLATFORM_DEVICE_UNKNOWN,
        .risk_level = 0,
        .allow_apply = false,
    };
    apply_io_defaults(&discovery, &io_profile);
    seed_plan(&discovery, "inventory-refresh");

    slm_plan_request_t core_audit = {
        .template_id = SLM_TEMPLATE_DISCOVERY,
        .action = SLM_ACTION_CORE_AUDIT,
        .target_vendor_id = 0,
        .target_device_id = 0,
        .target_kind = PLATFORM_DEVICE_UNKNOWN,
        .risk_level = 0,
        .allow_apply = false,
    };
    apply_io_defaults(&core_audit, &io_profile);
    seed_plan(&core_audit, "core-audit");

    slm_plan_request_t io_audit = {
        .template_id = SLM_TEMPLATE_DISCOVERY,
        .action = SLM_ACTION_IO_AUDIT,
        .target_vendor_id = 0,
        .target_device_id = 0,
        .target_kind = PLATFORM_DEVICE_UNKNOWN,
        .risk_level = 0,
        .allow_apply = false,
    };
    apply_io_defaults(&io_audit, &io_profile);
    seed_plan(&io_audit, "io-audit");

    const platform_device_t *ethernet = NULL;
    for (uint32_t i = 0; i < platform_probe_count(); i++) {
        const platform_device_t *dev = platform_probe_get(i);
        if (!dev) {
            continue;
        }

        if (dev->kind == PLATFORM_DEVICE_ETHERNET && !ethernet) {
            ethernet = dev;
        }
    }

    if (ethernet) {
        slm_plan_request_t nic_plan = {
            .template_id = SLM_TEMPLATE_PCI_ETHERNET,
            .action = e1000_driver_ready() ? SLM_ACTION_E1000_DUMP
                                           : SLM_ACTION_BOOTSTRAP_E1000,
            .target_vendor_id = ethernet->vendor_id,
            .target_device_id = ethernet->device_id,
            .target_kind = ethernet->kind,
            .risk_level = e1000_driver_ready() ? 0 : 1,
            .allow_apply = false,
        };
        apply_io_defaults(&nic_plan, &io_profile);
        seed_plan(&nic_plan, "ethernet-bootstrap");

        if (e1000_driver_ready()) {
            slm_plan_request_t tx_smoke = {
                .template_id = SLM_TEMPLATE_PCI_ETHERNET,
                .action = SLM_ACTION_E1000_TX_SMOKE,
                .target_vendor_id = ethernet->vendor_id,
                .target_device_id = ethernet->device_id,
                .target_kind = ethernet->kind,
                .risk_level = 1,
                .allow_apply = false,
            };
            apply_io_defaults(&tx_smoke, &io_profile);
            seed_plan(&tx_smoke, "ethernet-tx-smoke");
        }
    }

    for (uint32_t i = 0; i < platform_probe_count(); i++) {
        const platform_device_t *dev = platform_probe_get(i);
        if (!dev || dev->kind != PLATFORM_DEVICE_USB) {
            continue;
        }

        slm_plan_request_t usb_plan = {
            .template_id = SLM_TEMPLATE_PCI_USB,
            .action = usb_host_ready() ? SLM_ACTION_USB_DUMP
                                       : SLM_ACTION_BOOTSTRAP_USB,
            .target_vendor_id = dev->vendor_id,
            .target_device_id = dev->device_id,
            .target_kind = dev->kind,
            .risk_level = usb_host_ready() ? 0 : 1,
            .allow_apply = false,
        };
        apply_io_defaults(&usb_plan, &io_profile);
        seed_plan(&usb_plan, "usb-bootstrap");
        break;
    }

    for (uint32_t i = 0; i < platform_probe_count(); i++) {
        const platform_device_t *dev = platform_probe_get(i);
        if (!dev || dev->kind != PLATFORM_DEVICE_STORAGE) {
            continue;
        }

        slm_plan_request_t storage_plan = {
            .template_id = SLM_TEMPLATE_PCI_STORAGE,
            .action = storage_host_ready() ? SLM_ACTION_STORAGE_DUMP
                                           : SLM_ACTION_BOOTSTRAP_STORAGE,
            .target_vendor_id = dev->vendor_id,
            .target_device_id = dev->device_id,
            .target_kind = dev->kind,
            .risk_level = storage_host_ready() ? 0 : 1,
            .allow_apply = false,
        };
        apply_io_defaults(&storage_plan, &io_profile);
        seed_plan(&storage_plan, "storage-bootstrap");
        break;
    }
}

static aios_status_t apply_request(const slm_plan_request_t *req) {
    serial_printf("[SLM] Apply action=%u qd=%u poll=%u dma=%uKiB\n",
        (uint64_t)req->action,
        (uint64_t)req->queue_depth_hint,
        (uint64_t)req->poll_budget_hint,
        (uint64_t)req->dma_window_kib_hint);

    switch (req->action) {
        case SLM_ACTION_REPROBE_PCI:
            return platform_probe_init();
        case SLM_ACTION_CORE_AUDIT:
            serial_write("[SLM] Core audit begin\n");
            acpi_dump();
            pci_core_dump();
            platform_probe_dump();
            serial_write("[SLM] Core audit end\n");
            return AIOS_OK;
        case SLM_ACTION_IO_AUDIT:
            serial_write("[SLM] I/O audit begin\n");
            e1000_driver_dump();
            usb_host_dump();
            storage_host_dump();
            serial_write("[SLM] I/O audit end\n");
            return AIOS_OK;
        case SLM_ACTION_BOOTSTRAP_E1000:
            return e1000_driver_init();
        case SLM_ACTION_E1000_TX_SMOKE:
            return e1000_driver_tx_smoke();
        case SLM_ACTION_E1000_DUMP:
            e1000_driver_dump();
            return AIOS_OK;
        case SLM_ACTION_BOOTSTRAP_USB:
            return usb_host_init();
        case SLM_ACTION_USB_DUMP:
            usb_host_dump();
            return AIOS_OK;
        case SLM_ACTION_BOOTSTRAP_STORAGE:
            return storage_host_init();
        case SLM_ACTION_STORAGE_DUMP:
            storage_host_dump();
            return AIOS_OK;
        default:
            return AIOS_ERR_NOSYS;
    }
}

aios_status_t slm_orchestrator_init(void) {
    slm_hw_snapshot_t snapshot;

    for (uint32_t i = 0; i < SLM_PLAN_CAP; i++) {
        plan_table[i].plan_id = 0;
        plan_table[i].state = SLM_PLAN_EMPTY;
        plan_table[i].last_status = AIOS_OK;
        plan_table[i].expected_confidence = 0;
        plan_table[i].validation_score = 0;
        plan_table[i].created_ts_ns = 0;
        plan_table[i].applied_ts_ns = 0;
    }

    next_plan_id = 1;
    memset(&g_learning, 0, sizeof(g_learning));
    learning_init();
    learning_refresh_boot_observation();

    kprintf("\n");
    kprintf("    SLM Hardware Orchestrator initialized:\n");
    kprintf("    Plan slots: %u\n", (uint64_t)SLM_PLAN_CAP);
    kprintf("    Templates: discovery/core-audit, pci-ethernet, pci-usb, pci-storage\n");
    kprintf("    Learning confidence: %u | tuned qd=%u poll=%u dma=%uKiB\n",
        (uint64_t)g_learning.global_confidence,
        (uint64_t)g_learning.tuned_queue_depth,
        (uint64_t)g_learning.tuned_poll_budget,
        (uint64_t)g_learning.tuned_dma_window_kib);
    serial_write("[SLM] Hardware orchestrator ready\n");
    if (slm_snapshot_read(&snapshot) == AIOS_OK) {
        serial_printf("[SLM] MainAI mode=%s sco=%d workers=%u pipeline_qd=%u depth=%u ring=%u/%u\n",
            agent_mode_name(snapshot.main_ai_profile.mode),
            (int64_t)snapshot.main_ai_profile.sco_x100,
            (uint64_t)snapshot.main_ai_profile.recommended_max_active_workers,
            (uint64_t)snapshot.pipeline_profile.recommended_worker_queue_depth,
            (uint64_t)snapshot.pipeline_profile.recommended_token_pipeline_depth,
            (uint64_t)snapshot.pipeline_profile.recommended_submit_ring_entries,
            (uint64_t)snapshot.pipeline_profile.recommended_completion_ring_entries);
    }
    seed_boot_plans();
    return AIOS_OK;
}

aios_status_t slm_snapshot_read(slm_hw_snapshot_t *out) {
    if (!out) {
        return AIOS_ERR_INVAL;
    }

    const memory_selftest_result_t *profile = kernel_memory_selftest_last();
    const platform_probe_summary_t *summary = platform_probe_summary();
    kernel_health_summary_t health;
    e1000_driver_info_t nic;
    storage_host_info_t storage;
    usb_host_info_t usb;
    kernel_health_get_summary(&health);
    (void)e1000_driver_info(&nic);
    (void)storage_host_info(&storage);
    (void)usb_host_info(&usb);

    out->ts_ns = kernel_time_monotonic_ns();
    out->tsc_khz = kernel_time_tsc_khz();
    out->invariant_tsc = kernel_time_invariant_tsc();
    out->tier = profile->tier;
    out->memcpy_mib_per_sec = profile->memcpy_mib_per_sec;
    out->total_detected_devices = summary->total_pci_devices;
    out->listed_devices = 0;
    out->health_level = health.level;
    out->autonomy_allowed = health.autonomy_allowed;
    out->risky_io_allowed = health.risky_io_allowed;
    out->e1000_ready = nic.ready;
    out->e1000_link_up = nic.link_up;
    out->usb_ready = usb.ready;
    out->usb_controller_kind = (uint8_t)usb.controller_kind;
    out->storage_ready = storage.ready;
    out->storage_controller_kind = (uint8_t)storage.controller_kind;
    compute_fabric_profile(&out->fabric_profile);
    out->memory_fabric = *memory_fabric_profile();
    out->learning_profile = g_learning;
    compute_io_profile(&out->io_profile);
    ai_infer_ring_runtime(&out->ring_runtime);
    compute_agent_main_profile(&out->main_ai_profile, profile, &health,
        &out->fabric_profile, &out->io_profile);
    compute_agent_pipeline_profile(&out->pipeline_profile, &out->main_ai_profile,
        &out->io_profile);
    out->agent_tree_nodes = 0;
    compute_agent_tree(out->agent_tree, &out->agent_tree_nodes,
        &out->main_ai_profile, &out->pipeline_profile, &out->io_profile);

    for (uint32_t i = 0; i < SLM_HW_MAX_DEVICES; i++) {
        out->devices[i].vendor_id = 0;
        out->devices[i].device_id = 0;
        out->devices[i].class_code = 0;
        out->devices[i].subclass = 0;
        out->devices[i].prog_if = 0;
        out->devices[i].bus = 0;
        out->devices[i].slot = 0;
        out->devices[i].function = 0;
        out->devices[i].init_priority = 0;
        out->devices[i].pcie_capable = false;
        out->devices[i].pcie_link_speed = 0;
        out->devices[i].pcie_link_width = 0;
        out->devices[i].kind = PLATFORM_DEVICE_UNKNOWN;
    }

    uint32_t count = platform_probe_count();
    if (count > SLM_HW_MAX_DEVICES) {
        count = SLM_HW_MAX_DEVICES;
    }

    for (uint32_t i = 0; i < count; i++) {
        const platform_device_t *dev = platform_probe_get(i);
        if (!dev) {
            continue;
        }
        out->devices[i].vendor_id = dev->vendor_id;
        out->devices[i].device_id = dev->device_id;
        out->devices[i].class_code = dev->class_code;
        out->devices[i].subclass = dev->subclass;
        out->devices[i].prog_if = dev->prog_if;
        out->devices[i].bus = dev->bus;
        out->devices[i].slot = dev->slot;
        out->devices[i].function = dev->function;
        out->devices[i].init_priority = dev->init_priority;
        out->devices[i].pcie_capable = dev->pcie_capable;
        out->devices[i].pcie_link_speed = dev->pcie_link_speed;
        out->devices[i].pcie_link_width = dev->pcie_link_width;
        out->devices[i].kind = dev->kind;
        out->listed_devices++;
    }

    return AIOS_OK;
}

aios_status_t slm_plan_submit(const slm_plan_request_t *req, uint32_t *plan_id_out) {
    slm_plan_t *slot = alloc_plan();
    if (!slot || !req) {
        return AIOS_ERR_BUSY;
    }

    slot->plan_id = next_plan_id++;
    slot->request = *req;
    slot->created_ts_ns = kernel_time_monotonic_ns();
    slot->applied_ts_ns = 0;
    slot->last_status = AIOS_OK;
    slot->expected_confidence = compute_plan_confidence(req);
    slot->validation_score = clamp_u8((int32_t)slot->expected_confidence +
        ((req->allow_apply ? 5 : 0) - ((int32_t)req->risk_level * 5)));
    slot->state = SLM_PLAN_PROPOSED;
    slot->state = request_valid(req) ? SLM_PLAN_VALIDATED : SLM_PLAN_REJECTED;
    learning_record_submission(req->action, slot->state == SLM_PLAN_VALIDATED);

    serial_printf("[SLM] Plan %u submitted template=%u action=%u state=%u conf=%u score=%u qd=%u poll=%u dma=%uKiB\n",
        (uint64_t)slot->plan_id,
        (uint64_t)slot->request.template_id,
        (uint64_t)slot->request.action,
        (uint64_t)slot->state,
        (uint64_t)slot->expected_confidence,
        (uint64_t)slot->validation_score,
        (uint64_t)slot->request.queue_depth_hint,
        (uint64_t)slot->request.poll_budget_hint,
        (uint64_t)slot->request.dma_window_kib_hint);

    if (plan_id_out) {
        *plan_id_out = slot->plan_id;
    }

    return (slot->state == SLM_PLAN_VALIDATED) ? AIOS_OK : AIOS_ERR_INVAL;
}

aios_status_t slm_plan_apply(uint32_t plan_id) {
    slm_plan_t *plan = find_plan(plan_id);
    uint64_t start_ns;

    if (!plan) {
        return AIOS_ERR_INVAL;
    }
    if (plan->state != SLM_PLAN_VALIDATED || !plan->request.allow_apply) {
        plan->state = SLM_PLAN_REJECTED;
        plan->last_status = AIOS_ERR_PERM;
        learning_record_submission(plan->request.action, false);
        return AIOS_ERR_PERM;
    }

    start_ns = kernel_time_monotonic_ns();
    plan->last_status = apply_request(&plan->request);
    plan->applied_ts_ns = kernel_time_monotonic_ns();
    plan->state = (plan->last_status == AIOS_OK) ? SLM_PLAN_APPLIED : SLM_PLAN_FAILED;
    learning_record_result(plan->request.action, plan->last_status,
        plan->applied_ts_ns - start_ns);

    serial_printf("[SLM] Plan %u apply status=%d state=%u conf=%u latency=%u ns\n",
        (uint64_t)plan->plan_id,
        (int64_t)plan->last_status,
        (uint64_t)plan->state,
        (uint64_t)learning_get_confidence(plan->request.action),
        plan->applied_ts_ns - start_ns);

    return plan->last_status;
}

aios_status_t slm_plan_get(uint32_t plan_id, slm_plan_t *out) {
    slm_plan_t *plan = find_plan(plan_id);
    if (!out || !plan) {
        return AIOS_ERR_INVAL;
    }
    *out = *plan;
    return AIOS_OK;
}

aios_status_t slm_plan_list(slm_plan_list_t *out) {
    if (!out) {
        return AIOS_ERR_INVAL;
    }

    out->count = 0;
    for (uint32_t i = 0; i < SLM_PLAN_CAP; i++) {
        if (plan_table[i].state == SLM_PLAN_EMPTY) {
            continue;
        }
        out->plans[out->count++] = plan_table[i];
    }

    return AIOS_OK;
}

void slm_orchestrator_dump(void) {
    slm_hw_snapshot_t snapshot;
    if (slm_snapshot_read(&snapshot) != AIOS_OK) {
        return;
    }

    kprintf("\n=== SLM Hardware Orchestrator ===\n");
    kprintf("TSC: %u kHz | tier=%u | memcpy=%u MiB/s\n",
        snapshot.tsc_khz,
        (uint64_t)snapshot.tier,
        snapshot.memcpy_mib_per_sec);
    kprintf("Devices: detected=%u listed=%u | e1000_ready=%u link=%u | usb_ready=%u type=%u | sto_ready=%u type=%u\n",
        snapshot.total_detected_devices,
        snapshot.listed_devices,
        (uint64_t)snapshot.e1000_ready,
        (uint64_t)snapshot.e1000_link_up,
        (uint64_t)snapshot.usb_ready,
        (uint64_t)snapshot.usb_controller_kind,
        (uint64_t)snapshot.storage_ready,
        (uint64_t)snapshot.storage_controller_kind);
    kprintf("Health gate: level=%s autonomy=%u risky_io=%u\n",
        kernel_stability_name(snapshot.health_level),
        (uint64_t)snapshot.autonomy_allowed,
        (uint64_t)snapshot.risky_io_allowed);
    kprintf("Learning: global=%u bias=%d qd=%u poll=%u dma=%uKiB success=%u fail=%u reject=%u\n",
        (uint64_t)snapshot.learning_profile.global_confidence,
        (int64_t)snapshot.learning_profile.io_aggressiveness_bias,
        (uint64_t)snapshot.learning_profile.tuned_queue_depth,
        (uint64_t)snapshot.learning_profile.tuned_poll_budget,
        (uint64_t)snapshot.learning_profile.tuned_dma_window_kib,
        (uint64_t)snapshot.learning_profile.applied_successes,
        (uint64_t)snapshot.learning_profile.applied_failures,
        (uint64_t)snapshot.learning_profile.rejected_submissions);
    kprintf("Fabric: acpi=%u mcfg=%u madt=%u mode=%s score=%u total=%u bridges=%u pcie=%u msi=%u msix=%u\n",
        (uint64_t)snapshot.fabric_profile.acpi_ready,
        (uint64_t)snapshot.fabric_profile.mcfg_present,
        (uint64_t)snapshot.fabric_profile.madt_present,
        pci_cfg_access_mode_name(snapshot.fabric_profile.pci_access_mode),
        (uint64_t)snapshot.fabric_profile.compatibility_score,
        (uint64_t)snapshot.fabric_profile.pci_total_functions,
        (uint64_t)snapshot.fabric_profile.pci_bridge_count,
        (uint64_t)snapshot.fabric_profile.pci_pcie_functions,
        (uint64_t)snapshot.fabric_profile.pci_msi_capable_functions,
        (uint64_t)snapshot.fabric_profile.pci_msix_capable_functions);
    kprintf("Memory fabric: topology=%s compat=%u locality=%u slots=%u worker_qd=%u hotset=%uKiB staging=%uKiB zero_copy=%u numa=%u cxl=%u\n",
        (uint64_t)(uintptr_t)memory_fabric_topology_name(snapshot.memory_fabric.topology),
        (uint64_t)snapshot.memory_fabric.compatibility_score,
        (uint64_t)snapshot.memory_fabric.locality_score,
        (uint64_t)snapshot.memory_fabric.recommended_agent_slots,
        (uint64_t)snapshot.memory_fabric.recommended_worker_queue_depth,
        (uint64_t)snapshot.memory_fabric.recommended_hotset_kib,
        (uint64_t)snapshot.memory_fabric.recommended_staging_kib,
        (uint64_t)snapshot.memory_fabric.zero_copy_preferred,
        (uint64_t)snapshot.memory_fabric.numa_hint_ready,
        (uint64_t)snapshot.memory_fabric.fabric_expandable);
    kprintf("I/O profile: mode=%s ready=%u degraded=%u pcie=%u qd=%u poll=%u dma=%uKiB\n",
        io_mode_name(snapshot.io_profile.mode),
        (uint64_t)snapshot.io_profile.ready_controllers,
        (uint64_t)snapshot.io_profile.degraded_controllers,
        (uint64_t)snapshot.io_profile.pcie_io_devices,
        (uint64_t)snapshot.io_profile.recommended_queue_depth,
        (uint64_t)snapshot.io_profile.recommended_poll_budget,
        (uint64_t)snapshot.io_profile.recommended_dma_window_kib);
    kprintf("Ring runtime: registered=%u active=%u notify=%u max_tail=%u last_ring=%u event=%u shared_kv=%u\n",
        (uint64_t)snapshot.ring_runtime.registered_rings,
        (uint64_t)snapshot.ring_runtime.active_rings,
        (uint64_t)snapshot.ring_runtime.total_notifies,
        (uint64_t)snapshot.ring_runtime.max_submit_tail_observed,
        (uint64_t)snapshot.ring_runtime.last_ring_id,
        (uint64_t)snapshot.ring_runtime.any_event_notify,
        (uint64_t)snapshot.ring_runtime.any_shared_kv);
    kprintf("Main AI: mode=%s static=%u chaos=%u sco=%d workers=%u memory_write=%u%% adapter=%u\n",
        agent_mode_name(snapshot.main_ai_profile.mode),
        (uint64_t)snapshot.main_ai_profile.static_score,
        (uint64_t)snapshot.main_ai_profile.chaos_score,
        (int64_t)snapshot.main_ai_profile.sco_x100,
        (uint64_t)snapshot.main_ai_profile.recommended_max_active_workers,
        (uint64_t)snapshot.main_ai_profile.memory_write_intensity_pct,
        (uint64_t)snapshot.main_ai_profile.adapter_update_allowed);
    kprintf("Pipeline: qd=%u depth=%u fanout=%u microbatch=%u summary_ms=%u journal=%u ring=%u/%u zero_copy=%u shared_kv=%u device_nodes=%u shared_ring=%u\n",
        (uint64_t)snapshot.pipeline_profile.recommended_worker_queue_depth,
        (uint64_t)snapshot.pipeline_profile.recommended_token_pipeline_depth,
        (uint64_t)snapshot.pipeline_profile.recommended_planner_fanout,
        (uint64_t)snapshot.pipeline_profile.recommended_microbatch_tokens,
        (uint64_t)snapshot.pipeline_profile.recommended_summary_interval_ms,
        (uint64_t)snapshot.pipeline_profile.recommended_memory_journal_batch,
        (uint64_t)snapshot.pipeline_profile.recommended_submit_ring_entries,
        (uint64_t)snapshot.pipeline_profile.recommended_completion_ring_entries,
        (uint64_t)snapshot.pipeline_profile.zero_copy_preferred,
        (uint64_t)snapshot.pipeline_profile.shared_kv_preferred,
        (uint64_t)snapshot.pipeline_profile.device_nodes_preferred,
        (uint64_t)snapshot.pipeline_profile.shared_infer_ring_preferred);
    for (uint32_t i = 0; i < snapshot.agent_tree_nodes; i++) {
        kprintf("  node[%u] role=%s class=%u active=%u persist=%u prio=%u budget=%u zero_copy=%u\n",
            (uint64_t)snapshot.agent_tree[i].node_id,
            agent_role_name(snapshot.agent_tree[i].role),
            (uint64_t)snapshot.agent_tree[i].model_class,
            (uint64_t)snapshot.agent_tree[i].active,
            (uint64_t)snapshot.agent_tree[i].persistent,
            (uint64_t)snapshot.agent_tree[i].priority,
            (uint64_t)snapshot.agent_tree[i].budget_share,
            (uint64_t)snapshot.agent_tree[i].zero_copy_preferred);
    }
    for (uint32_t i = 0; i < snapshot.listed_devices; i++) {
        kprintf("  [%u] kind=%u vendor=%x device=%x bus=%u slot=%u prio=%u\n",
            (uint64_t)i,
            (uint64_t)snapshot.devices[i].kind,
            (uint64_t)snapshot.devices[i].vendor_id,
            (uint64_t)snapshot.devices[i].device_id,
            (uint64_t)snapshot.devices[i].bus,
            (uint64_t)snapshot.devices[i].slot,
            (uint64_t)snapshot.devices[i].init_priority);
    }
    for (uint32_t i = 0; i < SLM_PLAN_CAP; i++) {
        if (plan_table[i].state == SLM_PLAN_EMPTY) {
            continue;
        }
        kprintf("  plan[%u] action=%u state=%u risk=%u conf=%u score=%u allow=%u qd=%u poll=%u dma=%uKiB\n",
            (uint64_t)plan_table[i].plan_id,
            (uint64_t)plan_table[i].request.action,
            (uint64_t)plan_table[i].state,
            (uint64_t)plan_table[i].request.risk_level,
            (uint64_t)plan_table[i].expected_confidence,
            (uint64_t)plan_table[i].validation_score,
            (uint64_t)plan_table[i].request.allow_apply,
            (uint64_t)plan_table[i].request.queue_depth_hint,
            (uint64_t)plan_table[i].request.poll_budget_hint,
            (uint64_t)plan_table[i].request.dma_window_kib_hint);
    }

    for (uint32_t action = 1; action < SLM_ACTION_COUNT; action++) {
        const slm_learning_entry_t *entry = &snapshot.learning_profile.actions[action];
        if (entry->confidence == 0 && entry->attempts == 0 && entry->rejections == 0) {
            continue;
        }
        kprintf("  learn[action=%u] conf=%u attempts=%u ok=%u fail=%u timeout=%u reject=%u\n",
            (uint64_t)action,
            (uint64_t)entry->confidence,
            (uint64_t)entry->attempts,
            (uint64_t)entry->successes,
            (uint64_t)entry->failures,
            (uint64_t)entry->timeouts,
            (uint64_t)entry->rejections);
    }
    kprintf("=================================\n");
}
