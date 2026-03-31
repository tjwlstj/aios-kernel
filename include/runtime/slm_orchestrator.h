/*
 * AIOS Kernel - SLM Hardware Orchestrator
 * AI-Native Operating System
 */

#ifndef _AIOS_SLM_ORCHESTRATOR_H
#define _AIOS_SLM_ORCHESTRATOR_H

#include <kernel/types.h>
#include <kernel/health.h>
#include <kernel/selftest.h>
#include <drivers/platform_probe.h>
#include <mm/memory_fabric.h>
#include <runtime/ai_ring.h>

#define SLM_HW_MAX_DEVICES 16
#define SLM_PLAN_CAP       16
#define AGENT_TREE_MAX_NODES 10

typedef enum {
    SLM_TEMPLATE_NONE = 0,
    SLM_TEMPLATE_PCI_ETHERNET = 1,
    SLM_TEMPLATE_PCI_USB = 2,
    SLM_TEMPLATE_PCI_STORAGE = 3,
    SLM_TEMPLATE_DISCOVERY = 4,
} slm_template_t;

typedef enum {
    SLM_ACTION_NONE = 0,
    SLM_ACTION_REPROBE_PCI = 1,
    SLM_ACTION_BOOTSTRAP_E1000 = 2,
    SLM_ACTION_E1000_TX_SMOKE = 3,
    SLM_ACTION_E1000_DUMP = 4,
    SLM_ACTION_BOOTSTRAP_USB = 5,
    SLM_ACTION_USB_DUMP = 6,
    SLM_ACTION_BOOTSTRAP_STORAGE = 7,
    SLM_ACTION_STORAGE_DUMP = 8,
    SLM_ACTION_IO_AUDIT = 9,
    SLM_ACTION_CORE_AUDIT = 10,
    SLM_ACTION_COUNT
} slm_action_t;

typedef enum {
    SLM_PLAN_EMPTY = 0,
    SLM_PLAN_PROPOSED = 1,
    SLM_PLAN_VALIDATED = 2,
    SLM_PLAN_APPLIED = 3,
    SLM_PLAN_FAILED = 4,
    SLM_PLAN_REJECTED = 5,
} slm_plan_state_t;

typedef enum {
    SLM_IO_MODE_CONSERVATIVE = 0,
    SLM_IO_MODE_BALANCED = 1,
    SLM_IO_MODE_AGGRESSIVE = 2,
} slm_io_mode_t;

typedef enum {
    AGENT_OPERATOR_MODE_STABILIZE = 0,
    AGENT_OPERATOR_MODE_BALANCE = 1,
    AGENT_OPERATOR_MODE_EXPLORE = 2,
} agent_operator_mode_t;

typedef enum {
    AGENT_NODE_ROLE_NONE = 0,
    AGENT_NODE_ROLE_MAIN = 1,
    AGENT_NODE_ROLE_GUARDIAN = 2,
    AGENT_NODE_ROLE_ROUTER = 3,
    AGENT_NODE_ROLE_PLANNER = 4,
    AGENT_NODE_ROLE_CRITIC = 5,
    AGENT_NODE_ROLE_SUMMARIZER = 6,
    AGENT_NODE_ROLE_VERIFIER = 7,
    AGENT_NODE_ROLE_MEMORY_DISTILLER = 8,
    AGENT_NODE_ROLE_TOOL_WORKER = 9,
    AGENT_NODE_ROLE_DEVICE = 10,
} agent_node_role_t;

typedef enum {
    AGENT_MODEL_CLASS_MICRO = 0,
    AGENT_MODEL_CLASS_SMALL = 1,
    AGENT_MODEL_CLASS_MEDIUM = 2,
    AGENT_MODEL_CLASS_MAIN = 3,
} agent_model_class_t;

typedef struct {
    uint16_t vendor_id;
    uint16_t device_id;
    uint8_t class_code;
    uint8_t subclass;
    uint8_t prog_if;
    uint8_t bus;
    uint8_t slot;
    uint8_t function;
    uint8_t init_priority;
    bool pcie_capable;
    uint8_t pcie_link_speed;
    uint8_t pcie_link_width;
    platform_device_kind_t kind;
} slm_hw_device_t;

typedef struct {
    uint8_t ready_controllers;
    uint8_t degraded_controllers;
    uint8_t pcie_io_devices;
    slm_io_mode_t mode;
    uint16_t recommended_queue_depth;
    uint16_t recommended_poll_budget;
    uint32_t recommended_dma_window_kib;
} slm_io_profile_t;

typedef struct {
    bool acpi_ready;
    bool xsdt_present;
    bool mcfg_present;
    bool madt_present;
    bool ecam_available;
    pci_cfg_access_mode_t pci_access_mode;
    uint8_t pci_bus_start;
    uint8_t pci_bus_end;
    uint32_t pci_total_functions;
    uint32_t pci_bridge_count;
    uint32_t pci_pcie_functions;
    uint32_t pci_msi_capable_functions;
    uint32_t pci_msix_capable_functions;
    uint8_t compatibility_score;
} slm_fabric_profile_t;

typedef struct {
    uint32_t attempts;
    uint32_t successes;
    uint32_t failures;
    uint32_t timeouts;
    uint32_t rejections;
    uint64_t last_latency_ns;
    aios_status_t last_status;
    uint8_t confidence;
} slm_learning_entry_t;

typedef struct {
    uint32_t boot_epoch;
    uint32_t observed_ready_controllers;
    uint32_t applied_successes;
    uint32_t applied_failures;
    uint32_t rejected_submissions;
    uint8_t global_confidence;
    int8_t io_aggressiveness_bias;
    uint16_t tuned_queue_depth;
    uint16_t tuned_poll_budget;
    uint32_t tuned_dma_window_kib;
    slm_learning_entry_t actions[SLM_ACTION_COUNT];
} slm_learning_profile_t;

typedef struct {
    uint8_t self_consistency;
    uint8_t goal_continuity;
    uint8_t memory_coherence;
    uint8_t policy_stability;
    uint8_t resource_reserve;
    uint8_t safety_margin;
    uint8_t novelty_pressure;
    uint8_t prediction_error;
    uint8_t unresolved_uncertainty;
    uint8_t opportunity_gain;
    uint8_t external_surprise;
    uint8_t hypothesis_diversity_demand;
    uint8_t static_score;
    uint8_t chaos_score;
    int16_t sco_x100;
    agent_operator_mode_t mode;
    uint8_t recommended_max_active_workers;
    uint8_t memory_write_intensity_pct;
    bool adapter_update_allowed;
    bool memory_only_bias;
} agent_main_ai_profile_t;

typedef struct {
    uint16_t recommended_worker_queue_depth;
    uint16_t recommended_token_pipeline_depth;
    uint16_t recommended_planner_fanout;
    uint16_t recommended_microbatch_tokens;
    uint16_t recommended_summary_interval_ms;
    uint16_t recommended_memory_journal_batch;
    uint16_t recommended_submit_ring_entries;
    uint16_t recommended_completion_ring_entries;
    uint32_t recommended_zero_copy_window_kib;
    bool zero_copy_preferred;
    bool shared_kv_preferred;
    bool device_nodes_preferred;
    bool shared_infer_ring_preferred;
} agent_pipeline_profile_t;

typedef struct {
    uint8_t node_id;
    agent_node_role_t role;
    agent_model_class_t model_class;
    uint8_t priority;
    uint8_t budget_share;
    bool active;
    bool persistent;
    bool zero_copy_preferred;
} agent_tree_node_t;

typedef struct {
    uint64_t ts_ns;
    uint64_t tsc_khz;
    bool invariant_tsc;
    boot_perf_tier_t tier;
    uint64_t memcpy_mib_per_sec;
    uint32_t total_detected_devices;
    uint32_t listed_devices;
    kernel_stability_t health_level;
    bool autonomy_allowed;
    bool risky_io_allowed;
    bool e1000_ready;
    bool e1000_link_up;
    bool usb_ready;
    uint8_t usb_controller_kind;
    bool storage_ready;
    uint8_t storage_controller_kind;
    slm_fabric_profile_t fabric_profile;
    memory_fabric_profile_t memory_fabric;
    slm_learning_profile_t learning_profile;
    slm_io_profile_t io_profile;
    ai_ring_runtime_snapshot_t ring_runtime;
    agent_main_ai_profile_t main_ai_profile;
    agent_pipeline_profile_t pipeline_profile;
    uint32_t agent_tree_nodes;
    agent_tree_node_t agent_tree[AGENT_TREE_MAX_NODES];
    slm_hw_device_t devices[SLM_HW_MAX_DEVICES];
} slm_hw_snapshot_t;

typedef struct {
    slm_template_t template_id;
    slm_action_t action;
    uint16_t target_vendor_id;
    uint16_t target_device_id;
    platform_device_kind_t target_kind;
    uint32_t risk_level;
    uint16_t queue_depth_hint;
    uint16_t poll_budget_hint;
    uint32_t dma_window_kib_hint;
    bool allow_apply;
} slm_plan_request_t;

typedef struct {
    uint32_t plan_id;
    slm_plan_request_t request;
    slm_plan_state_t state;
    aios_status_t last_status;
    uint8_t expected_confidence;
    uint8_t validation_score;
    uint64_t created_ts_ns;
    uint64_t applied_ts_ns;
} slm_plan_t;

typedef struct {
    uint32_t count;
    slm_plan_t plans[SLM_PLAN_CAP];
} slm_plan_list_t;

aios_status_t slm_orchestrator_init(void);
aios_status_t slm_snapshot_read(slm_hw_snapshot_t *out);
aios_status_t slm_plan_submit(const slm_plan_request_t *req, uint32_t *plan_id_out);
aios_status_t slm_plan_apply(uint32_t plan_id);
aios_status_t slm_plan_get(uint32_t plan_id, slm_plan_t *out);
aios_status_t slm_plan_list(slm_plan_list_t *out);
void slm_orchestrator_dump(void);

#endif /* _AIOS_SLM_ORCHESTRATOR_H */
