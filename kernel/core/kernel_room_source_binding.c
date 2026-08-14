/*
 * AIOS Kernel - Kernel Room K2-a Native Source Binding
 *
 * CURRENT scope: bind the immutable K1 AI_SERVICE Node 101 to the exact-one
 * active, persistent SLM agent-tree MAIN source. The source owns its own
 * boot-local instance and lifecycle generation. No value in this contract is
 * an authorization, scheduling, allocation, reconciliation, or apply edge.
 */

#include <kernel/kernel_room_source_binding.h>
#include <kernel/kernel_room_management.h>
#include <runtime/slm_orchestrator.h>
#include <drivers/serial.h>
#include <drivers/vga.h>
#include <lib/string.h>

static kernel_room_source_binding_snapshot_t g_binding_registry;
static bool g_binding_ready = false;
static uint64_t g_binding_sample_sequence = 0;

static bool bytes_zero(const void *base, size_t size) {
    const uint8_t *bytes = (const uint8_t *)base;

    for (size_t i = 0; i < size; i++) {
        if (bytes[i] != 0U) {
            return false;
        }
    }
    return true;
}

static const kernel_room_cell_record_t *find_cell(
    const kernel_room_management_snapshot_t *management,
    uint32_t cell_id
) {
    for (uint32_t i = 0; i < management->cell_count; i++) {
        if (management->cells[i].cell_id == cell_id) {
            return &management->cells[i];
        }
    }
    return NULL;
}

static const kernel_room_node_record_t *find_node(
    const kernel_room_management_snapshot_t *management,
    uint32_t node_id
) {
    for (uint32_t i = 0; i < management->node_count; i++) {
        if (management->nodes[i].node_id == node_id) {
            return &management->nodes[i];
        }
    }
    return NULL;
}

static bool map_source_namespace(uint32_t source_namespace, uint32_t *out) {
    if (!out) {
        return false;
    }
    switch (source_namespace) {
        case SLM_AGENT_SOURCE_NAMESPACE_AGENT_TREE:
            *out = KERNEL_ROOM_BINDING_SOURCE_NAMESPACE_NATIVE_SLM_AGENT_TREE;
            return true;
        default:
            return false;
    }
}

static bool map_source_kind(uint32_t source_kind, uint32_t *out) {
    if (!out) {
        return false;
    }
    switch (source_kind) {
        case SLM_AGENT_SOURCE_KIND_AI_SERVICE:
            *out = KERNEL_ROOM_BINDING_SOURCE_KIND_AI_SERVICE;
            return true;
        default:
            return false;
    }
}

static bool map_source_kind_to_canonical(uint32_t source_kind, uint32_t *out) {
    if (!out) {
        return false;
    }
    switch (source_kind) {
        case SLM_AGENT_SOURCE_KIND_AI_SERVICE:
            *out = KERNEL_ROOM_NODE_KIND_AI_SERVICE;
            return true;
        default:
            return false;
    }
}

static bool map_source_role(uint32_t source_role, uint32_t *out) {
    if (!out) {
        return false;
    }
    switch (source_role) {
        case SLM_AGENT_SOURCE_ROLE_MAIN:
            *out = KERNEL_ROOM_BINDING_SOURCE_ROLE_MAIN;
            return true;
        default:
            return false;
    }
}

static bool map_source_lifecycle(uint32_t lifecycle_state, uint32_t *out) {
    if (!out) {
        return false;
    }
    switch (lifecycle_state) {
        case SLM_AGENT_SOURCE_LIFECYCLE_ACTIVE:
            *out = KERNEL_ROOM_BINDING_SOURCE_LIFECYCLE_ACTIVE;
            return true;
        default:
            return false;
    }
}

static kernel_room_binding_reject_reason_t validate_with_sources(
    const kernel_room_source_binding_snapshot_t *snapshot,
    const kernel_room_management_snapshot_t *management,
    const slm_agent_source_snapshot_t *source,
    const kernel_room_source_binding_snapshot_t *previous
) {
    uint32_t mapped_namespace;
    uint32_t mapped_kind;
    uint32_t mapped_canonical_kind;
    uint32_t mapped_role;
    uint32_t mapped_lifecycle;

    if (!snapshot || !management || !source) {
        return KERNEL_ROOM_BINDING_REJECT_INIT_ORDER;
    }
    if (source->ready != 1U) {
        return KERNEL_ROOM_BINDING_REJECT_MISSING;
    }
    if (!kernel_room_management_snapshot_valid(management) ||
        !slm_agent_source_snapshot_valid(source)) {
        return KERNEL_ROOM_BINDING_REJECT_MALFORMED;
    }
    if (snapshot->schema_version !=
            KERNEL_ROOM_SOURCE_BINDING_SCHEMA_VERSION ||
        snapshot->struct_size != sizeof(*snapshot)) {
        return KERNEL_ROOM_BINDING_REJECT_SCHEMA;
    }
    if (snapshot->binding_count == 0U) {
        return KERNEL_ROOM_BINDING_REJECT_MISSING;
    }
    if (snapshot->binding_count > KERNEL_ROOM_SOURCE_BINDING_CAPACITY ||
        snapshot->binding_capacity != KERNEL_ROOM_SOURCE_BINDING_CAPACITY) {
        return KERNEL_ROOM_BINDING_REJECT_OVERFLOW;
    }
    if (snapshot->binding_generation == 0U) {
        return KERNEL_ROOM_BINDING_REJECT_ZERO_GENERATION;
    }
    if (snapshot->observation_only != 1U ||
        snapshot->management_only != 1U ||
        snapshot->ready != 1U ||
        snapshot->last_reject_reason != KERNEL_ROOM_BINDING_REJECT_NONE ||
        snapshot->source_valid != 1U ||
        snapshot->generation_valid != 1U ||
        snapshot->binding_valid != 1U ||
        snapshot->reserved0 != 0U ||
        snapshot->sample_sequence == 0U) {
        return KERNEL_ROOM_BINDING_REJECT_MALFORMED;
    }
    if (!bytes_zero(&snapshot->bindings[snapshot->binding_count],
            (KERNEL_ROOM_SOURCE_BINDING_CAPACITY - snapshot->binding_count) *
                sizeof(snapshot->bindings[0])) ||
        !bytes_zero(snapshot->reserved_tail, sizeof(snapshot->reserved_tail))) {
        return KERNEL_ROOM_BINDING_REJECT_TAIL;
    }
    if (!map_source_namespace(source->source_namespace, &mapped_namespace) ||
        !map_source_kind(source->source_kind, &mapped_kind) ||
        !map_source_kind_to_canonical(source->source_kind,
            &mapped_canonical_kind) ||
        !map_source_role(source->source_role, &mapped_role) ||
        !map_source_lifecycle(source->lifecycle_state, &mapped_lifecycle)) {
        return KERNEL_ROOM_BINDING_REJECT_MALFORMED;
    }

    for (uint32_t i = 0; i < snapshot->binding_count; i++) {
        const kernel_room_source_binding_record_t *binding =
            &snapshot->bindings[i];
        const kernel_room_node_record_t *node;
        const kernel_room_cell_record_t *parent;

        if (binding->schema_version !=
                KERNEL_ROOM_SOURCE_BINDING_SCHEMA_VERSION ||
            binding->struct_size != sizeof(*binding)) {
            return KERNEL_ROOM_BINDING_REJECT_SCHEMA;
        }
        if (binding->canonical_generation == 0U ||
            binding->parent_generation == 0U ||
            binding->source_generation == 0U) {
            return KERNEL_ROOM_BINDING_REJECT_ZERO_GENERATION;
        }
        if (binding->source_instance == 0U) {
            return KERNEL_ROOM_BINDING_REJECT_INSTANCE;
        }
        if (binding->valid_flags != KERNEL_ROOM_BINDING_ALL_VALID_FLAGS ||
            binding->lifecycle_state != mapped_lifecycle) {
            return KERNEL_ROOM_BINDING_REJECT_MALFORMED;
        }
        if (binding->canonical_namespace != KERNEL_ROOM_NAMESPACE_NODE ||
            binding->source_namespace != mapped_namespace) {
            return KERNEL_ROOM_BINDING_REJECT_NAMESPACE;
        }

        node = find_node(management, binding->canonical_id);
        if (!node) {
            return KERNEL_ROOM_BINDING_REJECT_ORPHAN;
        }
        parent = find_cell(management, binding->parent_cell_id);
        if (!parent || node->parent_cell_id != parent->cell_id) {
            return KERNEL_ROOM_BINDING_REJECT_ORPHAN;
        }
        if (binding->canonical_kind != mapped_canonical_kind ||
            node->kind != mapped_canonical_kind ||
            binding->source_kind != mapped_kind) {
            return KERNEL_ROOM_BINDING_REJECT_KIND;
        }
        if (binding->source_role != mapped_role) {
            return KERNEL_ROOM_BINDING_REJECT_ROLE;
        }
        if (binding->source_id != source->source_id) {
            return KERNEL_ROOM_BINDING_REJECT_ORPHAN;
        }
        if (binding->source_instance != source->source_instance) {
            return KERNEL_ROOM_BINDING_REJECT_INSTANCE;
        }
        if (binding->canonical_generation < node->generation ||
            binding->parent_generation < parent->generation ||
            binding->source_generation < source->source_generation) {
            return KERNEL_ROOM_BINDING_REJECT_STALE;
        }
        if (binding->canonical_generation > node->generation ||
            binding->parent_generation > parent->generation ||
            binding->source_generation > source->source_generation) {
            return KERNEL_ROOM_BINDING_REJECT_GENERATION_ROLLBACK;
        }

        for (uint32_t j = 0; j < i; j++) {
            const kernel_room_source_binding_record_t *prior =
                &snapshot->bindings[j];
            if (prior->canonical_namespace == binding->canonical_namespace &&
                prior->canonical_id == binding->canonical_id) {
                return KERNEL_ROOM_BINDING_REJECT_DUPLICATE;
            }
            if (prior->source_namespace == binding->source_namespace &&
                prior->source_id == binding->source_id &&
                prior->source_instance == binding->source_instance) {
                return KERNEL_ROOM_BINDING_REJECT_DUPLICATE;
            }
        }
    }

    if (previous) {
        if (snapshot->binding_generation < previous->binding_generation) {
            return KERNEL_ROOM_BINDING_REJECT_GENERATION_ROLLBACK;
        }
        for (uint32_t i = 0; i < snapshot->binding_count; i++) {
            for (uint32_t j = 0; j < previous->binding_count; j++) {
                if (snapshot->bindings[i].source_namespace ==
                        previous->bindings[j].source_namespace &&
                    snapshot->bindings[i].source_id ==
                        previous->bindings[j].source_id &&
                    snapshot->bindings[i].source_instance ==
                        previous->bindings[j].source_instance &&
                    snapshot->bindings[i].source_generation <
                        previous->bindings[j].source_generation) {
                    return KERNEL_ROOM_BINDING_REJECT_GENERATION_ROLLBACK;
                }
            }
        }
    }

    return KERNEL_ROOM_BINDING_REJECT_NONE;
}

kernel_room_binding_reject_reason_t
kernel_room_source_binding_snapshot_validate(
    const kernel_room_source_binding_snapshot_t *snapshot
) {
    kernel_room_management_snapshot_t management;
    slm_agent_source_snapshot_t source;

    if (!kernel_room_management_ready() ||
        kernel_room_management_snapshot_read(&management) != AIOS_OK ||
        slm_agent_source_snapshot_read(&source) != AIOS_OK) {
        return KERNEL_ROOM_BINDING_REJECT_INIT_ORDER;
    }
    return validate_with_sources(snapshot, &management, &source, NULL);
}

bool kernel_room_source_binding_snapshot_valid(
    const kernel_room_source_binding_snapshot_t *snapshot
) {
    return kernel_room_source_binding_snapshot_validate(snapshot) ==
        KERNEL_ROOM_BINDING_REJECT_NONE;
}

static aios_status_t seed_snapshot(
    kernel_room_source_binding_snapshot_t *snapshot,
    const kernel_room_management_snapshot_t *management,
    const slm_agent_source_snapshot_t *source
) {
    kernel_room_source_binding_record_t *binding;
    const kernel_room_node_record_t *node;
    const kernel_room_cell_record_t *parent;

    if (!snapshot || !management || !source) {
        return AIOS_ERR_INVAL;
    }
    node = find_node(management, KERNEL_ROOM_NODE_ID_MAIN_AI);
    if (!node) {
        return AIOS_ERR_NODEV;
    }
    parent = find_cell(management, node->parent_cell_id);
    if (!parent) {
        return AIOS_ERR_NODEV;
    }

    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->schema_version = KERNEL_ROOM_SOURCE_BINDING_SCHEMA_VERSION;
    snapshot->struct_size = (uint32_t)sizeof(*snapshot);
    snapshot->observation_only = 1U;
    snapshot->management_only = 1U;
    snapshot->ready = 1U;
    snapshot->binding_count = 1U;
    snapshot->binding_capacity = KERNEL_ROOM_SOURCE_BINDING_CAPACITY;
    snapshot->last_reject_reason = KERNEL_ROOM_BINDING_REJECT_NONE;
    snapshot->source_valid = 1U;
    snapshot->generation_valid = 1U;
    snapshot->binding_valid = 1U;
    snapshot->binding_generation = KERNEL_ROOM_SOURCE_BINDING_GENERATION;
    snapshot->sample_sequence = 1U;

    binding = &snapshot->bindings[0];
    binding->schema_version = KERNEL_ROOM_SOURCE_BINDING_SCHEMA_VERSION;
    binding->struct_size = (uint32_t)sizeof(*binding);
    binding->canonical_namespace = node->namespace_id;
    binding->canonical_id = node->node_id;
    binding->canonical_kind = node->kind;
    binding->parent_cell_id = parent->cell_id;
    if (!map_source_namespace(source->source_namespace,
            &binding->source_namespace) ||
        !map_source_kind(source->source_kind, &binding->source_kind) ||
        !map_source_role(source->source_role, &binding->source_role) ||
        !map_source_lifecycle(source->lifecycle_state,
            &binding->lifecycle_state)) {
        return AIOS_ERR_INVAL;
    }
    binding->source_id = source->source_id;
    binding->valid_flags = KERNEL_ROOM_BINDING_ALL_VALID_FLAGS;
    binding->canonical_generation = node->generation;
    binding->parent_generation = parent->generation;
    binding->source_instance = source->source_instance;
    binding->source_generation = source->source_generation;
    return AIOS_OK;
}

static bool negative_fixtures_pass(
    const kernel_room_source_binding_snapshot_t *valid,
    const kernel_room_management_snapshot_t *management,
    const slm_agent_source_snapshot_t *source
) {
    kernel_room_source_binding_snapshot_t test;
    kernel_room_source_binding_snapshot_t previous;
    slm_agent_source_snapshot_t changed_source;

    test = *valid;
    test.binding_count = 0U;
    if (validate_with_sources(&test, management, source, NULL) !=
            KERNEL_ROOM_BINDING_REJECT_MISSING) {
        return false;
    }

    changed_source = *source;
    changed_source.ready = 0U;
    if (validate_with_sources(valid, management, &changed_source, NULL) !=
            KERNEL_ROOM_BINDING_REJECT_MISSING) {
        return false;
    }

    changed_source = *source;
    changed_source.valid_flags &= ~SLM_AGENT_SOURCE_F_ROLE_VALID;
    if (validate_with_sources(valid, management, &changed_source, NULL) !=
            KERNEL_ROOM_BINDING_REJECT_MALFORMED) {
        return false;
    }

    test = *valid;
    test.binding_count = 2U;
    test.bindings[1] = test.bindings[0];
    if (validate_with_sources(&test, management, source, NULL) !=
            KERNEL_ROOM_BINDING_REJECT_DUPLICATE) {
        return false;
    }

    test = *valid;
    test.bindings[0].canonical_id++;
    if (validate_with_sources(&test, management, source, NULL) !=
            KERNEL_ROOM_BINDING_REJECT_ORPHAN) {
        return false;
    }

    test = *valid;
    test.bindings[0].source_namespace =
        KERNEL_ROOM_BINDING_SOURCE_NAMESPACE_COUNT;
    if (validate_with_sources(&test, management, source, NULL) !=
            KERNEL_ROOM_BINDING_REJECT_NAMESPACE) {
        return false;
    }

    test = *valid;
    test.bindings[0].source_kind =
        KERNEL_ROOM_BINDING_SOURCE_KIND_INVALID;
    if (validate_with_sources(&test, management, source, NULL) !=
            KERNEL_ROOM_BINDING_REJECT_KIND) {
        return false;
    }

    test = *valid;
    test.bindings[0].source_role =
        KERNEL_ROOM_BINDING_SOURCE_ROLE_INVALID;
    if (validate_with_sources(&test, management, source, NULL) !=
            KERNEL_ROOM_BINDING_REJECT_ROLE) {
        return false;
    }

    test = *valid;
    test.bindings[0].source_instance++;
    if (validate_with_sources(&test, management, source, NULL) !=
            KERNEL_ROOM_BINDING_REJECT_INSTANCE) {
        return false;
    }

    test = *valid;
    test.bindings[0].source_generation = 0U;
    if (validate_with_sources(&test, management, source, NULL) !=
            KERNEL_ROOM_BINDING_REJECT_ZERO_GENERATION) {
        return false;
    }

    test = *valid;
    previous = *valid;
    previous.binding_generation++;
    if (validate_with_sources(&test, management, source, &previous) !=
            KERNEL_ROOM_BINDING_REJECT_GENERATION_ROLLBACK) {
        return false;
    }

    test = *valid;
    previous = *valid;
    previous.bindings[0].source_generation++;
    if (validate_with_sources(&test, management, source, &previous) !=
            KERNEL_ROOM_BINDING_REJECT_GENERATION_ROLLBACK) {
        return false;
    }

    changed_source = *source;
    changed_source.source_generation++;
    if (validate_with_sources(valid, management, &changed_source, NULL) !=
            KERNEL_ROOM_BINDING_REJECT_STALE) {
        return false;
    }

    test = *valid;
    test.schema_version++;
    if (validate_with_sources(&test, management, source, NULL) !=
            KERNEL_ROOM_BINDING_REJECT_SCHEMA) {
        return false;
    }

    test = *valid;
    test.bindings[0].valid_flags &= ~KERNEL_ROOM_BINDING_F_ROLE_MATCH;
    if (validate_with_sources(&test, management, source, NULL) !=
            KERNEL_ROOM_BINDING_REJECT_MALFORMED) {
        return false;
    }

    test = *valid;
    test.binding_count = KERNEL_ROOM_SOURCE_BINDING_CAPACITY + 1U;
    if (validate_with_sources(&test, management, source, NULL) !=
            KERNEL_ROOM_BINDING_REJECT_OVERFLOW) {
        return false;
    }

    test = *valid;
    test.bindings[1].schema_version =
        KERNEL_ROOM_SOURCE_BINDING_SCHEMA_VERSION;
    if (validate_with_sources(&test, management, source, NULL) !=
            KERNEL_ROOM_BINDING_REJECT_TAIL) {
        return false;
    }

    test = *valid;
    test.reserved_tail[0] = 1U;
    if (validate_with_sources(&test, management, source, NULL) !=
            KERNEL_ROOM_BINDING_REJECT_TAIL) {
        return false;
    }

    return validate_with_sources(valid, management, NULL, NULL) ==
        KERNEL_ROOM_BINDING_REJECT_INIT_ORDER;
}

static bool copied_source_read_pass(void) {
    slm_agent_source_snapshot_t first;
    slm_agent_source_snapshot_t second;

    if (slm_agent_source_snapshot_read(&first) != AIOS_OK) {
        return false;
    }
    first.source_id++;
    first.source_instance++;
    first.source_generation++;
    if (slm_agent_source_snapshot_read(&second) != AIOS_OK ||
        !slm_agent_source_snapshot_valid(&second)) {
        return false;
    }
    return second.source_id == 1U &&
        second.source_instance == SLM_AGENT_SOURCE_BOOT_INSTANCE &&
        second.source_generation == SLM_AGENT_SOURCE_BOOT_GENERATION;
}

aios_status_t kernel_room_source_binding_init(void) {
    kernel_room_management_snapshot_t management;
    slm_agent_source_snapshot_t source;
    kernel_room_source_binding_snapshot_t seed;
    kernel_room_source_binding_snapshot_t first;
    kernel_room_source_binding_snapshot_t second;

    if (g_binding_ready) {
        return AIOS_ERR_BUSY;
    }
    memset(&g_binding_registry, 0, sizeof(g_binding_registry));
    g_binding_sample_sequence = 0U;

    if (kernel_room_source_binding_snapshot_read(NULL) != AIOS_ERR_INVAL ||
        kernel_room_source_binding_snapshot_read(&first) != AIOS_ERR_INVAL ||
        !kernel_room_management_ready() ||
        kernel_room_management_snapshot_read(&management) != AIOS_OK ||
        slm_agent_source_snapshot_read(&source) != AIOS_OK ||
        !slm_agent_source_snapshot_valid(&source) ||
        seed_snapshot(&seed, &management, &source) != AIOS_OK ||
        validate_with_sources(&seed, &management, &source, NULL) !=
            KERNEL_ROOM_BINDING_REJECT_NONE ||
        !negative_fixtures_pass(&seed, &management, &source) ||
        !copied_source_read_pass()) {
        serial_write("[ROOM] source binding selftest FAIL invariant=0\n");
        return AIOS_ERR_IO;
    }

    g_binding_registry = seed;
    g_binding_sample_sequence = seed.sample_sequence;
    g_binding_ready = true;
    if (kernel_room_source_binding_snapshot_read(&first) != AIOS_OK ||
        !kernel_room_source_binding_snapshot_valid(&first)) {
        g_binding_ready = false;
        serial_write("[ROOM] source binding selftest FAIL live=0\n");
        return AIOS_ERR_IO;
    }
    first.bindings[0].source_id++;
    if (kernel_room_source_binding_snapshot_read(&second) != AIOS_OK ||
        !kernel_room_source_binding_snapshot_valid(&second) ||
        second.bindings[0].source_id != source.source_id) {
        g_binding_ready = false;
        serial_write("[ROOM] source binding selftest FAIL copied_read=0\n");
        return AIOS_ERR_IO;
    }

    serial_write("[ROOM] source binding selftest PASS schema=1 struct_size=256 binding_generation=1 bindings=1 capacity=2 canonical_namespace=2 canonical_id=101 canonical_kind=1 canonical_generation=1 parent_cell_id=1 parent_generation=1 source_namespace=1 source_id=1 source_instance=1 source_generation=1 source_kind=1 source_role=1 kind_match=1 role_match=1 producer_owned=1 copied_read=1 missing_rejected=1 duplicate_rejected=1 orphan_rejected=1 namespace_rejected=1 kind_rejected=1 role_rejected=1 instance_rejected=1 zero_generation_rejected=1 generation_rollback_rejected=1 stale_rejected=1 init_order_rejected=1 schema_rejected=1 overflow_rejected=1 tail_rejected=1 source_valid=1 generation_valid=1 binding_valid=1 observation_only=1 management_only=1\n");
    kprintf("[ROOM] source binding ready canonical=101 source=1 binding_generation=1 management_only=1\n");
    return AIOS_OK;
}

bool kernel_room_source_binding_ready(void) {
    return g_binding_ready;
}

aios_status_t kernel_room_source_binding_snapshot_read(
    kernel_room_source_binding_snapshot_t *out
) {
    if (!out || !g_binding_ready) {
        return AIOS_ERR_INVAL;
    }

    *out = g_binding_registry;
    out->sample_sequence = __atomic_add_fetch(
        &g_binding_sample_sequence, 1ULL, __ATOMIC_RELAXED
    );
    return AIOS_OK;
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
