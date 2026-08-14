/*
 * AIOS Kernel - Kernel Room K2-a Native Source Binding
 *
 * A bounded, immutable boot-local semantic binding from the canonical K1
 * AI_SERVICE Node to a producer-owned SLM MAIN agent source. This contract is
 * read-only and does not authorize, schedule, allocate, reconcile, or apply.
 */

#ifndef _AIOS_KERNEL_KERNEL_ROOM_SOURCE_BINDING_H
#define _AIOS_KERNEL_KERNEL_ROOM_SOURCE_BINDING_H

#include <kernel/types.h>

#define KERNEL_ROOM_SOURCE_BINDING_SCHEMA_VERSION 1U
#define KERNEL_ROOM_SOURCE_BINDING_GENERATION     1ULL
#define KERNEL_ROOM_SOURCE_BINDING_CAPACITY       2U
#define KERNEL_ROOM_SOURCE_BINDING_TAIL_SIZE      32U

typedef enum {
    KERNEL_ROOM_BINDING_SOURCE_NAMESPACE_INVALID = 0,
    KERNEL_ROOM_BINDING_SOURCE_NAMESPACE_NATIVE_SLM_AGENT_TREE = 1,
    KERNEL_ROOM_BINDING_SOURCE_NAMESPACE_COUNT = 2,
} kernel_room_binding_source_namespace_t;

AIOS_STATIC_ASSERT(
    KERNEL_ROOM_BINDING_SOURCE_NAMESPACE_INVALID == 0 &&
    KERNEL_ROOM_BINDING_SOURCE_NAMESPACE_NATIVE_SLM_AGENT_TREE == 1 &&
    KERNEL_ROOM_BINDING_SOURCE_NAMESPACE_COUNT == 2,
    "Kernel Room binding source namespace IDs are append-only");

typedef enum {
    KERNEL_ROOM_BINDING_SOURCE_KIND_INVALID = 0,
    KERNEL_ROOM_BINDING_SOURCE_KIND_AI_SERVICE = 1,
    KERNEL_ROOM_BINDING_SOURCE_KIND_COUNT = 2,
} kernel_room_binding_source_kind_t;

AIOS_STATIC_ASSERT(
    KERNEL_ROOM_BINDING_SOURCE_KIND_INVALID == 0 &&
    KERNEL_ROOM_BINDING_SOURCE_KIND_AI_SERVICE == 1 &&
    KERNEL_ROOM_BINDING_SOURCE_KIND_COUNT == 2,
    "Kernel Room binding source kind IDs are append-only");

typedef enum {
    KERNEL_ROOM_BINDING_SOURCE_ROLE_INVALID = 0,
    KERNEL_ROOM_BINDING_SOURCE_ROLE_MAIN = 1,
    KERNEL_ROOM_BINDING_SOURCE_ROLE_COUNT = 2,
} kernel_room_binding_source_role_t;

AIOS_STATIC_ASSERT(
    KERNEL_ROOM_BINDING_SOURCE_ROLE_INVALID == 0 &&
    KERNEL_ROOM_BINDING_SOURCE_ROLE_MAIN == 1 &&
    KERNEL_ROOM_BINDING_SOURCE_ROLE_COUNT == 2,
    "Kernel Room binding source role IDs are append-only");

typedef enum {
    KERNEL_ROOM_BINDING_SOURCE_LIFECYCLE_INVALID = 0,
    KERNEL_ROOM_BINDING_SOURCE_LIFECYCLE_ACTIVE = 1,
    KERNEL_ROOM_BINDING_SOURCE_LIFECYCLE_COUNT = 2,
} kernel_room_binding_source_lifecycle_t;

AIOS_STATIC_ASSERT(
    KERNEL_ROOM_BINDING_SOURCE_LIFECYCLE_INVALID == 0 &&
    KERNEL_ROOM_BINDING_SOURCE_LIFECYCLE_ACTIVE == 1 &&
    KERNEL_ROOM_BINDING_SOURCE_LIFECYCLE_COUNT == 2,
    "Kernel Room binding lifecycle IDs are append-only");

typedef enum {
    KERNEL_ROOM_BINDING_REJECT_NONE = 0,
    KERNEL_ROOM_BINDING_REJECT_INIT_ORDER = 1,
    KERNEL_ROOM_BINDING_REJECT_MISSING = 2,
    KERNEL_ROOM_BINDING_REJECT_SCHEMA = 3,
    KERNEL_ROOM_BINDING_REJECT_MALFORMED = 4,
    KERNEL_ROOM_BINDING_REJECT_OVERFLOW = 5,
    KERNEL_ROOM_BINDING_REJECT_DUPLICATE = 6,
    KERNEL_ROOM_BINDING_REJECT_ORPHAN = 7,
    KERNEL_ROOM_BINDING_REJECT_NAMESPACE = 8,
    KERNEL_ROOM_BINDING_REJECT_KIND = 9,
    KERNEL_ROOM_BINDING_REJECT_ROLE = 10,
    KERNEL_ROOM_BINDING_REJECT_INSTANCE = 11,
    KERNEL_ROOM_BINDING_REJECT_ZERO_GENERATION = 12,
    KERNEL_ROOM_BINDING_REJECT_GENERATION_ROLLBACK = 13,
    KERNEL_ROOM_BINDING_REJECT_STALE = 14,
    KERNEL_ROOM_BINDING_REJECT_TAIL = 15,
    KERNEL_ROOM_BINDING_REJECT_COUNT = 16,
} kernel_room_binding_reject_reason_t;

AIOS_STATIC_ASSERT(
    KERNEL_ROOM_BINDING_REJECT_NONE == 0 &&
    KERNEL_ROOM_BINDING_REJECT_INIT_ORDER == 1 &&
    KERNEL_ROOM_BINDING_REJECT_MISSING == 2 &&
    KERNEL_ROOM_BINDING_REJECT_SCHEMA == 3 &&
    KERNEL_ROOM_BINDING_REJECT_MALFORMED == 4 &&
    KERNEL_ROOM_BINDING_REJECT_OVERFLOW == 5 &&
    KERNEL_ROOM_BINDING_REJECT_DUPLICATE == 6 &&
    KERNEL_ROOM_BINDING_REJECT_ORPHAN == 7 &&
    KERNEL_ROOM_BINDING_REJECT_NAMESPACE == 8 &&
    KERNEL_ROOM_BINDING_REJECT_KIND == 9 &&
    KERNEL_ROOM_BINDING_REJECT_ROLE == 10 &&
    KERNEL_ROOM_BINDING_REJECT_INSTANCE == 11 &&
    KERNEL_ROOM_BINDING_REJECT_ZERO_GENERATION == 12 &&
    KERNEL_ROOM_BINDING_REJECT_GENERATION_ROLLBACK == 13 &&
    KERNEL_ROOM_BINDING_REJECT_STALE == 14 &&
    KERNEL_ROOM_BINDING_REJECT_TAIL == 15 &&
    KERNEL_ROOM_BINDING_REJECT_COUNT == 16,
    "Kernel Room binding reject reason IDs are append-only");

#define KERNEL_ROOM_BINDING_F_CANONICAL_VALID ((uint32_t)BIT(0))
#define KERNEL_ROOM_BINDING_F_PARENT_VALID    ((uint32_t)BIT(1))
#define KERNEL_ROOM_BINDING_F_SOURCE_VALID    ((uint32_t)BIT(2))
#define KERNEL_ROOM_BINDING_F_GENERATION_VALID ((uint32_t)BIT(3))
#define KERNEL_ROOM_BINDING_F_KIND_MATCH      ((uint32_t)BIT(4))
#define KERNEL_ROOM_BINDING_F_ROLE_MATCH      ((uint32_t)BIT(5))
#define KERNEL_ROOM_BINDING_F_LIFECYCLE_VALID ((uint32_t)BIT(6))
#define KERNEL_ROOM_BINDING_ALL_VALID_FLAGS   ((uint32_t)0x7FU)

AIOS_STATIC_ASSERT(
    (KERNEL_ROOM_BINDING_F_CANONICAL_VALID |
     KERNEL_ROOM_BINDING_F_PARENT_VALID |
     KERNEL_ROOM_BINDING_F_SOURCE_VALID |
     KERNEL_ROOM_BINDING_F_GENERATION_VALID |
     KERNEL_ROOM_BINDING_F_KIND_MATCH |
     KERNEL_ROOM_BINDING_F_ROLE_MATCH |
     KERNEL_ROOM_BINDING_F_LIFECYCLE_VALID) ==
        KERNEL_ROOM_BINDING_ALL_VALID_FLAGS,
    "Kernel Room binding validity flags are append-only");

typedef struct {
    uint32_t schema_version;
    uint32_t struct_size;
    uint32_t canonical_namespace;
    uint32_t canonical_id;
    uint32_t canonical_kind;
    uint32_t parent_cell_id;
    uint32_t source_namespace;
    uint32_t source_id;
    uint32_t source_kind;
    uint32_t source_role;
    uint32_t lifecycle_state;
    uint32_t valid_flags;
    uint64_t canonical_generation;
    uint64_t parent_generation;
    uint64_t source_instance;
    uint64_t source_generation;
} kernel_room_source_binding_record_t;

AIOS_STATIC_ASSERT(sizeof(kernel_room_source_binding_record_t) == 80U,
    "Kernel Room source binding record layout changed; version it explicitly");

typedef struct {
    uint32_t schema_version;
    uint32_t struct_size;
    uint32_t observation_only;
    uint32_t management_only;
    uint32_t ready;
    uint32_t binding_count;
    uint32_t binding_capacity;
    uint32_t last_reject_reason;
    uint32_t source_valid;
    uint32_t generation_valid;
    uint32_t binding_valid;
    uint32_t reserved0;
    uint64_t binding_generation;
    uint64_t sample_sequence;
    kernel_room_source_binding_record_t
        bindings[KERNEL_ROOM_SOURCE_BINDING_CAPACITY];
    uint8_t reserved_tail[KERNEL_ROOM_SOURCE_BINDING_TAIL_SIZE];
} kernel_room_source_binding_snapshot_t;

AIOS_STATIC_ASSERT(sizeof(kernel_room_source_binding_snapshot_t) == 256U,
    "Kernel Room source binding snapshot must remain a bounded 256B contract");

aios_status_t kernel_room_source_binding_init(void);
bool kernel_room_source_binding_ready(void);
aios_status_t kernel_room_source_binding_snapshot_read(
    kernel_room_source_binding_snapshot_t *out
);
kernel_room_binding_reject_reason_t
kernel_room_source_binding_snapshot_validate(
    const kernel_room_source_binding_snapshot_t *snapshot
);
bool kernel_room_source_binding_snapshot_valid(
    const kernel_room_source_binding_snapshot_t *snapshot
);

#endif /* _AIOS_KERNEL_KERNEL_ROOM_SOURCE_BINDING_H */
