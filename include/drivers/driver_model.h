/*
 * AIOS Kernel - Driver Model / Stack Snapshot
 * AI-Native Operating System
 */

#ifndef _AIOS_DRIVER_MODEL_H
#define _AIOS_DRIVER_MODEL_H

#include <kernel/types.h>
#include <drivers/platform_probe.h>

#define DRIVER_STACK_MAX_ENTRIES 8

typedef enum {
    DRIVER_CLASS_NONE = 0,
    DRIVER_CLASS_NET = 1,
    DRIVER_CLASS_USB = 2,
    DRIVER_CLASS_STORAGE = 3,
    DRIVER_CLASS_COUNT = 4,
} driver_class_t;

typedef enum {
    DRIVER_STAGE_ABSENT = 0,
    DRIVER_STAGE_DISCOVERED = 1,
    DRIVER_STAGE_BOOTSTRAP = 2,
    DRIVER_STAGE_CONTROL_READY = 3,
    DRIVER_STAGE_PARTIAL_IO = 4,
    DRIVER_STAGE_ACTIVE_IO = 5,
    DRIVER_STAGE_DEGRADED = 6,
    DRIVER_STAGE_COUNT = 7,
} driver_stage_t;

typedef enum {
    DRIVER_PROFILE_NONE = 0,
    DRIVER_PROFILE_GENERIC_PCI_NET = 1,
    DRIVER_PROFILE_INTEL_E1000 = 2,
    DRIVER_PROFILE_GENERIC_USB_HOST = 3,
    DRIVER_PROFILE_XHCI = 4,
    DRIVER_PROFILE_GENERIC_STORAGE_HOST = 5,
    DRIVER_PROFILE_IDE_COMPAT = 6,
    DRIVER_PROFILE_AHCI = 7,
    DRIVER_PROFILE_NVME = 8,
    DRIVER_PROFILE_COUNT = 9,
} driver_profile_id_t;

AIOS_STATIC_ASSERT(DRIVER_CLASS_COUNT == 4,
    "Update driver class users when enum changes");
AIOS_STATIC_ASSERT(DRIVER_STAGE_COUNT == 7,
    "Update driver stage users when enum changes");
AIOS_STATIC_ASSERT(DRIVER_PROFILE_COUNT == 9,
    "Update driver profile users when enum changes");

typedef struct {
    uint16_t queue_depth_hint;
    uint16_t poll_budget_hint;
    uint32_t dma_window_kib_hint;
    uint8_t confidence;
    bool observe_only;
} driver_policy_hint_t;

typedef struct {
    driver_class_t class_id;
    driver_stage_t stage;
    driver_profile_id_t profile_id;
    platform_device_kind_t kind;
    uint16_t vendor_id;
    uint16_t device_id;
    uint8_t bus;
    uint8_t slot;
    uint8_t function;
    bool present;
    bool ready;
    bool pcie_capable;
    bool data_path_ready;
    aios_status_t last_status;
    driver_policy_hint_t policy;
    char name[32];
} driver_stack_entry_t;

typedef struct {
    uint32_t count;
    uint32_t ready_count;
    uint32_t degraded_count;
    uint32_t pcie_device_count;
    driver_policy_hint_t merged_policy;
    driver_stack_entry_t entries[DRIVER_STACK_MAX_ENTRIES];
} driver_stack_snapshot_t;

static inline bool driver_class_valid(uint32_t class_id) {
    return class_id < DRIVER_CLASS_COUNT;
}

static inline bool driver_stage_valid(uint32_t stage) {
    return stage < DRIVER_STAGE_COUNT;
}

static inline bool driver_stage_ready(driver_stage_t stage) {
    return stage == DRIVER_STAGE_CONTROL_READY ||
           stage == DRIVER_STAGE_PARTIAL_IO ||
           stage == DRIVER_STAGE_ACTIVE_IO;
}

static inline bool driver_stage_data_path_ready(driver_stage_t stage) {
    return stage == DRIVER_STAGE_PARTIAL_IO ||
           stage == DRIVER_STAGE_ACTIVE_IO;
}

static inline const driver_stack_entry_t *driver_model_find_class(
    const driver_stack_snapshot_t *snapshot,
    driver_class_t class_id) {
    if (!snapshot) {
        return NULL;
    }

    for (uint32_t i = 0; i < snapshot->count; i++) {
        if (snapshot->entries[i].class_id == class_id) {
            return &snapshot->entries[i];
        }
    }
    return NULL;
}

const char *driver_class_name(driver_class_t class_id);
const char *driver_stage_name(driver_stage_t stage);
const char *driver_profile_name(driver_profile_id_t profile_id);
aios_status_t driver_model_snapshot_read(driver_stack_snapshot_t *out);
void driver_model_dump(void);

#endif /* _AIOS_DRIVER_MODEL_H */
