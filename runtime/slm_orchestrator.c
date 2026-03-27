/*
 * AIOS Kernel - SLM Hardware Orchestrator
 * AI-Native Operating System
 */

#include <runtime/slm_orchestrator.h>
#include <kernel/health.h>
#include <kernel/time.h>
#include <drivers/e1000.h>
#include <drivers/storage_host.h>
#include <drivers/usb_host.h>
#include <drivers/serial.h>
#include <drivers/vga.h>

static slm_plan_t plan_table[SLM_PLAN_CAP];
static uint32_t next_plan_id = 1;

static bool device_is_io_kind(platform_device_kind_t kind) {
    return kind == PLATFORM_DEVICE_ETHERNET ||
           kind == PLATFORM_DEVICE_USB ||
           kind == PLATFORM_DEVICE_STORAGE;
}

static const char *io_mode_name(slm_io_mode_t mode) {
    switch (mode) {
        case SLM_IO_MODE_CONSERVATIVE: return "conservative";
        case SLM_IO_MODE_BALANCED:     return "balanced";
        case SLM_IO_MODE_AGGRESSIVE:   return "aggressive";
        default:                       return "unknown";
    }
}

static void compute_io_profile(slm_io_profile_t *out) {
    if (!out) {
        return;
    }

    const platform_probe_summary_t *summary = platform_probe_summary();
    const memory_selftest_result_t *profile = kernel_memory_selftest_last();
    uint32_t total_io = summary->ethernet_count + summary->usb_count + summary->storage_count;
    uint32_t ready = 0;

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

    if (out->degraded_controllers > 0 || profile->tier == BOOT_PERF_TIER_LOW) {
        out->mode = SLM_IO_MODE_CONSERVATIVE;
        out->recommended_queue_depth = 8;
        out->recommended_poll_budget = 256;
        out->recommended_dma_window_kib = 64;
        return;
    }

    if (profile->tier == BOOT_PERF_TIER_HIGH && out->ready_controllers >= 2) {
        out->mode = SLM_IO_MODE_AGGRESSIVE;
        out->recommended_queue_depth = 32;
        out->recommended_poll_budget = 1024;
        out->recommended_dma_window_kib = 256;
        return;
    }

    out->mode = SLM_IO_MODE_BALANCED;
    out->recommended_queue_depth = 16;
    out->recommended_poll_budget = 512;
    out->recommended_dma_window_kib = 128;
}

static void apply_io_defaults(slm_plan_request_t *req, const slm_io_profile_t *profile) {
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
}

static bool request_valid(const slm_plan_request_t *req) {
    kernel_health_summary_t health;

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

    kernel_health_get_summary(&health);
    if (health.level == KERNEL_STABILITY_UNSAFE &&
        req->action != SLM_ACTION_REPROBE_PCI &&
        req->action != SLM_ACTION_IO_AUDIT) {
        return false;
    }
    if (!health.risky_io_allowed &&
        req->risk_level > 0 &&
        (req->template_id == SLM_TEMPLATE_PCI_ETHERNET ||
         req->template_id == SLM_TEMPLATE_PCI_USB ||
         req->template_id == SLM_TEMPLATE_PCI_STORAGE)) {
        return false;
    }

    switch (req->action) {
        case SLM_ACTION_REPROBE_PCI:
        case SLM_ACTION_IO_AUDIT:
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
            (uint64_t)(uintptr_t)label,
            (int64_t)status);
        return;
    }

    serial_printf("[SLM] Seeded plan %u label=%s action=%u risk=%u qd=%u poll=%u dma=%uKiB\n",
        (uint64_t)plan_id,
        (uint64_t)(uintptr_t)label,
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
    for (uint32_t i = 0; i < SLM_PLAN_CAP; i++) {
        plan_table[i].plan_id = 0;
        plan_table[i].state = SLM_PLAN_EMPTY;
        plan_table[i].last_status = AIOS_OK;
        plan_table[i].created_ts_ns = 0;
        plan_table[i].applied_ts_ns = 0;
    }

    next_plan_id = 1;

    kprintf("\n");
    kprintf("    SLM Hardware Orchestrator initialized:\n");
    kprintf("    Plan slots: %u\n", (uint64_t)SLM_PLAN_CAP);
    kprintf("    Templates: discovery, pci-ethernet, pci-usb, pci-storage\n");
    serial_write("[SLM] Hardware orchestrator ready\n");
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
    out->total_detected_devices = summary->matched_devices;
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
    compute_io_profile(&out->io_profile);

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
    slot->state = SLM_PLAN_PROPOSED;
    slot->state = request_valid(req) ? SLM_PLAN_VALIDATED : SLM_PLAN_REJECTED;

    serial_printf("[SLM] Plan %u submitted template=%u action=%u state=%u qd=%u poll=%u dma=%uKiB\n",
        (uint64_t)slot->plan_id,
        (uint64_t)slot->request.template_id,
        (uint64_t)slot->request.action,
        (uint64_t)slot->state,
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
    if (!plan) {
        return AIOS_ERR_INVAL;
    }
    if (plan->state != SLM_PLAN_VALIDATED || !plan->request.allow_apply) {
        plan->state = SLM_PLAN_REJECTED;
        plan->last_status = AIOS_ERR_PERM;
        return AIOS_ERR_PERM;
    }

    plan->last_status = apply_request(&plan->request);
    plan->applied_ts_ns = kernel_time_monotonic_ns();
    plan->state = (plan->last_status == AIOS_OK) ? SLM_PLAN_APPLIED : SLM_PLAN_FAILED;

    serial_printf("[SLM] Plan %u apply status=%d state=%u\n",
        (uint64_t)plan->plan_id,
        (int64_t)plan->last_status,
        (uint64_t)plan->state);

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
        (uint64_t)(uintptr_t)kernel_stability_name(snapshot.health_level),
        (uint64_t)snapshot.autonomy_allowed,
        (uint64_t)snapshot.risky_io_allowed);
    kprintf("I/O profile: mode=%s ready=%u degraded=%u pcie=%u qd=%u poll=%u dma=%uKiB\n",
        io_mode_name(snapshot.io_profile.mode),
        (uint64_t)snapshot.io_profile.ready_controllers,
        (uint64_t)snapshot.io_profile.degraded_controllers,
        (uint64_t)snapshot.io_profile.pcie_io_devices,
        (uint64_t)snapshot.io_profile.recommended_queue_depth,
        (uint64_t)snapshot.io_profile.recommended_poll_budget,
        (uint64_t)snapshot.io_profile.recommended_dma_window_kib);
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
        kprintf("  plan[%u] action=%u state=%u risk=%u allow=%u qd=%u poll=%u dma=%uKiB\n",
            (uint64_t)plan_table[i].plan_id,
            (uint64_t)plan_table[i].request.action,
            (uint64_t)plan_table[i].state,
            (uint64_t)plan_table[i].request.risk_level,
            (uint64_t)plan_table[i].request.allow_apply,
            (uint64_t)plan_table[i].request.queue_depth_hint,
            (uint64_t)plan_table[i].request.poll_budget_hint,
            (uint64_t)plan_table[i].request.dma_window_kib_hint);
    }
    kprintf("=================================\n");
}
