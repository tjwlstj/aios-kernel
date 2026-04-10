/*
 * AIOS Kernel - Health Registry
 * AI-Native Operating System
 */

#include <kernel/health.h>
#include <kernel/time.h>
#include <drivers/serial.h>
#include <drivers/vga.h>

static kernel_subsystem_health_t g_health[KERNEL_SUBSYSTEM_COUNT] = {
    [KERNEL_SUBSYSTEM_IDT] = {
        .id = KERNEL_SUBSYSTEM_IDT,
        .name = "Interrupt Descriptor Table",
        .required = true,
        .io_path = false,
    },
    [KERNEL_SUBSYSTEM_TIME] = {
        .id = KERNEL_SUBSYSTEM_TIME,
        .name = "Kernel Time Source",
        .required = true,
        .io_path = false,
    },
    [KERNEL_SUBSYSTEM_SELFTEST] = {
        .id = KERNEL_SUBSYSTEM_SELFTEST,
        .name = "Boot Selftest",
        .required = true,
        .io_path = false,
    },
    [KERNEL_SUBSYSTEM_ACPI] = {
        .id = KERNEL_SUBSYSTEM_ACPI,
        .name = "ACPI Fabric",
        .required = false,
        .io_path = false,
    },
    [KERNEL_SUBSYSTEM_PCI_CORE] = {
        .id = KERNEL_SUBSYSTEM_PCI_CORE,
        .name = "PCI Core",
        .required = true,
        .io_path = true,
    },
    [KERNEL_SUBSYSTEM_TENSOR_MM] = {
        .id = KERNEL_SUBSYSTEM_TENSOR_MM,
        .name = "Tensor Memory Manager",
        .required = true,
        .io_path = false,
    },
    [KERNEL_SUBSYSTEM_MEMORY_FABRIC] = {
        .id = KERNEL_SUBSYSTEM_MEMORY_FABRIC,
        .name = "Memory Fabric",
        .required = true,
        .io_path = false,
    },
    [KERNEL_SUBSYSTEM_SCHED] = {
        .id = KERNEL_SUBSYSTEM_SCHED,
        .name = "AI Scheduler",
        .required = true,
        .io_path = false,
    },
    [KERNEL_SUBSYSTEM_ACCEL] = {
        .id = KERNEL_SUBSYSTEM_ACCEL,
        .name = "Accelerator HAL",
        .required = false,
        .io_path = false,
    },
    [KERNEL_SUBSYSTEM_PCI_PROBE] = {
        .id = KERNEL_SUBSYSTEM_PCI_PROBE,
        .name = "PCI Probe",
        .required = true,
        .io_path = true,
    },
    [KERNEL_SUBSYSTEM_NETWORK] = {
        .id = KERNEL_SUBSYSTEM_NETWORK,
        .name = "Network I/O",
        .required = false,
        .io_path = true,
    },
    [KERNEL_SUBSYSTEM_USB] = {
        .id = KERNEL_SUBSYSTEM_USB,
        .name = "USB I/O",
        .required = false,
        .io_path = true,
    },
    [KERNEL_SUBSYSTEM_STORAGE] = {
        .id = KERNEL_SUBSYSTEM_STORAGE,
        .name = "Storage I/O",
        .required = false,
        .io_path = true,
    },
    [KERNEL_SUBSYSTEM_SYSCALL] = {
        .id = KERNEL_SUBSYSTEM_SYSCALL,
        .name = "AI Syscall Interface",
        .required = true,
        .io_path = false,
    },
    [KERNEL_SUBSYSTEM_AUTONOMY] = {
        .id = KERNEL_SUBSYSTEM_AUTONOMY,
        .name = "Autonomy Control",
        .required = true,
        .io_path = false,
    },
    [KERNEL_SUBSYSTEM_SLM] = {
        .id = KERNEL_SUBSYSTEM_SLM,
        .name = "SLM Orchestrator",
        .required = true,
        .io_path = false,
    },
};

AIOS_STATIC_ASSERT(ARRAY_SIZE(g_health) == KERNEL_SUBSYSTEM_COUNT,
    "Health registry table must stay aligned with kernel_subsystem_id_t");

static uint64_t health_now_ns(void) {
    return kernel_time_monotonic_ns();
}

aios_status_t kernel_health_init(void) {
    for (uint32_t i = 0; i < KERNEL_SUBSYSTEM_COUNT; i++) {
        g_health[i].state = KERNEL_HEALTH_UNKNOWN;
        g_health[i].last_status = AIOS_OK;
        g_health[i].updated_ts_ns = 0;
    }
    return AIOS_OK;
}

void kernel_health_mark(kernel_subsystem_id_t id, kernel_health_state_t state,
                        aios_status_t status) {
    if (!kernel_subsystem_id_valid((uint32_t)id)) {
        return;
    }

    g_health[id].state = state;
    g_health[id].last_status = status;
    g_health[id].updated_ts_ns = health_now_ns();
}

const kernel_subsystem_health_t *kernel_health_get(kernel_subsystem_id_t id) {
    if (!kernel_subsystem_id_valid((uint32_t)id)) {
        return NULL;
    }
    return &g_health[id];
}

void kernel_health_get_summary(kernel_health_summary_t *out) {
    if (!out) {
        return;
    }

    out->level = KERNEL_STABILITY_STABLE;
    out->ok_count = 0;
    out->degraded_count = 0;
    out->failed_count = 0;
    out->unknown_count = 0;
    out->required_failures = 0;
    out->io_degraded = 0;
    out->autonomy_allowed = true;
    out->risky_io_allowed = true;

    for (uint32_t i = 0; i < KERNEL_SUBSYSTEM_COUNT; i++) {
        const kernel_subsystem_health_t *entry = &g_health[i];

        switch (entry->state) {
            case KERNEL_HEALTH_OK:
                out->ok_count++;
                break;
            case KERNEL_HEALTH_DEGRADED:
                out->degraded_count++;
                if (entry->io_path) {
                    out->io_degraded++;
                }
                break;
            case KERNEL_HEALTH_FAILED:
                out->failed_count++;
                if (entry->required) {
                    out->required_failures++;
                }
                if (entry->io_path) {
                    out->io_degraded++;
                }
                break;
            case KERNEL_HEALTH_UNKNOWN:
            default:
                out->unknown_count++;
                break;
        }
    }

    if (out->required_failures > 0) {
        out->level = KERNEL_STABILITY_UNSAFE;
    } else if (out->failed_count > 0 || out->degraded_count > 0) {
        out->level = KERNEL_STABILITY_DEGRADED;
    }

    out->autonomy_allowed = (out->level == KERNEL_STABILITY_STABLE);
    out->risky_io_allowed = (out->level == KERNEL_STABILITY_STABLE &&
                             out->io_degraded == 0);
}

bool kernel_health_allows_autonomy(void) {
    kernel_health_summary_t summary;
    kernel_health_get_summary(&summary);
    return summary.autonomy_allowed;
}

bool kernel_health_allows_risky_io(void) {
    kernel_health_summary_t summary;
    kernel_health_get_summary(&summary);
    return summary.risky_io_allowed;
}

const char *kernel_health_state_name(kernel_health_state_t state) {
    switch (state) {
        case KERNEL_HEALTH_OK:       return "ok";
        case KERNEL_HEALTH_DEGRADED: return "degraded";
        case KERNEL_HEALTH_FAILED:   return "failed";
        case KERNEL_HEALTH_UNKNOWN:
        default:                     return "unknown";
    }
}

const char *kernel_stability_name(kernel_stability_t level) {
    switch (level) {
        case KERNEL_STABILITY_STABLE:   return "stable";
        case KERNEL_STABILITY_DEGRADED: return "degraded";
        case KERNEL_STABILITY_UNSAFE:
        default:                        return "unsafe";
    }
}

void kernel_health_dump(void) {
    kernel_health_summary_t summary;
    kernel_health_get_summary(&summary);

    kprintf("\n=== Kernel Health ===\n");
    kprintf("Stability: %s\n",
        (uint64_t)(uintptr_t)kernel_stability_name(summary.level));
    kprintf("Counts: ok=%u degraded=%u failed=%u unknown=%u required_failures=%u\n",
        (uint64_t)summary.ok_count,
        (uint64_t)summary.degraded_count,
        (uint64_t)summary.failed_count,
        (uint64_t)summary.unknown_count,
        (uint64_t)summary.required_failures);
    kprintf("Policy: autonomy=%u risky_io=%u io_degraded=%u\n",
        (uint64_t)summary.autonomy_allowed,
        (uint64_t)summary.risky_io_allowed,
        (uint64_t)summary.io_degraded);

    for (uint32_t i = 0; i < KERNEL_SUBSYSTEM_COUNT; i++) {
        const kernel_subsystem_health_t *entry = &g_health[i];
        kprintf("  [%u] %s state=%s required=%u status=%d\n",
            (uint64_t)i,
            (uint64_t)(uintptr_t)entry->name,
            (uint64_t)(uintptr_t)kernel_health_state_name(entry->state),
            (uint64_t)entry->required,
            (int64_t)entry->last_status);
    }
    kprintf("=====================\n");

    serial_printf("[HEALTH] stability=%s ok=%u degraded=%u failed=%u unknown=%u req_fail=%u autonomy=%u risky_io=%u\n",
        (uint64_t)(uintptr_t)kernel_stability_name(summary.level),
        (uint64_t)summary.ok_count,
        (uint64_t)summary.degraded_count,
        (uint64_t)summary.failed_count,
        (uint64_t)summary.unknown_count,
        (uint64_t)summary.required_failures,
        (uint64_t)summary.autonomy_allowed,
        (uint64_t)summary.risky_io_allowed);
}
