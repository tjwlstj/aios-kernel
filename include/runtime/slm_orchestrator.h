/*
 * AIOS Kernel - SLM Hardware Orchestrator
 * AI-Native Operating System
 */

#ifndef _AIOS_SLM_ORCHESTRATOR_H
#define _AIOS_SLM_ORCHESTRATOR_H

#include <kernel/types.h>
#include <kernel/selftest.h>
#include <drivers/platform_probe.h>

#define SLM_HW_MAX_DEVICES 16
#define SLM_PLAN_CAP       16

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
    uint64_t ts_ns;
    uint64_t tsc_khz;
    bool invariant_tsc;
    boot_perf_tier_t tier;
    uint64_t memcpy_mib_per_sec;
    uint32_t total_detected_devices;
    uint32_t listed_devices;
    bool e1000_ready;
    bool e1000_link_up;
    bool usb_ready;
    uint8_t usb_controller_kind;
    bool storage_ready;
    uint8_t storage_controller_kind;
    slm_io_profile_t io_profile;
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
