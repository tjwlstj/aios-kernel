/*
 * AIOS Kernel - Driver Model / Stack Snapshot
 * AI-Native Operating System
 */

#include <drivers/driver_model.h>
#include <drivers/e1000.h>
#include <drivers/storage_host.h>
#include <drivers/usb_host.h>
#include <drivers/serial.h>
#include <kernel/selftest.h>
#include <lib/string.h>

#define PCI_VENDOR_INTEL 0x8086
#define E1000_DEV_82540EM 0x100E
#define E1000_DEV_82545EM 0x100F
#define E1000_DEV_82574L  0x10D3

static uint16_t clamp_u16_hint(int32_t value, uint16_t min_value, uint16_t max_value) {
    if (value < min_value) {
        return min_value;
    }
    if (value > max_value) {
        return max_value;
    }
    return (uint16_t)value;
}

static uint32_t clamp_u32_hint(int32_t value, uint32_t min_value, uint32_t max_value) {
    if (value < 0 || (uint32_t)value < min_value) {
        return min_value;
    }
    if ((uint32_t)value > max_value) {
        return max_value;
    }
    return (uint32_t)value;
}

static uint8_t clamp_u8_hint(int32_t value) {
    if (value < 0) {
        return 0;
    }
    if (value > 100) {
        return 100;
    }
    return (uint8_t)value;
}

static void copy_name(char *dst, const char *src, uint32_t max_len) {
    uint32_t i = 0;

    if (!dst || max_len == 0) {
        return;
    }

    if (!src) {
        dst[0] = '\0';
        return;
    }

    while (src[i] && i + 1 < max_len) {
        dst[i] = src[i];
        i++;
    }
    dst[i] = '\0';
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

static bool e1000_supported_device(uint16_t vendor_id, uint16_t device_id) {
    if (vendor_id != PCI_VENDOR_INTEL) {
        return false;
    }

    switch (device_id) {
        case E1000_DEV_82540EM:
        case E1000_DEV_82545EM:
        case E1000_DEV_82574L:
            return true;
        default:
            return false;
    }
}

static driver_policy_hint_t policy_hint_for_entry(driver_class_t class_id,
                                                  driver_stage_t stage,
                                                  bool pcie_capable,
                                                  boot_perf_tier_t tier) {
    driver_policy_hint_t hint;

    switch (class_id) {
        case DRIVER_CLASS_NET:
            hint.queue_depth_hint = 16;
            hint.poll_budget_hint = 256;
            hint.dma_window_kib_hint = 128;
            break;
        case DRIVER_CLASS_USB:
            hint.queue_depth_hint = 8;
            hint.poll_budget_hint = 128;
            hint.dma_window_kib_hint = 64;
            break;
        case DRIVER_CLASS_STORAGE:
            hint.queue_depth_hint = 16;
            hint.poll_budget_hint = 256;
            hint.dma_window_kib_hint = 128;
            break;
        case DRIVER_CLASS_NONE:
        default:
            hint.queue_depth_hint = 8;
            hint.poll_budget_hint = 128;
            hint.dma_window_kib_hint = 64;
            break;
    }

    hint.confidence = 0;
    hint.observe_only = true;

    switch (stage) {
        case DRIVER_STAGE_DISCOVERED:
            hint.queue_depth_hint = clamp_u16_hint(hint.queue_depth_hint / 2, 4, 64);
            hint.poll_budget_hint = clamp_u16_hint(hint.poll_budget_hint / 2, 64, 2048);
            hint.dma_window_kib_hint = clamp_u32_hint(hint.dma_window_kib_hint / 2, 16, 512);
            hint.confidence = 25;
            break;
        case DRIVER_STAGE_BOOTSTRAP:
            hint.queue_depth_hint = clamp_u16_hint(hint.queue_depth_hint / 2, 4, 64);
            hint.poll_budget_hint = clamp_u16_hint(hint.poll_budget_hint / 2, 64, 2048);
            hint.dma_window_kib_hint = clamp_u32_hint(hint.dma_window_kib_hint / 2, 16, 512);
            hint.confidence = 40;
            break;
        case DRIVER_STAGE_CONTROL_READY:
            hint.confidence = 62;
            hint.observe_only = false;
            break;
        case DRIVER_STAGE_PARTIAL_IO:
            hint.queue_depth_hint = clamp_u16_hint(hint.queue_depth_hint + 8, 8, 64);
            hint.poll_budget_hint = clamp_u16_hint(hint.poll_budget_hint + 128, 128, 2048);
            hint.dma_window_kib_hint = clamp_u32_hint(hint.dma_window_kib_hint + 32, 32, 512);
            hint.confidence = 78;
            hint.observe_only = false;
            break;
        case DRIVER_STAGE_ACTIVE_IO:
            hint.queue_depth_hint = clamp_u16_hint(hint.queue_depth_hint + 12, 8, 64);
            hint.poll_budget_hint = clamp_u16_hint(hint.poll_budget_hint + 256, 128, 2048);
            hint.dma_window_kib_hint = clamp_u32_hint(hint.dma_window_kib_hint + 64, 32, 512);
            hint.confidence = 90;
            hint.observe_only = false;
            break;
        case DRIVER_STAGE_DEGRADED:
            hint.queue_depth_hint = clamp_u16_hint(hint.queue_depth_hint / 2, 4, 64);
            hint.poll_budget_hint = clamp_u16_hint(hint.poll_budget_hint / 2, 64, 2048);
            hint.dma_window_kib_hint = clamp_u32_hint(hint.dma_window_kib_hint / 2, 16, 512);
            hint.confidence = 22;
            break;
        case DRIVER_STAGE_ABSENT:
        default:
            hint.queue_depth_hint = 0;
            hint.poll_budget_hint = 0;
            hint.dma_window_kib_hint = 0;
            hint.confidence = 0;
            break;
    }

    if (!hint.observe_only) {
        if (tier == BOOT_PERF_TIER_HIGH) {
            hint.queue_depth_hint = clamp_u16_hint(hint.queue_depth_hint + 4, 8, 64);
            hint.poll_budget_hint = clamp_u16_hint(hint.poll_budget_hint + 128, 128, 2048);
            hint.dma_window_kib_hint = clamp_u32_hint(hint.dma_window_kib_hint + 32, 32, 512);
            hint.confidence = clamp_u8_hint(hint.confidence + 6);
        } else if (tier == BOOT_PERF_TIER_LOW) {
            hint.queue_depth_hint = clamp_u16_hint(hint.queue_depth_hint - 4, 4, 64);
            hint.poll_budget_hint = clamp_u16_hint(hint.poll_budget_hint - 64, 64, 2048);
            hint.dma_window_kib_hint = clamp_u32_hint(hint.dma_window_kib_hint - 16, 16, 512);
            hint.confidence = clamp_u8_hint(hint.confidence - 10);
        }
    }

    if (pcie_capable && !hint.observe_only) {
        hint.queue_depth_hint = clamp_u16_hint(hint.queue_depth_hint + 4, 8, 64);
        hint.poll_budget_hint = clamp_u16_hint(hint.poll_budget_hint + 64, 128, 2048);
        hint.dma_window_kib_hint = clamp_u32_hint(hint.dma_window_kib_hint + 16, 32, 512);
        hint.confidence = clamp_u8_hint(hint.confidence + 4);
    }

    return hint;
}

static void snapshot_append_entry(driver_stack_snapshot_t *out,
                                  const driver_stack_entry_t *entry) {
    if (!out || !entry || out->count >= DRIVER_STACK_MAX_ENTRIES) {
        return;
    }

    out->entries[out->count++] = *entry;

    if (driver_stage_ready(entry->stage)) {
        out->ready_count++;
    } else if (entry->stage != DRIVER_STAGE_ABSENT) {
        out->degraded_count++;
    }

    if (entry->present && entry->pcie_capable) {
        out->pcie_device_count++;
    }
}

static void snapshot_merge_policy(driver_stack_snapshot_t *out) {
    bool have_present = false;
    bool have_apply = false;
    uint8_t confidence = 100;

    if (!out) {
        return;
    }

    out->merged_policy.queue_depth_hint = 8;
    out->merged_policy.poll_budget_hint = 128;
    out->merged_policy.dma_window_kib_hint = 64;
    out->merged_policy.confidence = 0;
    out->merged_policy.observe_only = true;

    for (uint32_t i = 0; i < out->count; i++) {
        const driver_stack_entry_t *entry = &out->entries[i];
        if (!entry->present) {
            continue;
        }

        have_present = true;
        if (!entry->policy.observe_only) {
            have_apply = true;
        }

        if (entry->policy.queue_depth_hint > out->merged_policy.queue_depth_hint) {
            out->merged_policy.queue_depth_hint = entry->policy.queue_depth_hint;
        }
        if (entry->policy.poll_budget_hint > out->merged_policy.poll_budget_hint) {
            out->merged_policy.poll_budget_hint = entry->policy.poll_budget_hint;
        }
        if (entry->policy.dma_window_kib_hint > out->merged_policy.dma_window_kib_hint) {
            out->merged_policy.dma_window_kib_hint = entry->policy.dma_window_kib_hint;
        }
        if (entry->policy.confidence < confidence) {
            confidence = entry->policy.confidence;
        }
    }

    if (!have_present) {
        out->merged_policy.queue_depth_hint = 0;
        out->merged_policy.poll_budget_hint = 0;
        out->merged_policy.dma_window_kib_hint = 0;
        out->merged_policy.confidence = 0;
        out->merged_policy.observe_only = true;
        return;
    }

    if (out->degraded_count > 0) {
        out->merged_policy.queue_depth_hint =
            clamp_u16_hint(out->merged_policy.queue_depth_hint / 2, 4, 64);
        out->merged_policy.poll_budget_hint =
            clamp_u16_hint(out->merged_policy.poll_budget_hint / 2, 64, 2048);
        out->merged_policy.dma_window_kib_hint =
            clamp_u32_hint(out->merged_policy.dma_window_kib_hint / 2, 16, 512);
        confidence = clamp_u8_hint(confidence - 12);
    }

    out->merged_policy.confidence = confidence;
    out->merged_policy.observe_only = !have_apply;
}

static void build_net_entry(driver_stack_snapshot_t *out, boot_perf_tier_t tier) {
    const platform_device_t *dev = find_first_device(PLATFORM_DEVICE_ETHERNET);
    e1000_driver_info_t nic;
    driver_stack_entry_t entry;
    bool have_info = (e1000_driver_info(&nic) == AIOS_OK);

    memset(&entry, 0, sizeof(entry));
    entry.class_id = DRIVER_CLASS_NET;
    entry.kind = PLATFORM_DEVICE_ETHERNET;
    entry.stage = DRIVER_STAGE_ABSENT;
    entry.profile_id = DRIVER_PROFILE_NONE;
    entry.last_status = AIOS_ERR_NODEV;

    if (!dev && (!have_info || !nic.present)) {
        return;
    }

    entry.present = (dev != NULL) || (have_info && nic.present);
    entry.vendor_id = dev ? dev->vendor_id : nic.vendor_id;
    entry.device_id = dev ? dev->device_id : nic.device_id;
    entry.bus = dev ? dev->bus : nic.bus;
    entry.slot = dev ? dev->slot : nic.slot;
    entry.function = dev ? dev->function : nic.function;
    entry.pcie_capable = dev ? dev->pcie_capable : false;
    entry.ready = have_info && nic.ready;
    entry.last_status = have_info ? nic.last_tx_status : AIOS_ERR_NODEV;

    if (e1000_supported_device(entry.vendor_id, entry.device_id)) {
        entry.profile_id = DRIVER_PROFILE_INTEL_E1000;
        copy_name(entry.name, "intel-e1000", sizeof(entry.name));
    } else {
        entry.profile_id = DRIVER_PROFILE_GENERIC_PCI_NET;
        copy_name(entry.name, "net-generic", sizeof(entry.name));
    }

    if (!have_info || !nic.present) {
        entry.stage = DRIVER_STAGE_DISCOVERED;
    } else if (!nic.ready) {
        entry.stage = DRIVER_STAGE_BOOTSTRAP;
    } else if (!nic.link_up) {
        entry.stage = DRIVER_STAGE_DEGRADED;
    } else {
        entry.data_path_ready = nic.rx_ready || nic.last_tx_status == AIOS_OK;
        entry.stage = entry.data_path_ready ? DRIVER_STAGE_PARTIAL_IO
                                            : DRIVER_STAGE_CONTROL_READY;
    }

    entry.policy = policy_hint_for_entry(entry.class_id, entry.stage,
        entry.pcie_capable, tier);
    snapshot_append_entry(out, &entry);
}

static void build_usb_entry(driver_stack_snapshot_t *out, boot_perf_tier_t tier) {
    const platform_device_t *dev = find_first_device(PLATFORM_DEVICE_USB);
    usb_host_info_t usb;
    driver_stack_entry_t entry;
    bool have_info = (usb_host_info(&usb) == AIOS_OK);

    memset(&entry, 0, sizeof(entry));
    entry.class_id = DRIVER_CLASS_USB;
    entry.kind = PLATFORM_DEVICE_USB;
    entry.stage = DRIVER_STAGE_ABSENT;
    entry.profile_id = DRIVER_PROFILE_NONE;
    entry.last_status = AIOS_ERR_NODEV;

    if (!dev && (!have_info || !usb.present)) {
        return;
    }

    entry.present = (dev != NULL) || (have_info && usb.present);
    entry.vendor_id = dev ? dev->vendor_id : usb.vendor_id;
    entry.device_id = dev ? dev->device_id : usb.device_id;
    entry.bus = dev ? dev->bus : usb.bus;
    entry.slot = dev ? dev->slot : usb.slot;
    entry.function = dev ? dev->function : usb.function;
    entry.pcie_capable = dev ? dev->pcie_capable : false;
    entry.ready = have_info && usb.ready;
    entry.last_status = have_info ? usb.last_init_status : AIOS_ERR_NODEV;

    if (have_info && usb.controller_kind == USB_HOST_CONTROLLER_XHCI) {
        entry.profile_id = DRIVER_PROFILE_XHCI;
        copy_name(entry.name, "usb-xhci", sizeof(entry.name));
    } else {
        entry.profile_id = DRIVER_PROFILE_GENERIC_USB_HOST;
        copy_name(entry.name, "usb-host", sizeof(entry.name));
    }

    if (!have_info || !usb.present) {
        entry.stage = DRIVER_STAGE_DISCOVERED;
    } else if (!usb.ready) {
        entry.stage = DRIVER_STAGE_BOOTSTRAP;
    } else if (usb.controller_kind == USB_HOST_CONTROLLER_XHCI && !usb.capability_valid) {
        entry.stage = DRIVER_STAGE_DEGRADED;
    } else {
        entry.stage = DRIVER_STAGE_CONTROL_READY;
    }

    entry.policy = policy_hint_for_entry(entry.class_id, entry.stage,
        entry.pcie_capable, tier);
    snapshot_append_entry(out, &entry);
}

static void build_storage_entry(driver_stack_snapshot_t *out, boot_perf_tier_t tier) {
    const platform_device_t *dev = find_first_device(PLATFORM_DEVICE_STORAGE);
    storage_host_info_t storage;
    driver_stack_entry_t entry;
    bool have_info = (storage_host_info(&storage) == AIOS_OK);

    memset(&entry, 0, sizeof(entry));
    entry.class_id = DRIVER_CLASS_STORAGE;
    entry.kind = PLATFORM_DEVICE_STORAGE;
    entry.stage = DRIVER_STAGE_ABSENT;
    entry.profile_id = DRIVER_PROFILE_NONE;
    entry.last_status = AIOS_ERR_NODEV;

    if (!dev && (!have_info || !storage.present)) {
        return;
    }

    entry.present = (dev != NULL) || (have_info && storage.present);
    entry.vendor_id = dev ? dev->vendor_id : storage.vendor_id;
    entry.device_id = dev ? dev->device_id : storage.device_id;
    entry.bus = dev ? dev->bus : storage.bus;
    entry.slot = dev ? dev->slot : storage.slot;
    entry.function = dev ? dev->function : storage.function;
    entry.pcie_capable = dev ? dev->pcie_capable : false;
    entry.ready = have_info && storage.ready;
    entry.last_status = have_info ? storage.last_init_status : AIOS_ERR_NODEV;

    switch (have_info ? storage.controller_kind : STORAGE_HOST_CONTROLLER_NONE) {
        case STORAGE_HOST_CONTROLLER_IDE:
            entry.profile_id = DRIVER_PROFILE_IDE_COMPAT;
            copy_name(entry.name, "storage-ide", sizeof(entry.name));
            break;
        case STORAGE_HOST_CONTROLLER_AHCI:
            entry.profile_id = DRIVER_PROFILE_AHCI;
            copy_name(entry.name, "storage-ahci", sizeof(entry.name));
            break;
        case STORAGE_HOST_CONTROLLER_NVME:
            entry.profile_id = DRIVER_PROFILE_NVME;
            copy_name(entry.name, "storage-nvme", sizeof(entry.name));
            break;
        default:
            entry.profile_id = DRIVER_PROFILE_GENERIC_STORAGE_HOST;
            copy_name(entry.name, "storage-host", sizeof(entry.name));
            break;
    }

    if (!have_info || !storage.present) {
        entry.stage = DRIVER_STAGE_DISCOVERED;
    } else if (!storage.ready) {
        entry.stage = DRIVER_STAGE_BOOTSTRAP;
    } else if (storage.controller_kind == STORAGE_HOST_CONTROLLER_IDE &&
               !storage.primary_channel_live && !storage.secondary_channel_live) {
        entry.stage = DRIVER_STAGE_DEGRADED;
    } else {
        entry.stage = DRIVER_STAGE_CONTROL_READY;
    }

    entry.policy = policy_hint_for_entry(entry.class_id, entry.stage,
        entry.pcie_capable, tier);
    snapshot_append_entry(out, &entry);
}

const char *driver_class_name(driver_class_t class_id) {
    switch (class_id) {
        case DRIVER_CLASS_NET:     return "net";
        case DRIVER_CLASS_USB:     return "usb";
        case DRIVER_CLASS_STORAGE: return "storage";
        case DRIVER_CLASS_NONE:
        default:                   return "none";
    }
}

const char *driver_stage_name(driver_stage_t stage) {
    switch (stage) {
        case DRIVER_STAGE_DISCOVERED:    return "discovered";
        case DRIVER_STAGE_BOOTSTRAP:     return "bootstrap";
        case DRIVER_STAGE_CONTROL_READY: return "control-ready";
        case DRIVER_STAGE_PARTIAL_IO:    return "partial-io";
        case DRIVER_STAGE_ACTIVE_IO:     return "active-io";
        case DRIVER_STAGE_DEGRADED:      return "degraded";
        case DRIVER_STAGE_ABSENT:
        default:                         return "absent";
    }
}

const char *driver_profile_name(driver_profile_id_t profile_id) {
    switch (profile_id) {
        case DRIVER_PROFILE_GENERIC_PCI_NET:      return "generic-pci-net";
        case DRIVER_PROFILE_INTEL_E1000:          return "intel-e1000";
        case DRIVER_PROFILE_GENERIC_USB_HOST:     return "generic-usb-host";
        case DRIVER_PROFILE_XHCI:                 return "xhci";
        case DRIVER_PROFILE_GENERIC_STORAGE_HOST: return "generic-storage-host";
        case DRIVER_PROFILE_IDE_COMPAT:           return "ide-compat";
        case DRIVER_PROFILE_AHCI:                 return "ahci";
        case DRIVER_PROFILE_NVME:                 return "nvme";
        case DRIVER_PROFILE_NONE:
        default:                                  return "none";
    }
}

aios_status_t driver_model_snapshot_read(driver_stack_snapshot_t *out) {
    const memory_selftest_result_t *profile;

    if (!out) {
        return AIOS_ERR_INVAL;
    }

    memset(out, 0, sizeof(*out));
    profile = kernel_memory_selftest_last();

    build_net_entry(out, profile->tier);
    build_usb_entry(out, profile->tier);
    build_storage_entry(out, profile->tier);
    snapshot_merge_policy(out);
    return AIOS_OK;
}

void driver_model_dump(void) {
    driver_stack_snapshot_t snapshot;
    if (driver_model_snapshot_read(&snapshot) != AIOS_OK) {
        return;
    }

    serial_printf("[DRV] stack count=%u ready=%u degraded=%u pcie=%u merged qd=%u poll=%u dma=%uKiB conf=%u observe=%u\n",
        (uint64_t)snapshot.count,
        (uint64_t)snapshot.ready_count,
        (uint64_t)snapshot.degraded_count,
        (uint64_t)snapshot.pcie_device_count,
        (uint64_t)snapshot.merged_policy.queue_depth_hint,
        (uint64_t)snapshot.merged_policy.poll_budget_hint,
        (uint64_t)snapshot.merged_policy.dma_window_kib_hint,
        (uint64_t)snapshot.merged_policy.confidence,
        snapshot.merged_policy.observe_only ? 1ULL : 0ULL);

    for (uint32_t i = 0; i < snapshot.count; i++) {
        const driver_stack_entry_t *entry = &snapshot.entries[i];
        serial_printf("[DRV] %s profile=%s stage=%s ready=%u data=%u vendor=%x device=%x pci=%u:%u.%u conf=%u observe=%u\n",
            (uint64_t)(uintptr_t)driver_class_name(entry->class_id),
            (uint64_t)(uintptr_t)driver_profile_name(entry->profile_id),
            (uint64_t)(uintptr_t)driver_stage_name(entry->stage),
            entry->ready ? 1ULL : 0ULL,
            entry->data_path_ready ? 1ULL : 0ULL,
            (uint64_t)entry->vendor_id,
            (uint64_t)entry->device_id,
            (uint64_t)entry->bus,
            (uint64_t)entry->slot,
            (uint64_t)entry->function,
            (uint64_t)entry->policy.confidence,
            entry->policy.observe_only ? 1ULL : 0ULL);
    }
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
