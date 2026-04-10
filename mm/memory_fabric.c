/*
 * AIOS Kernel - Memory Fabric Foundation
 * AI-Native Operating System
 */

#include <mm/memory_fabric.h>
#include <kernel/acpi.h>
#include <kernel/selftest.h>
#include <kernel/time.h>
#include <drivers/pci_core.h>
#include <drivers/platform_probe.h>
#include <drivers/serial.h>
#include <drivers/vga.h>
#include <lib/string.h>

static memory_fabric_profile_t g_profile = {0};
static memory_domain_t g_domains[MEMORY_FABRIC_MAX_DOMAINS];
static memory_shared_window_t g_windows[MEMORY_FABRIC_MAX_WINDOWS];
static uint32_t g_domain_count = 0;
static uint32_t g_window_count = 0;
static uint32_t g_next_domain_id = 1;
static uint32_t g_next_window_id = 1;

static uint8_t clamp_u8_value(int32_t value, uint8_t min_value, uint8_t max_value) {
    if (value < (int32_t)min_value) {
        return min_value;
    }
    if (value > (int32_t)max_value) {
        return max_value;
    }
    return (uint8_t)value;
}

static uint16_t clamp_u16_value(int32_t value, uint16_t min_value, uint16_t max_value) {
    if (value < (int32_t)min_value) {
        return min_value;
    }
    if (value > (int32_t)max_value) {
        return max_value;
    }
    return (uint16_t)value;
}

static uint32_t clamp_u32_value(int64_t value, uint32_t min_value, uint32_t max_value) {
    if (value < (int64_t)min_value) {
        return min_value;
    }
    if (value > (int64_t)max_value) {
        return max_value;
    }
    return (uint32_t)value;
}

static uint32_t domain_bit(uint32_t domain_id) {
    if (domain_id == 0 || domain_id > 32) {
        return 0;
    }
    return (uint32_t)BIT(domain_id - 1);
}

static memory_domain_t *find_domain(uint32_t domain_id) {
    for (uint32_t i = 0; i < g_domain_count; i++) {
        if (g_domains[i].active && g_domains[i].domain_id == domain_id) {
            return &g_domains[i];
        }
    }
    return NULL;
}

static memory_shared_window_t *find_window(uint32_t window_id) {
    for (uint32_t i = 0; i < g_window_count; i++) {
        if (g_windows[i].active && g_windows[i].window_id == window_id) {
            return &g_windows[i];
        }
    }
    return NULL;
}

static memory_shared_window_t *alloc_window_slot(void) {
    for (uint32_t i = 0; i < g_window_count; i++) {
        if (!g_windows[i].active) {
            return &g_windows[i];
        }
    }
    if (g_window_count >= MEMORY_FABRIC_MAX_WINDOWS) {
        return NULL;
    }
    return &g_windows[g_window_count++];
}

static uint8_t count_active_pcie_io_devices(void) {
    uint8_t count = 0;
    for (uint32_t i = 0; i < platform_probe_count(); i++) {
        const platform_device_t *dev = platform_probe_get(i);
        if (!dev || !dev->pcie_capable) {
            continue;
        }
        if (dev->kind == PLATFORM_DEVICE_ETHERNET ||
            dev->kind == PLATFORM_DEVICE_USB ||
            dev->kind == PLATFORM_DEVICE_STORAGE ||
            dev->kind == PLATFORM_DEVICE_WIRELESS ||
            dev->kind == PLATFORM_DEVICE_BLUETOOTH) {
            count++;
        }
    }
    return count;
}

static void compute_profile(void) {
    const acpi_info_t *acpi = acpi_info();
    const pci_core_summary_t *pci = pci_core_summary();
    const platform_probe_summary_t *probe = platform_probe_summary();
    const memory_selftest_result_t *selftest = kernel_memory_selftest_last();
    uint8_t io_pcie = count_active_pcie_io_devices();
    int32_t compatibility = 35;
    int32_t locality = 40;

    memset(&g_profile, 0, sizeof(g_profile));

    g_profile.acpi_ready = acpi_ready();
    g_profile.ecam_available = pci->ecam_available;
    g_profile.invariant_tsc = kernel_time_invariant_tsc();
    g_profile.pcie_present = pci->pcie_functions > 0;
    g_profile.numa_hint_ready = false;   /* SRAT/SLIT/HMAT parsing is not wired yet. */
    g_profile.fabric_expandable = false; /* CXL/CDAT is future work. */
    g_profile.topology = MEMORY_FABRIC_TOPOLOGY_UNIFORM;

    if (g_profile.pcie_present || probe->matched_devices > 0 || pci->bridges > 0) {
        g_profile.topology = MEMORY_FABRIC_TOPOLOGY_SEGMENTED;
    }
    if (g_profile.numa_hint_ready) {
        g_profile.topology = MEMORY_FABRIC_TOPOLOGY_NUMA_HINTED;
    }
    if (g_profile.fabric_expandable) {
        g_profile.topology = MEMORY_FABRIC_TOPOLOGY_FABRIC_EXPANDABLE;
    }

    if (g_profile.acpi_ready) {
        compatibility += 20;
    }
    if (acpi->xsdt_present) {
        compatibility += 10;
    }
    if (acpi->mcfg_present) {
        compatibility += 10;
    }
    if (pci->ecam_available) {
        compatibility += 10;
    }
    if (g_profile.invariant_tsc) {
        compatibility += 5;
        locality += 10;
    }
    if (io_pcie > 0) {
        locality += 10;
    }
    if (selftest->tier == BOOT_PERF_TIER_HIGH) {
        locality += 20;
    } else if (selftest->tier == BOOT_PERF_TIER_MID) {
        locality += 10;
    } else {
        locality -= 5;
    }
    if (!pci->ecam_available && pci->total_functions > 4) {
        compatibility -= 10;
    }

    g_profile.compatibility_score = clamp_u8_value(compatibility, 0, 100);
    g_profile.locality_score = clamp_u8_value(locality, 0, 100);

    switch (selftest->tier) {
        case BOOT_PERF_TIER_HIGH:
            g_profile.recommended_agent_slots = 12;
            g_profile.recommended_worker_queue_depth = 16;
            g_profile.recommended_hotset_kib = 8192;
            g_profile.recommended_staging_kib = io_pcie ? 2048 : 1024;
            g_profile.recommended_zero_copy_window_kib = 2048;
            break;
        case BOOT_PERF_TIER_MID:
            g_profile.recommended_agent_slots = 8;
            g_profile.recommended_worker_queue_depth = 8;
            g_profile.recommended_hotset_kib = 4096;
            g_profile.recommended_staging_kib = io_pcie ? 1024 : 512;
            g_profile.recommended_zero_copy_window_kib = 1024;
            break;
        case BOOT_PERF_TIER_LOW:
        default:
            g_profile.recommended_agent_slots = 4;
            g_profile.recommended_worker_queue_depth = 4;
            g_profile.recommended_hotset_kib = 1024;
            g_profile.recommended_staging_kib = io_pcie ? 512 : 256;
            g_profile.recommended_zero_copy_window_kib = 256;
            break;
    }

    if (!g_profile.ecam_available && io_pcie > 0) {
        g_profile.recommended_staging_kib =
            clamp_u32_value((int64_t)g_profile.recommended_staging_kib + 256, 256, 4096);
    }

    g_profile.shared_window_cap = MEMORY_FABRIC_MAX_WINDOWS;
    g_profile.zero_copy_preferred = (g_profile.locality_score >= 55 &&
                                     g_profile.compatibility_score >= 55);
}

static aios_status_t domain_open_internal(memory_domain_role_t role, uint8_t priority,
                                          bool realtime, bool zero_copy_preferred,
                                          uint32_t budget_kib, uint32_t *domain_id_out) {
    if (g_domain_count >= MEMORY_FABRIC_MAX_DOMAINS) {
        return AIOS_ERR_BUSY;
    }

    memory_domain_t *slot = &g_domains[g_domain_count++];
    memset(slot, 0, sizeof(*slot));
    slot->domain_id = g_next_domain_id++;
    slot->role = role;
    slot->active = true;
    slot->realtime = realtime;
    slot->zero_copy_preferred = zero_copy_preferred;
    slot->priority = priority;
    slot->max_inflight_ops = clamp_u16_value(
        (int32_t)g_profile.recommended_worker_queue_depth +
        ((role == MEMORY_DOMAIN_ROLE_MAIN) ? 4 : 0) +
        (realtime ? 4 : 0), 2, 64);
    slot->local_budget_kib = budget_kib;
    slot->attached_windows = 0;
    slot->last_activity_ns = kernel_time_monotonic_ns();

    if (domain_id_out) {
        *domain_id_out = slot->domain_id;
    }
    return AIOS_OK;
}

static void seed_default_domains(void) {
    uint32_t workers;
    uint32_t domain_id = 0;
    uint32_t main_budget = MAX(1024, g_profile.recommended_hotset_kib / 2);
    uint32_t memory_budget = MAX(1024, g_profile.recommended_hotset_kib / 2);
    uint32_t device_budget = MAX(512, g_profile.recommended_staging_kib);

    g_domain_count = 0;
    g_next_domain_id = 1;

    (void)domain_open_internal(MEMORY_DOMAIN_ROLE_MAIN, 0, true,
        g_profile.zero_copy_preferred, main_budget, &domain_id);
    (void)domain_open_internal(MEMORY_DOMAIN_ROLE_MEMORY, 24, false, true,
        memory_budget, &domain_id);
    (void)domain_open_internal(MEMORY_DOMAIN_ROLE_DEVICE, 32, false,
        g_profile.pcie_present, device_budget, &domain_id);

    workers = (g_profile.recommended_agent_slots > 3)
        ? (g_profile.recommended_agent_slots - 3)
        : 1;
    if (workers > 4) {
        workers = 4;
    }

    for (uint32_t i = 0; i < workers; i++) {
        uint32_t worker_budget = MAX(512, g_profile.recommended_hotset_kib / (workers + 1));
        (void)domain_open_internal(MEMORY_DOMAIN_ROLE_WORKER,
            (uint8_t)(64 + (i * 8)),
            false,
            g_profile.zero_copy_preferred,
            worker_budget,
            &domain_id);
    }
}

static void update_window_accounting(uint32_t domain_mask, int32_t delta) {
    for (uint32_t i = 0; i < g_domain_count; i++) {
        uint32_t bit = domain_bit(g_domains[i].domain_id);
        if ((domain_mask & bit) == 0) {
            continue;
        }
        if (delta > 0) {
            g_domains[i].attached_windows =
                clamp_u16_value((int32_t)g_domains[i].attached_windows + delta, 0, 0xFFFF);
        } else if (g_domains[i].attached_windows > 0) {
            g_domains[i].attached_windows--;
        }
    }
}

aios_status_t memory_fabric_init(void) {
    memset(g_domains, 0, sizeof(g_domains));
    memset(g_windows, 0, sizeof(g_windows));
    g_window_count = 0;
    g_next_window_id = 1;

    compute_profile();
    seed_default_domains();

    kprintf("\n");
    kprintf("    Memory Fabric initialized:\n");
    kprintf("    Topology: %s\n", (uint64_t)(uintptr_t)memory_fabric_topology_name(g_profile.topology));
    kprintf("    Agent slots: %u | worker_qd: %u | hotset: %u KiB | zero_copy: %u\n",
        (uint64_t)g_profile.recommended_agent_slots,
        (uint64_t)g_profile.recommended_worker_queue_depth,
        (uint64_t)g_profile.recommended_hotset_kib,
        (uint64_t)g_profile.zero_copy_preferred);

    serial_printf("[FABRIC] topology=%s compat=%u locality=%u agents=%u worker_qd=%u hotset=%uKiB staging=%uKiB zero_copy=%u\n",
        (uint64_t)(uintptr_t)memory_fabric_topology_name(g_profile.topology),
        (uint64_t)g_profile.compatibility_score,
        (uint64_t)g_profile.locality_score,
        (uint64_t)g_domain_count,
        (uint64_t)g_profile.recommended_worker_queue_depth,
        (uint64_t)g_profile.recommended_hotset_kib,
        (uint64_t)g_profile.recommended_staging_kib,
        (uint64_t)g_profile.zero_copy_preferred);

    return AIOS_OK;
}

const memory_fabric_profile_t *memory_fabric_profile(void) {
    return &g_profile;
}

uint32_t memory_fabric_domain_count(void) {
    return g_domain_count;
}

uint32_t memory_fabric_window_count(void) {
    uint32_t count = 0;
    for (uint32_t i = 0; i < g_window_count; i++) {
        if (g_windows[i].active) {
            count++;
        }
    }
    return count;
}

aios_status_t memory_fabric_domain_open(memory_domain_role_t role, uint8_t priority,
                                        bool realtime, bool zero_copy_preferred,
                                        uint32_t budget_kib, uint32_t *domain_id_out) {
    return domain_open_internal(role, priority, realtime, zero_copy_preferred,
        budget_kib, domain_id_out);
}

aios_status_t memory_fabric_domain_get(uint32_t domain_id, memory_domain_t *out) {
    memory_domain_t *domain = find_domain(domain_id);
    if (!domain || !out) {
        return AIOS_ERR_INVAL;
    }
    *out = *domain;
    return AIOS_OK;
}

aios_status_t memory_fabric_window_share(uint32_t owner_domain_id, tensor_id_t tensor_id,
                                         uint32_t reader_mask, uint32_t writer_mask,
                                         memory_fabric_access_t access,
                                         uint32_t *window_id_out) {
    tensor_alloc_t tensor;
    memory_shared_window_t *window;
    memory_domain_t *owner = find_domain(owner_domain_id);
    uint32_t owner_bit = domain_bit(owner_domain_id);
    uint32_t share_mask;
    aios_status_t status;

    if (!owner || tensor_id == 0) {
        return AIOS_ERR_INVAL;
    }
    status = tensor_info(tensor_id, &tensor);
    if (status != AIOS_OK) {
        return status;
    }
    status = tensor_ref(tensor_id);
    if (status != AIOS_OK) {
        return status;
    }

    window = alloc_window_slot();
    if (!window) {
        (void)tensor_unref(tensor_id);
        return AIOS_ERR_BUSY;
    }
    memset(window, 0, sizeof(*window));
    window->window_id = g_next_window_id++;
    window->tensor_id = tensor_id;
    window->owner_domain_id = owner_domain_id;
    window->region = tensor.region;
    window->access = access;
    window->size_bytes = tensor.size;
    window->reader_mask = reader_mask | owner_bit | writer_mask;
    window->writer_mask = writer_mask | owner_bit;
    window->pinned = tensor.pinned;
    window->dma_capable = tensor.dma_capable;
    window->active = true;
    window->map_count = 1;
    window->last_access_ns = kernel_time_monotonic_ns();

    share_mask = window->reader_mask | window->writer_mask;
    update_window_accounting(share_mask, 1);
    owner->last_activity_ns = window->last_access_ns;

    if (window_id_out) {
        *window_id_out = window->window_id;
    }

    return AIOS_OK;
}

aios_status_t memory_fabric_window_attach(uint32_t window_id, uint32_t domain_id,
                                          bool write_access) {
    memory_shared_window_t *window = find_window(window_id);
    memory_domain_t *domain = find_domain(domain_id);
    uint32_t mask = domain_bit(domain_id);

    if (!window || !domain || mask == 0) {
        return AIOS_ERR_INVAL;
    }
    if ((window->reader_mask & mask) == 0) {
        return AIOS_ERR_PERM;
    }
    if (write_access && (window->writer_mask & mask) == 0) {
        return AIOS_ERR_PERM;
    }

    window->map_count = clamp_u16_value((int32_t)window->map_count + 1, 1, 0xFFFF);
    window->last_access_ns = kernel_time_monotonic_ns();
    domain->last_activity_ns = window->last_access_ns;
    return AIOS_OK;
}

aios_status_t memory_fabric_window_get(uint32_t window_id, memory_shared_window_t *out) {
    memory_shared_window_t *window = find_window(window_id);
    if (!window || !out) {
        return AIOS_ERR_INVAL;
    }
    *out = *window;
    return AIOS_OK;
}

aios_status_t memory_fabric_window_release(uint32_t window_id) {
    memory_shared_window_t *window = find_window(window_id);
    uint32_t share_mask;
    aios_status_t status;

    if (!window) {
        return AIOS_ERR_INVAL;
    }

    share_mask = window->reader_mask | window->writer_mask;
    update_window_accounting(share_mask, -1);

    status = tensor_unref(window->tensor_id);
    window->active = false;
    window->reader_mask = 0;
    window->writer_mask = 0;
    window->map_count = 0;
    return status;
}

const char *memory_fabric_topology_name(memory_fabric_topology_t topology) {
    switch (topology) {
        case MEMORY_FABRIC_TOPOLOGY_UNIFORM:          return "uniform";
        case MEMORY_FABRIC_TOPOLOGY_SEGMENTED:        return "segmented";
        case MEMORY_FABRIC_TOPOLOGY_NUMA_HINTED:      return "numa-hinted";
        case MEMORY_FABRIC_TOPOLOGY_FABRIC_EXPANDABLE:return "fabric-expandable";
        default:                                      return "unknown";
    }
}

const char *memory_domain_role_name(memory_domain_role_t role) {
    switch (role) {
        case MEMORY_DOMAIN_ROLE_MAIN:   return "main";
        case MEMORY_DOMAIN_ROLE_WORKER: return "worker";
        case MEMORY_DOMAIN_ROLE_MEMORY: return "memory";
        case MEMORY_DOMAIN_ROLE_DEVICE: return "device";
        case MEMORY_DOMAIN_ROLE_NONE:
        default:                        return "none";
    }
}

const char *memory_fabric_access_name(memory_fabric_access_t access) {
    switch (access) {
        case MEMORY_FABRIC_ACCESS_PRIVATE:   return "private";
        case MEMORY_FABRIC_ACCESS_SHARED_RO: return "shared-ro";
        case MEMORY_FABRIC_ACCESS_SHARED_RW: return "shared-rw";
        case MEMORY_FABRIC_ACCESS_DMA_STREAM:return "dma-stream";
        default:                             return "unknown";
    }
}

void memory_fabric_dump(void) {
    kprintf("\n=== Memory Fabric ===\n");
    kprintf("Topology=%s compat=%u locality=%u acpi=%u ecam=%u pcie=%u invariant_tsc=%u\n",
        (uint64_t)(uintptr_t)memory_fabric_topology_name(g_profile.topology),
        (uint64_t)g_profile.compatibility_score,
        (uint64_t)g_profile.locality_score,
        (uint64_t)g_profile.acpi_ready,
        (uint64_t)g_profile.ecam_available,
        (uint64_t)g_profile.pcie_present,
        (uint64_t)g_profile.invariant_tsc);
    kprintf("Plan: slots=%u worker_qd=%u hotset=%uKiB staging=%uKiB zero_copy_window=%uKiB zero_copy=%u\n",
        (uint64_t)g_profile.recommended_agent_slots,
        (uint64_t)g_profile.recommended_worker_queue_depth,
        (uint64_t)g_profile.recommended_hotset_kib,
        (uint64_t)g_profile.recommended_staging_kib,
        (uint64_t)g_profile.recommended_zero_copy_window_kib,
        (uint64_t)g_profile.zero_copy_preferred);
    for (uint32_t i = 0; i < g_domain_count; i++) {
        const memory_domain_t *domain = &g_domains[i];
        if (!domain->active) {
            continue;
        }
        kprintf("  domain[%u] role=%s prio=%u realtime=%u zero_copy=%u budget=%uKiB inflight=%u windows=%u\n",
            (uint64_t)domain->domain_id,
            (uint64_t)(uintptr_t)memory_domain_role_name(domain->role),
            (uint64_t)domain->priority,
            (uint64_t)domain->realtime,
            (uint64_t)domain->zero_copy_preferred,
            (uint64_t)domain->local_budget_kib,
            (uint64_t)domain->max_inflight_ops,
            (uint64_t)domain->attached_windows);
    }
    for (uint32_t i = 0; i < g_window_count; i++) {
        const memory_shared_window_t *window = &g_windows[i];
        if (!window->active) {
            continue;
        }
        kprintf("  window[%u] tensor=%u owner=%u access=%s size=%uKiB maps=%u readers=%x writers=%x dma=%u pinned=%u\n",
            (uint64_t)window->window_id,
            (uint64_t)window->tensor_id,
            (uint64_t)window->owner_domain_id,
            (uint64_t)(uintptr_t)memory_fabric_access_name(window->access),
            (uint64_t)(window->size_bytes / KB(1)),
            (uint64_t)window->map_count,
            (uint64_t)window->reader_mask,
            (uint64_t)window->writer_mask,
            (uint64_t)window->dma_capable,
            (uint64_t)window->pinned);
    }
    kprintf("=====================\n");
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
