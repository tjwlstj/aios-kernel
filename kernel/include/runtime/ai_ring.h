/*
 * AIOS Kernel - Shared Inference Ring UAPI
 * AI-Native Operating System
 *
 * This header defines the shared-memory submit/completion ring layout used
 * between the kernel and the future ring3 AI runtime. The intent is to keep
 * control operations in syscalls while moving high-frequency inference submit
 * and completion traffic onto a zero-copy data plane.
 */

#ifndef _AIOS_AI_RING_H
#define _AIOS_AI_RING_H

#include <kernel/types.h>

/* ABI version for shared submit/completion ring structures. */
#define AI_RING_ABI_VERSION          1

/* Recommended power-of-two entry counts for bootstrap runtimes. */
#define AI_RING_MIN_ENTRIES          64
#define AI_RING_DEFAULT_ENTRIES      256
#define AI_RING_MAX_ENTRIES          4096

/* Ring roles */
#define AI_RING_ROLE_SUBMIT          1
#define AI_RING_ROLE_COMPLETION      2

/* Submit opcodes */
#define AI_RING_OP_INFER             1
#define AI_RING_OP_TRAIN             2
#define AI_RING_OP_PIPELINE          3
#define AI_RING_OP_NOP               255

/* Submit flags */
#define AI_RING_F_STREAM             BIT(0)
#define AI_RING_F_DEADLINE           BIT(1)
#define AI_RING_F_SHARED_INPUT       BIT(2)
#define AI_RING_F_SHARED_OUTPUT      BIT(3)
#define AI_RING_F_ZERO_COPY_HINT     BIT(4)
#define AI_RING_F_HIGH_PRIORITY      BIT(5)
#define AI_RING_F_MEMORY_ONLY        BIT(6)

/* Completion flags */
#define AI_RING_CQE_F_DONE           BIT(0)
#define AI_RING_CQE_F_PARTIAL        BIT(1)
#define AI_RING_CQE_F_RETRY          BIT(2)
#define AI_RING_CQE_F_OVERFLOW       BIT(3)
#define AI_RING_CQE_F_FAULT          BIT(4)

/* Bootstrap ring registration flags */
#define AI_RING_REG_F_POLL_KERNEL    BIT(0)
#define AI_RING_REG_F_EVENT_NOTIFY   BIT(1)
#define AI_RING_REG_F_SHARED_KV      BIT(2)
#define AI_RING_REG_F_DEVICE_LOCAL   BIT(3)

typedef struct PACKED {
    uint8_t     opcode;
    uint8_t     reserved0;
    uint16_t    flags;
    uint32_t    reserved1;

    uint64_t    user_token;
    model_id_t  model_id;
    task_id_t   task_id_hint;
    accel_id_t  accel_hint;
    uint32_t    queue_hint;

    uint64_t    input_addr;
    uint64_t    output_addr;
    uint64_t    aux_addr;

    uint32_t    input_bytes;
    uint32_t    output_bytes;
    uint32_t    max_tokens;
    uint32_t    batch_size;

    uint32_t    seq_len;
    uint32_t    deadline_ms;
    uint32_t    timeout_ms;
    uint32_t    reserved2;
} ai_submit_entry_t;

typedef struct PACKED {
    uint64_t        user_token;
    task_id_t       task_id;
    tensor_id_t     output_tensor;
    int32_t         status;

    uint32_t        flags;
    uint32_t        produced_tokens;
    uint64_t        latency_ns;
    uint64_t        bytes_written;
} ai_completion_entry_t;

typedef struct PACKED {
    uint32_t    abi_version;
    uint16_t    role;
    uint16_t    entry_size;
    uint32_t    entry_count;
    uint32_t    mask;

    volatile uint32_t head;
    volatile uint32_t tail;

    uint64_t    dropped;
    uint64_t    overflow;
    uint64_t    reserved0;
} ai_ring_header_t;

typedef struct PACKED {
    uint32_t    abi_version;
    uint16_t    flags;
    uint16_t    submit_entries;
    uint16_t    completion_entries;
    uint16_t    queue_depth_hint;

    uint64_t    submit_ring_addr;
    uint64_t    completion_ring_addr;
    uint32_t    submit_ring_bytes;
    uint32_t    completion_ring_bytes;

    uint32_t    event_word_offset;
    uint32_t    completion_word_offset;
    uint64_t    user_cookie;
} ai_ring_registration_t;

typedef struct PACKED {
    uint16_t    registered_rings;
    uint16_t    active_rings;
    uint32_t    total_notifies;
    uint32_t    max_submit_tail_observed;
    uint16_t    last_ring_id;
    uint16_t    reserved0;
    uint64_t    last_notify_ns;
    bool        any_event_notify;
    bool        any_shared_kv;
    uint16_t    reserved1;
} ai_ring_runtime_snapshot_t;

static inline uint32_t ai_ring_bytes(uint32_t entry_count, uint32_t entry_size) {
    return (uint32_t)(sizeof(ai_ring_header_t) + ((uint64_t)entry_count * entry_size));
}

static inline bool ai_ring_valid_entries(uint32_t entry_count) {
    if (entry_count < AI_RING_MIN_ENTRIES || entry_count > AI_RING_MAX_ENTRIES) {
        return false;
    }

    return (entry_count & (entry_count - 1)) == 0;
}

#endif /* _AIOS_AI_RING_H */
