/*
 * AIOS Kernel - Memory Fabric Foundation
 * AI-Native Operating System
 *
 * This layer sits above Tensor MM and below higher-level AI runtimes.
 * It tracks agent memory domains, shared tensor windows, and zero-copy
 * recommendations so future ring3 runtimes can coordinate multi-agent
 * execution with lower copy and DMA overhead.
 */

#ifndef _AIOS_MEMORY_FABRIC_H
#define _AIOS_MEMORY_FABRIC_H

#include <kernel/types.h>
#include <mm/tensor_mm.h>

#define MEMORY_FABRIC_MAX_DOMAINS 16
#define MEMORY_FABRIC_MAX_WINDOWS 64

typedef enum {
    MEMORY_FABRIC_TOPOLOGY_UNIFORM = 0,
    MEMORY_FABRIC_TOPOLOGY_SEGMENTED = 1,
    MEMORY_FABRIC_TOPOLOGY_NUMA_HINTED = 2,
    MEMORY_FABRIC_TOPOLOGY_FABRIC_EXPANDABLE = 3,
} memory_fabric_topology_t;

typedef enum {
    MEMORY_DOMAIN_ROLE_NONE = 0,
    MEMORY_DOMAIN_ROLE_MAIN = 1,
    MEMORY_DOMAIN_ROLE_WORKER = 2,
    MEMORY_DOMAIN_ROLE_MEMORY = 3,
    MEMORY_DOMAIN_ROLE_DEVICE = 4,
} memory_domain_role_t;

typedef enum {
    MEMORY_FABRIC_ACCESS_PRIVATE = 0,
    MEMORY_FABRIC_ACCESS_SHARED_RO = 1,
    MEMORY_FABRIC_ACCESS_SHARED_RW = 2,
    MEMORY_FABRIC_ACCESS_DMA_STREAM = 3,
} memory_fabric_access_t;

typedef struct {
    bool acpi_ready;
    bool ecam_available;
    bool invariant_tsc;
    bool pcie_present;
    bool numa_hint_ready;
    bool fabric_expandable;
    bool zero_copy_preferred;
    memory_fabric_topology_t topology;
    uint8_t compatibility_score;
    uint8_t locality_score;
    uint16_t recommended_agent_slots;
    uint16_t recommended_worker_queue_depth;
    uint32_t recommended_hotset_kib;
    uint32_t recommended_staging_kib;
    uint32_t recommended_zero_copy_window_kib;
    uint32_t shared_window_cap;
} memory_fabric_profile_t;

typedef struct {
    uint32_t domain_id;
    memory_domain_role_t role;
    bool active;
    bool realtime;
    bool zero_copy_preferred;
    uint8_t priority;
    uint16_t max_inflight_ops;
    uint32_t local_budget_kib;
    uint16_t attached_windows;
    uint64_t last_activity_ns;
} memory_domain_t;

typedef struct {
    uint32_t window_id;
    tensor_id_t tensor_id;
    uint32_t owner_domain_id;
    mem_region_type_t region;
    memory_fabric_access_t access;
    uint64_t size_bytes;
    uint32_t reader_mask;
    uint32_t writer_mask;
    bool pinned;
    bool dma_capable;
    bool active;
    uint16_t map_count;
    uint64_t last_access_ns;
} memory_shared_window_t;

aios_status_t memory_fabric_init(void);
const memory_fabric_profile_t *memory_fabric_profile(void);
uint32_t memory_fabric_domain_count(void);
uint32_t memory_fabric_window_count(void);
aios_status_t memory_fabric_domain_open(memory_domain_role_t role, uint8_t priority,
                                        bool realtime, bool zero_copy_preferred,
                                        uint32_t budget_kib, uint32_t *domain_id_out);
aios_status_t memory_fabric_domain_get(uint32_t domain_id, memory_domain_t *out);
aios_status_t memory_fabric_window_share(uint32_t owner_domain_id, tensor_id_t tensor_id,
                                         uint32_t reader_mask, uint32_t writer_mask,
                                         memory_fabric_access_t access,
                                         uint32_t *window_id_out);
aios_status_t memory_fabric_window_attach(uint32_t window_id, uint32_t domain_id,
                                          bool write_access);
aios_status_t memory_fabric_window_get(uint32_t window_id, memory_shared_window_t *out);
aios_status_t memory_fabric_window_release(uint32_t window_id);
const char *memory_fabric_topology_name(memory_fabric_topology_t topology);
const char *memory_domain_role_name(memory_domain_role_t role);
const char *memory_fabric_access_name(memory_fabric_access_t access);
void memory_fabric_dump(void);

#endif /* _AIOS_MEMORY_FABRIC_H */
