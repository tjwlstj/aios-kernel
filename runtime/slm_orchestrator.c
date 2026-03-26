/*
 * AIOS Kernel - SLM Hardware Orchestrator
 * AI-Native Operating System
 */

#include <runtime/slm_orchestrator.h>
#include <kernel/time.h>
#include <drivers/e1000.h>
#include <drivers/serial.h>
#include <drivers/vga.h>

static slm_plan_t plan_table[SLM_PLAN_CAP];
static uint32_t next_plan_id = 1;

static bool request_valid(const slm_plan_request_t *req) {
    if (!req) {
        return false;
    }
    if (req->risk_level > 3) {
        return false;
    }

    switch (req->action) {
        case SLM_ACTION_REPROBE_PCI:
            return req->template_id == SLM_TEMPLATE_DISCOVERY;
        case SLM_ACTION_BOOTSTRAP_E1000:
        case SLM_ACTION_E1000_TX_SMOKE:
        case SLM_ACTION_E1000_DUMP:
            return req->template_id == SLM_TEMPLATE_PCI_ETHERNET;
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

    serial_printf("[SLM] Seeded plan %u label=%s action=%u risk=%u\n",
        (uint64_t)plan_id,
        (uint64_t)(uintptr_t)label,
        (uint64_t)req->action,
        (uint64_t)req->risk_level);
}

static void seed_boot_plans(void) {
    slm_plan_request_t discovery = {
        .template_id = SLM_TEMPLATE_DISCOVERY,
        .action = SLM_ACTION_REPROBE_PCI,
        .target_vendor_id = 0,
        .target_device_id = 0,
        .target_kind = PLATFORM_DEVICE_UNKNOWN,
        .risk_level = 0,
        .allow_apply = false,
    };
    seed_plan(&discovery, "inventory-refresh");

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

    if (!ethernet) {
        return;
    }

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
        seed_plan(&tx_smoke, "ethernet-tx-smoke");
    }
}

static aios_status_t apply_request(const slm_plan_request_t *req) {
    switch (req->action) {
        case SLM_ACTION_REPROBE_PCI:
            return platform_probe_init();
        case SLM_ACTION_BOOTSTRAP_E1000:
            return e1000_driver_init();
        case SLM_ACTION_E1000_TX_SMOKE:
            return e1000_driver_tx_smoke();
        case SLM_ACTION_E1000_DUMP:
            e1000_driver_dump();
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
    e1000_driver_info_t nic;
    (void)e1000_driver_info(&nic);

    out->ts_ns = kernel_time_monotonic_ns();
    out->tsc_khz = kernel_time_tsc_khz();
    out->invariant_tsc = kernel_time_invariant_tsc();
    out->tier = profile->tier;
    out->memcpy_mib_per_sec = profile->memcpy_mib_per_sec;
    out->total_detected_devices = summary->matched_devices;
    out->listed_devices = 0;
    out->e1000_ready = nic.ready;
    out->e1000_link_up = nic.link_up;

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

    serial_printf("[SLM] Plan %u submitted template=%u action=%u state=%u\n",
        (uint64_t)slot->plan_id,
        (uint64_t)slot->request.template_id,
        (uint64_t)slot->request.action,
        (uint64_t)slot->state);

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
    kprintf("Devices: detected=%u listed=%u | e1000_ready=%u link=%u\n",
        snapshot.total_detected_devices,
        snapshot.listed_devices,
        (uint64_t)snapshot.e1000_ready,
        (uint64_t)snapshot.e1000_link_up);
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
        kprintf("  plan[%u] action=%u state=%u risk=%u allow=%u\n",
            (uint64_t)plan_table[i].plan_id,
            (uint64_t)plan_table[i].request.action,
            (uint64_t)plan_table[i].state,
            (uint64_t)plan_table[i].request.risk_level,
            (uint64_t)plan_table[i].request.allow_apply);
    }
    kprintf("=================================\n");
}
