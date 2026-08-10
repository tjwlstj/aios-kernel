/*
 * AIOS Kernel - Kernel Room Management Hierarchy v0
 *
 * CURRENT v0 scope: one bootstrap Cell, one explicitly bound declared Node,
 * and two typed child NodeBits. The registry becomes immutable after init and
 * exposes read-only snapshots only. It has no scheduler, allocator, quota,
 * capability, policy, syscall-enforcement, or legacy NodeBit mutation edge.
 */

#include <kernel/kernel_room_management.h>
#include <drivers/serial.h>
#include <drivers/vga.h>
#include <lib/string.h>

static kernel_room_management_snapshot_t g_management_registry;
static bool g_management_ready = false;
static uint64_t g_management_sample_sequence = 0;

static bool namespace_valid(uint32_t namespace_id) {
    return namespace_id > KERNEL_ROOM_NAMESPACE_INVALID &&
           namespace_id < KERNEL_ROOM_NAMESPACE_COUNT;
}

static bool source_valid(uint32_t source_kind, uint32_t source_id) {
    return source_kind == KERNEL_ROOM_SOURCE_BOOTSTRAP &&
           source_id == KERNEL_ROOM_BOOTSTRAP_SOURCE_ID;
}

static bool cell_kind_valid(uint32_t kind) {
    return kind > KERNEL_ROOM_CELL_KIND_INVALID &&
           kind < KERNEL_ROOM_CELL_KIND_COUNT;
}

static bool cell_state_valid(uint32_t state) {
    return state > KERNEL_ROOM_CELL_STATE_INVALID &&
           state < KERNEL_ROOM_CELL_STATE_COUNT;
}

static bool node_kind_valid(uint32_t kind) {
    return kind > KERNEL_ROOM_NODE_KIND_INVALID &&
           kind < KERNEL_ROOM_NODE_KIND_COUNT;
}

static bool node_state_valid(uint32_t state) {
    return state > KERNEL_ROOM_NODE_STATE_INVALID &&
           state < KERNEL_ROOM_NODE_STATE_COUNT;
}

static bool nodebit_class_valid(uint32_t class_id) {
    return class_id > KERNEL_ROOM_NODEBIT_CLASS_INVALID &&
           class_id < KERNEL_ROOM_NODEBIT_CLASS_COUNT;
}

static bool nodebit_key_valid(uint32_t key) {
    return key > KERNEL_ROOM_NODEBIT_KEY_INVALID &&
           key < KERNEL_ROOM_NODEBIT_KEY_COUNT;
}

static bool nodebit_class_key_valid(uint32_t class_id, uint32_t key) {
    return (class_id == KERNEL_ROOM_NODEBIT_CLASS_STATE &&
            key == KERNEL_ROOM_NODEBIT_KEY_PRESENT) ||
           (class_id == KERNEL_ROOM_NODEBIT_CLASS_VALIDITY &&
            key == KERNEL_ROOM_NODEBIT_KEY_SOURCE_BOUND);
}

static const kernel_room_cell_record_t *find_cell(
    const kernel_room_management_snapshot_t *snapshot,
    uint32_t cell_id
) {
    for (uint32_t i = 0; i < snapshot->cell_count; i++) {
        if (snapshot->cells[i].cell_id == cell_id) {
            return &snapshot->cells[i];
        }
    }
    return NULL;
}

static const kernel_room_node_record_t *find_node(
    const kernel_room_management_snapshot_t *snapshot,
    uint32_t node_id
) {
    for (uint32_t i = 0; i < snapshot->node_count; i++) {
        if (snapshot->nodes[i].node_id == node_id) {
            return &snapshot->nodes[i];
        }
    }
    return NULL;
}

static bool tail_zero(
    const void *base,
    uint32_t used,
    uint32_t capacity,
    size_t record_size
) {
    const uint8_t *bytes = (const uint8_t *)base;
    for (uint32_t i = used; i < capacity; i++) {
        for (size_t j = 0; j < record_size; j++) {
            if (bytes[(size_t)i * record_size + j] != 0U) {
                return false;
            }
        }
    }
    return true;
}

static bool cell_records_valid(
    const kernel_room_management_snapshot_t *snapshot
) {
    const uint32_t expected_flags =
        KERNEL_ROOM_RECORD_SOURCE_VALID |
        KERNEL_ROOM_RECORD_GENERATION_VALID;

    for (uint32_t i = 0; i < snapshot->cell_count; i++) {
        const kernel_room_cell_record_t *cell = &snapshot->cells[i];
        uint32_t child_count = 0;

        if (cell->schema_version != KERNEL_ROOM_MANAGEMENT_SCHEMA_VERSION ||
            cell->struct_size != sizeof(*cell) ||
            cell->namespace_id != KERNEL_ROOM_NAMESPACE_CELL ||
            !namespace_valid(cell->namespace_id) ||
            cell->cell_id == 0U ||
            !cell_kind_valid(cell->kind) ||
            !cell_state_valid(cell->state) ||
            cell->valid_flags != expected_flags ||
            !source_valid(cell->source_kind, cell->source_id) ||
            cell->generation != snapshot->registry_generation ||
            cell->source_generation != snapshot->registry_generation) {
            return false;
        }

        for (uint32_t j = 0; j < i; j++) {
            if (snapshot->cells[j].cell_id == cell->cell_id) {
                return false;
            }
        }
        for (uint32_t j = 0; j < snapshot->node_count; j++) {
            if (snapshot->nodes[j].parent_cell_id == cell->cell_id) {
                child_count++;
            }
        }
        if (cell->bound_node_count != child_count ||
            child_count > snapshot->node_capacity) {
            return false;
        }
    }
    return true;
}

static bool node_records_valid(
    const kernel_room_management_snapshot_t *snapshot
) {
    const uint32_t expected_flags =
        KERNEL_ROOM_RECORD_SOURCE_VALID |
        KERNEL_ROOM_RECORD_PARENT_VALID |
        KERNEL_ROOM_RECORD_GENERATION_VALID;

    for (uint32_t i = 0; i < snapshot->node_count; i++) {
        const kernel_room_node_record_t *node = &snapshot->nodes[i];
        const kernel_room_cell_record_t *parent;

        if (node->schema_version != KERNEL_ROOM_MANAGEMENT_SCHEMA_VERSION ||
            node->struct_size != sizeof(*node) ||
            node->namespace_id != KERNEL_ROOM_NAMESPACE_NODE ||
            !namespace_valid(node->namespace_id) ||
            node->node_id == 0U ||
            !node_kind_valid(node->kind) ||
            !node_state_valid(node->state) ||
            node->valid_flags != expected_flags ||
            !source_valid(node->source_kind, node->source_id) ||
            node->generation != snapshot->registry_generation ||
            node->source_generation != snapshot->registry_generation) {
            return false;
        }

        parent = find_cell(snapshot, node->parent_cell_id);
        if (!parent || node->parent_generation != parent->generation) {
            return false;
        }
        for (uint32_t j = 0; j < i; j++) {
            if (snapshot->nodes[j].node_id == node->node_id) {
                return false;
            }
        }
    }
    return true;
}

static bool nodebit_records_valid(
    const kernel_room_management_snapshot_t *snapshot
) {
    const uint32_t expected_flags = KERNEL_ROOM_RECORD_ALL_VALID_FLAGS;

    for (uint32_t i = 0; i < snapshot->nodebit_count; i++) {
        const kernel_room_nodebit_record_t *nodebit = &snapshot->nodebits[i];
        const kernel_room_node_record_t *parent;

        if (nodebit->schema_version != KERNEL_ROOM_MANAGEMENT_SCHEMA_VERSION ||
            nodebit->struct_size != sizeof(*nodebit) ||
            nodebit->namespace_id != KERNEL_ROOM_NAMESPACE_NODEBIT ||
            !namespace_valid(nodebit->namespace_id) ||
            nodebit->nodebit_id == 0U ||
            !nodebit_class_valid(nodebit->class_id) ||
            !nodebit_key_valid(nodebit->key) ||
            !nodebit_class_key_valid(nodebit->class_id, nodebit->key) ||
            nodebit->value != 1U ||
            nodebit->valid_flags != expected_flags ||
            !source_valid(nodebit->source_kind, nodebit->source_id) ||
            nodebit->reserved0 != 0U ||
            nodebit->generation != snapshot->registry_generation ||
            nodebit->source_generation != snapshot->registry_generation) {
            return false;
        }

        parent = find_node(snapshot, nodebit->parent_node_id);
        if (!parent || nodebit->parent_generation != parent->generation) {
            return false;
        }
        for (uint32_t j = 0; j < i; j++) {
            if (snapshot->nodebits[j].nodebit_id == nodebit->nodebit_id) {
                return false;
            }
        }
    }
    return true;
}

bool kernel_room_management_snapshot_valid(
    const kernel_room_management_snapshot_t *snapshot
) {
    if (!snapshot ||
        snapshot->schema_version != KERNEL_ROOM_MANAGEMENT_SCHEMA_VERSION ||
        snapshot->struct_size != sizeof(*snapshot) ||
        snapshot->observation_only != 1U ||
        snapshot->management_only != 1U ||
        snapshot->ready != 1U ||
        snapshot->cell_count == 0U ||
        snapshot->cell_count > KERNEL_ROOM_CELL_CAPACITY ||
        snapshot->node_count == 0U ||
        snapshot->node_count > KERNEL_ROOM_NODE_CAPACITY ||
        snapshot->bound_node_count != snapshot->node_count ||
        snapshot->nodebit_count == 0U ||
        snapshot->nodebit_count > KERNEL_ROOM_NODEBIT_CAPACITY ||
        snapshot->bound_nodebit_count != snapshot->nodebit_count ||
        snapshot->cell_capacity != KERNEL_ROOM_CELL_CAPACITY ||
        snapshot->node_capacity != KERNEL_ROOM_NODE_CAPACITY ||
        snapshot->nodebit_capacity != KERNEL_ROOM_NODEBIT_CAPACITY ||
        snapshot->source_valid != 1U ||
        snapshot->generation_valid != 1U ||
        snapshot->reserved0 != 0U ||
        snapshot->registry_generation == 0U ||
        snapshot->sample_sequence == 0U) {
        return false;
    }

    if (!cell_records_valid(snapshot) ||
        !node_records_valid(snapshot) ||
        !nodebit_records_valid(snapshot) ||
        !tail_zero(snapshot->cells, snapshot->cell_count,
            snapshot->cell_capacity, sizeof(snapshot->cells[0])) ||
        !tail_zero(snapshot->nodes, snapshot->node_count,
            snapshot->node_capacity, sizeof(snapshot->nodes[0])) ||
        !tail_zero(snapshot->nodebits, snapshot->nodebit_count,
            snapshot->nodebit_capacity, sizeof(snapshot->nodebits[0]))) {
        return false;
    }
    return true;
}

static void seed_snapshot(kernel_room_management_snapshot_t *snapshot) {
    kernel_room_cell_record_t *cell;
    kernel_room_node_record_t *node;
    kernel_room_nodebit_record_t *present;
    kernel_room_nodebit_record_t *source_bound;

    memset(snapshot, 0, sizeof(*snapshot));
    snapshot->schema_version = KERNEL_ROOM_MANAGEMENT_SCHEMA_VERSION;
    snapshot->struct_size = (uint32_t)sizeof(*snapshot);
    snapshot->observation_only = 1U;
    snapshot->management_only = 1U;
    snapshot->ready = 1U;
    snapshot->cell_count = 1U;
    snapshot->node_count = 1U;
    snapshot->bound_node_count = 1U;
    snapshot->nodebit_count = 2U;
    snapshot->bound_nodebit_count = 2U;
    snapshot->cell_capacity = KERNEL_ROOM_CELL_CAPACITY;
    snapshot->node_capacity = KERNEL_ROOM_NODE_CAPACITY;
    snapshot->nodebit_capacity = KERNEL_ROOM_NODEBIT_CAPACITY;
    snapshot->source_valid = 1U;
    snapshot->generation_valid = 1U;
    snapshot->registry_generation = KERNEL_ROOM_MANAGEMENT_GENERATION;
    snapshot->sample_sequence = 1U;

    cell = &snapshot->cells[0];
    cell->schema_version = KERNEL_ROOM_MANAGEMENT_SCHEMA_VERSION;
    cell->struct_size = (uint32_t)sizeof(*cell);
    cell->namespace_id = KERNEL_ROOM_NAMESPACE_CELL;
    cell->cell_id = KERNEL_ROOM_CELL_ID_MAIN;
    cell->kind = KERNEL_ROOM_CELL_KIND_MAIN;
    cell->state = KERNEL_ROOM_CELL_STATE_ACTIVE;
    cell->valid_flags = KERNEL_ROOM_RECORD_SOURCE_VALID |
        KERNEL_ROOM_RECORD_GENERATION_VALID;
    cell->source_kind = KERNEL_ROOM_SOURCE_BOOTSTRAP;
    cell->source_id = KERNEL_ROOM_BOOTSTRAP_SOURCE_ID;
    cell->bound_node_count = 1U;
    cell->generation = KERNEL_ROOM_MANAGEMENT_GENERATION;
    cell->source_generation = KERNEL_ROOM_MANAGEMENT_GENERATION;

    node = &snapshot->nodes[0];
    node->schema_version = KERNEL_ROOM_MANAGEMENT_SCHEMA_VERSION;
    node->struct_size = (uint32_t)sizeof(*node);
    node->namespace_id = KERNEL_ROOM_NAMESPACE_NODE;
    node->node_id = KERNEL_ROOM_NODE_ID_MAIN_AI;
    node->kind = KERNEL_ROOM_NODE_KIND_AI_SERVICE;
    node->state = KERNEL_ROOM_NODE_STATE_DECLARED;
    node->valid_flags = KERNEL_ROOM_RECORD_SOURCE_VALID |
        KERNEL_ROOM_RECORD_PARENT_VALID |
        KERNEL_ROOM_RECORD_GENERATION_VALID;
    node->source_kind = KERNEL_ROOM_SOURCE_BOOTSTRAP;
    node->source_id = KERNEL_ROOM_BOOTSTRAP_SOURCE_ID;
    node->parent_cell_id = KERNEL_ROOM_CELL_ID_MAIN;
    node->generation = KERNEL_ROOM_MANAGEMENT_GENERATION;
    node->parent_generation = KERNEL_ROOM_MANAGEMENT_GENERATION;
    node->source_generation = KERNEL_ROOM_MANAGEMENT_GENERATION;

    present = &snapshot->nodebits[0];
    present->schema_version = KERNEL_ROOM_MANAGEMENT_SCHEMA_VERSION;
    present->struct_size = (uint32_t)sizeof(*present);
    present->namespace_id = KERNEL_ROOM_NAMESPACE_NODEBIT;
    present->nodebit_id = KERNEL_ROOM_NODEBIT_ID_PRESENT;
    present->class_id = KERNEL_ROOM_NODEBIT_CLASS_STATE;
    present->key = KERNEL_ROOM_NODEBIT_KEY_PRESENT;
    present->value = 1U;
    present->valid_flags = KERNEL_ROOM_RECORD_ALL_VALID_FLAGS;
    present->source_kind = KERNEL_ROOM_SOURCE_BOOTSTRAP;
    present->source_id = KERNEL_ROOM_BOOTSTRAP_SOURCE_ID;
    present->parent_node_id = KERNEL_ROOM_NODE_ID_MAIN_AI;
    present->generation = KERNEL_ROOM_MANAGEMENT_GENERATION;
    present->parent_generation = KERNEL_ROOM_MANAGEMENT_GENERATION;
    present->source_generation = KERNEL_ROOM_MANAGEMENT_GENERATION;

    source_bound = &snapshot->nodebits[1];
    *source_bound = *present;
    source_bound->nodebit_id = KERNEL_ROOM_NODEBIT_ID_SOURCE_BOUND;
    source_bound->class_id = KERNEL_ROOM_NODEBIT_CLASS_VALIDITY;
    source_bound->key = KERNEL_ROOM_NODEBIT_KEY_SOURCE_BOUND;
}

static bool duplicate_rejected(
    const kernel_room_management_snapshot_t *valid
) {
    kernel_room_management_snapshot_t test = *valid;
    test.cell_count = 2U;
    test.cells[1] = test.cells[0];
    if (kernel_room_management_snapshot_valid(&test)) {
        return false;
    }
    test = *valid;
    test.node_count = 2U;
    test.bound_node_count = 2U;
    test.cells[0].bound_node_count = 2U;
    test.nodes[1] = test.nodes[0];
    if (kernel_room_management_snapshot_valid(&test)) {
        return false;
    }
    test = *valid;
    test.nodebit_count = 3U;
    test.bound_nodebit_count = 3U;
    test.nodebits[2] = test.nodebits[0];
    return !kernel_room_management_snapshot_valid(&test);
}

static bool orphan_rejected(
    const kernel_room_management_snapshot_t *valid
) {
    kernel_room_management_snapshot_t test = *valid;
    test.nodes[0].parent_cell_id = 999U;
    if (kernel_room_management_snapshot_valid(&test)) {
        return false;
    }
    test = *valid;
    test.nodebits[0].parent_node_id = 999U;
    return !kernel_room_management_snapshot_valid(&test);
}

static bool unknown_rejected(
    const kernel_room_management_snapshot_t *valid
) {
    kernel_room_management_snapshot_t test = *valid;
    test.nodes[0].namespace_id = KERNEL_ROOM_NAMESPACE_COUNT;
    if (kernel_room_management_snapshot_valid(&test)) {
        return false;
    }
    test = *valid;
    test.cells[0].source_kind = KERNEL_ROOM_SOURCE_COUNT;
    if (kernel_room_management_snapshot_valid(&test)) {
        return false;
    }
    test = *valid;
    test.cells[0].source_id = KERNEL_ROOM_BOOTSTRAP_SOURCE_ID + 1U;
    if (kernel_room_management_snapshot_valid(&test)) {
        return false;
    }
    test = *valid;
    test.nodebits[0].class_id = KERNEL_ROOM_NODEBIT_CLASS_COUNT;
    if (kernel_room_management_snapshot_valid(&test)) {
        return false;
    }
    test = *valid;
    test.nodebits[0].class_id = KERNEL_ROOM_NODEBIT_CLASS_VALIDITY;
    return !kernel_room_management_snapshot_valid(&test);
}

static bool stale_rejected(
    const kernel_room_management_snapshot_t *valid
) {
    kernel_room_management_snapshot_t test = *valid;
    test.nodes[0].parent_generation++;
    if (kernel_room_management_snapshot_valid(&test)) {
        return false;
    }
    test = *valid;
    test.nodebits[0].source_generation++;
    return !kernel_room_management_snapshot_valid(&test);
}

static bool overflow_rejected(
    const kernel_room_management_snapshot_t *valid
) {
    kernel_room_management_snapshot_t test = *valid;
    test.cell_count = KERNEL_ROOM_CELL_CAPACITY + 1U;
    if (kernel_room_management_snapshot_valid(&test)) {
        return false;
    }
    test = *valid;
    test.node_count = KERNEL_ROOM_NODE_CAPACITY + 1U;
    if (kernel_room_management_snapshot_valid(&test)) {
        return false;
    }
    test = *valid;
    test.nodebit_count = KERNEL_ROOM_NODEBIT_CAPACITY + 1U;
    return !kernel_room_management_snapshot_valid(&test);
}

static bool tail_rejected(
    const kernel_room_management_snapshot_t *valid
) {
    kernel_room_management_snapshot_t test = *valid;
    test.cells[1].cell_id = 1U;
    if (kernel_room_management_snapshot_valid(&test)) {
        return false;
    }
    test = *valid;
    test.nodes[1].node_id = 1U;
    if (kernel_room_management_snapshot_valid(&test)) {
        return false;
    }
    test = *valid;
    test.nodebits[2].nodebit_id = 1U;
    return !kernel_room_management_snapshot_valid(&test);
}

aios_status_t kernel_room_management_init(void) {
    kernel_room_management_snapshot_t seed;
    kernel_room_management_snapshot_t live;

    if (g_management_ready) {
        return AIOS_ERR_BUSY;
    }
    memset(&g_management_registry, 0, sizeof(g_management_registry));
    g_management_sample_sequence = 0U;

    if (kernel_room_management_snapshot_read(NULL) != AIOS_ERR_INVAL ||
        kernel_room_management_snapshot_read(&live) != AIOS_ERR_INVAL) {
        serial_write("[ROOM] management hierarchy selftest FAIL precondition=0\n");
        return AIOS_ERR_IO;
    }

    seed_snapshot(&seed);
    if (!kernel_room_management_snapshot_valid(&seed) ||
        !duplicate_rejected(&seed) ||
        !orphan_rejected(&seed) ||
        !unknown_rejected(&seed) ||
        !stale_rejected(&seed) ||
        !overflow_rejected(&seed) ||
        !tail_rejected(&seed)) {
        serial_write("[ROOM] management hierarchy selftest FAIL invariant=0\n");
        return AIOS_ERR_IO;
    }

    g_management_registry = seed;
    g_management_sample_sequence = seed.sample_sequence;
    g_management_ready = true;
    if (kernel_room_management_snapshot_read(&live) != AIOS_OK ||
        !kernel_room_management_snapshot_valid(&live)) {
        g_management_ready = false;
        serial_write("[ROOM] management hierarchy selftest FAIL live=0\n");
        return AIOS_ERR_IO;
    }

    serial_write("[ROOM] management hierarchy selftest PASS schema=1 struct_size=1024 generation=1 cells=1 nodes=1 bound_nodes=1 nodebits=2 bound_nodebits=2 source_valid=1 generation_valid=1 duplicate_rejected=1 orphan_rejected=1 unknown_rejected=1 stale_rejected=1 overflow_rejected=1 tail_rejected=1 observation_only=1 management_only=1\n");
    kprintf("[ROOM] management hierarchy ready cells=1 nodes=1 nodebits=2 management_only=1\n");
    return AIOS_OK;
}

bool kernel_room_management_ready(void) {
    return g_management_ready;
}

aios_status_t kernel_room_management_snapshot_read(
    kernel_room_management_snapshot_t *out
) {
    if (!out || !g_management_ready) {
        return AIOS_ERR_INVAL;
    }

    *out = g_management_registry;
    out->sample_sequence = __atomic_add_fetch(
        &g_management_sample_sequence, 1ULL, __ATOMIC_RELAXED
    );
    return AIOS_OK;
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
