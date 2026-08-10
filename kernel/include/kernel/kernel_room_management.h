/*
 * AIOS Kernel - Kernel Room Management Hierarchy v0
 *
 * A bounded, immutable-after-init Room -> Cell -> Node -> NodeBit registry.
 * This contract is read-only and does not authorize, schedule, allocate, or
 * mutate any legacy NodeBit or subsystem state.
 */

#ifndef _AIOS_KERNEL_KERNEL_ROOM_MANAGEMENT_H
#define _AIOS_KERNEL_KERNEL_ROOM_MANAGEMENT_H

#include <kernel/types.h>

#define KERNEL_ROOM_MANAGEMENT_SCHEMA_VERSION 1U
#define KERNEL_ROOM_MANAGEMENT_GENERATION     1ULL

#define KERNEL_ROOM_CELL_CAPACITY             2U
#define KERNEL_ROOM_NODE_CAPACITY             4U
#define KERNEL_ROOM_NODEBIT_CAPACITY          8U

#define KERNEL_ROOM_CELL_ID_MAIN              1U
#define KERNEL_ROOM_NODE_ID_MAIN_AI           101U
#define KERNEL_ROOM_NODEBIT_ID_PRESENT        1001U
#define KERNEL_ROOM_NODEBIT_ID_SOURCE_BOUND   1002U
#define KERNEL_ROOM_BOOTSTRAP_SOURCE_ID       1U

typedef enum {
    KERNEL_ROOM_NAMESPACE_INVALID = 0,
    KERNEL_ROOM_NAMESPACE_CELL = 1,
    KERNEL_ROOM_NAMESPACE_NODE = 2,
    KERNEL_ROOM_NAMESPACE_NODEBIT = 3,
    KERNEL_ROOM_NAMESPACE_COUNT = 4,
} kernel_room_namespace_t;

AIOS_STATIC_ASSERT(KERNEL_ROOM_NAMESPACE_COUNT == 4,
    "Kernel Room namespace IDs are append-only");

typedef enum {
    KERNEL_ROOM_SOURCE_INVALID = 0,
    KERNEL_ROOM_SOURCE_BOOTSTRAP = 1,
    KERNEL_ROOM_SOURCE_COUNT = 2,
} kernel_room_source_kind_t;

AIOS_STATIC_ASSERT(KERNEL_ROOM_SOURCE_COUNT == 2,
    "Kernel Room source IDs are append-only");

typedef enum {
    KERNEL_ROOM_CELL_KIND_INVALID = 0,
    KERNEL_ROOM_CELL_KIND_MAIN = 1,
    KERNEL_ROOM_CELL_KIND_COUNT = 2,
} kernel_room_cell_kind_t;

AIOS_STATIC_ASSERT(KERNEL_ROOM_CELL_KIND_COUNT == 2,
    "Kernel Room Cell kind IDs are append-only");

typedef enum {
    KERNEL_ROOM_CELL_STATE_INVALID = 0,
    KERNEL_ROOM_CELL_STATE_ACTIVE = 1,
    KERNEL_ROOM_CELL_STATE_COUNT = 2,
} kernel_room_cell_state_t;

AIOS_STATIC_ASSERT(KERNEL_ROOM_CELL_STATE_COUNT == 2,
    "Kernel Room Cell state IDs are append-only");

typedef enum {
    KERNEL_ROOM_NODE_KIND_INVALID = 0,
    KERNEL_ROOM_NODE_KIND_AI_SERVICE = 1,
    KERNEL_ROOM_NODE_KIND_COUNT = 2,
} kernel_room_node_kind_t;

AIOS_STATIC_ASSERT(KERNEL_ROOM_NODE_KIND_COUNT == 2,
    "Kernel Room Node kind IDs are append-only");

typedef enum {
    KERNEL_ROOM_NODE_STATE_INVALID = 0,
    KERNEL_ROOM_NODE_STATE_DECLARED = 1,
    KERNEL_ROOM_NODE_STATE_COUNT = 2,
} kernel_room_node_state_t;

AIOS_STATIC_ASSERT(KERNEL_ROOM_NODE_STATE_COUNT == 2,
    "Kernel Room Node state IDs are append-only");

typedef enum {
    KERNEL_ROOM_NODEBIT_CLASS_INVALID = 0,
    KERNEL_ROOM_NODEBIT_CLASS_STATE = 1,
    KERNEL_ROOM_NODEBIT_CLASS_VALIDITY = 2,
    KERNEL_ROOM_NODEBIT_CLASS_COUNT = 3,
} kernel_room_nodebit_class_t;

AIOS_STATIC_ASSERT(KERNEL_ROOM_NODEBIT_CLASS_COUNT == 3,
    "Kernel Room NodeBit class IDs are append-only");

typedef enum {
    KERNEL_ROOM_NODEBIT_KEY_INVALID = 0,
    KERNEL_ROOM_NODEBIT_KEY_PRESENT = 1,
    KERNEL_ROOM_NODEBIT_KEY_SOURCE_BOUND = 2,
    KERNEL_ROOM_NODEBIT_KEY_COUNT = 3,
} kernel_room_nodebit_key_t;

AIOS_STATIC_ASSERT(KERNEL_ROOM_NODEBIT_KEY_COUNT == 3,
    "Kernel Room NodeBit key IDs are append-only");

#define KERNEL_ROOM_RECORD_SOURCE_VALID      ((uint32_t)BIT(0))
#define KERNEL_ROOM_RECORD_PARENT_VALID      ((uint32_t)BIT(1))
#define KERNEL_ROOM_RECORD_GENERATION_VALID  ((uint32_t)BIT(2))
#define KERNEL_ROOM_RECORD_VALUE_VALID       ((uint32_t)BIT(3))
#define KERNEL_ROOM_RECORD_ALL_VALID_FLAGS   ((uint32_t)0x0FU)

AIOS_STATIC_ASSERT(
    (KERNEL_ROOM_RECORD_SOURCE_VALID |
     KERNEL_ROOM_RECORD_PARENT_VALID |
     KERNEL_ROOM_RECORD_GENERATION_VALID |
     KERNEL_ROOM_RECORD_VALUE_VALID) == KERNEL_ROOM_RECORD_ALL_VALID_FLAGS,
    "Kernel Room record validity flags are append-only");

typedef struct {
    uint32_t schema_version;
    uint32_t struct_size;
    uint32_t namespace_id;
    uint32_t cell_id;
    uint32_t kind;
    uint32_t state;
    uint32_t valid_flags;
    uint32_t source_kind;
    uint32_t source_id;
    uint32_t bound_node_count;
    uint64_t generation;
    uint64_t source_generation;
} kernel_room_cell_record_t;

AIOS_STATIC_ASSERT(sizeof(kernel_room_cell_record_t) == 56U,
    "Kernel Room Cell record layout changed; version it explicitly");

typedef struct {
    uint32_t schema_version;
    uint32_t struct_size;
    uint32_t namespace_id;
    uint32_t node_id;
    uint32_t kind;
    uint32_t state;
    uint32_t valid_flags;
    uint32_t source_kind;
    uint32_t source_id;
    uint32_t parent_cell_id;
    uint64_t generation;
    uint64_t parent_generation;
    uint64_t source_generation;
} kernel_room_node_record_t;

AIOS_STATIC_ASSERT(sizeof(kernel_room_node_record_t) == 64U,
    "Kernel Room Node record layout changed; version it explicitly");

typedef struct {
    uint32_t schema_version;
    uint32_t struct_size;
    uint32_t namespace_id;
    uint32_t nodebit_id;
    uint32_t class_id;
    uint32_t key;
    uint32_t value;
    uint32_t valid_flags;
    uint32_t source_kind;
    uint32_t source_id;
    uint32_t parent_node_id;
    uint32_t reserved0;
    uint64_t generation;
    uint64_t parent_generation;
    uint64_t source_generation;
} kernel_room_nodebit_record_t;

AIOS_STATIC_ASSERT(sizeof(kernel_room_nodebit_record_t) == 72U,
    "Kernel Room NodeBit record layout changed; version it explicitly");

typedef struct {
    uint32_t schema_version;
    uint32_t struct_size;
    uint32_t observation_only;
    uint32_t management_only;
    uint32_t ready;
    uint32_t cell_count;
    uint32_t node_count;
    uint32_t bound_node_count;
    uint32_t nodebit_count;
    uint32_t bound_nodebit_count;
    uint32_t cell_capacity;
    uint32_t node_capacity;
    uint32_t nodebit_capacity;
    uint32_t source_valid;
    uint32_t generation_valid;
    uint32_t reserved0;
    uint64_t registry_generation;
    uint64_t sample_sequence;
    kernel_room_cell_record_t cells[KERNEL_ROOM_CELL_CAPACITY];
    kernel_room_node_record_t nodes[KERNEL_ROOM_NODE_CAPACITY];
    kernel_room_nodebit_record_t nodebits[KERNEL_ROOM_NODEBIT_CAPACITY];
} kernel_room_management_snapshot_t;

AIOS_STATIC_ASSERT(sizeof(kernel_room_management_snapshot_t) == 1024U,
    "Kernel Room management snapshot must remain a bounded 1 KiB contract");

aios_status_t kernel_room_management_init(void);
bool kernel_room_management_ready(void);
aios_status_t kernel_room_management_snapshot_read(
    kernel_room_management_snapshot_t *out
);
bool kernel_room_management_snapshot_valid(
    const kernel_room_management_snapshot_t *snapshot
);

#endif /* _AIOS_KERNEL_KERNEL_ROOM_MANAGEMENT_H */
