/*
 * AIOS Kernel - NodeBit Capability-Based Policy Gate
 * AI-Native Operating System
 *
 * Provides a generic capability-based access control gate for AI workloads.
 * Each registered "node" carries a uint64_t capability bitmask; the gate
 * checks requested capabilities against the node's allowed set and the
 * current kernel health state before issuing a permit or deny decision.
 *
 * This module is distinct from slm_nodebit (hardware driver policy).
 * slm_nodebit gates SLM hardware plans; this module gates AI agent
 * capability access at the syscall / autonomy layer.
 */

#ifndef _AIOS_RUNTIME_NODEBIT_H
#define _AIOS_RUNTIME_NODEBIT_H

#include <kernel/types.h>
#include <kernel/health.h>

/* -------------------------------------------------------------------------
 * Configuration
 * ---------------------------------------------------------------------- */

#define NODEBIT_MAX_ENTRIES   64U
#define NODEBIT_LABEL_MAX     32U

/* -------------------------------------------------------------------------
 * Capability bitmask definitions
 * ---------------------------------------------------------------------- */

#define NODEBIT_CAP_OBSERVE       BIT(0)   /* Read-only observe / info */
#define NODEBIT_CAP_INFER         BIT(1)   /* Submit inference requests */
#define NODEBIT_CAP_TRAIN         BIT(2)   /* Initiate model training */
#define NODEBIT_CAP_PLAN_SUBMIT   BIT(3)   /* Submit SLM hardware plans */
#define NODEBIT_CAP_PLAN_APPLY    BIT(4)   /* Apply validated SLM plans (risky) */
#define NODEBIT_CAP_AUTONOMY      BIT(5)   /* Autonomy control actions (risky) */
#define NODEBIT_CAP_IO_READ       BIT(6)   /* Safe I/O read operations */
#define NODEBIT_CAP_IO_WRITE      BIT(7)   /* Safe I/O write operations */
#define NODEBIT_CAP_RISKY_IO      BIT(8)   /* Risky I/O (net tx, storage write) */
#define NODEBIT_CAP_DRIVER_RESET  BIT(9)   /* Driver-level reset (risky) */
#define NODEBIT_CAP_SYSCALL_ADMIN BIT(10)  /* Administrative syscall operations */
#define NODEBIT_CAP_ALL           ((BIT(11)) - 1)

/* Mask of capabilities that require STABLE kernel health and risky_io_allowed */
#define NODEBIT_CAP_RISKY_MASK \
    (NODEBIT_CAP_RISKY_IO | NODEBIT_CAP_DRIVER_RESET | \
     NODEBIT_CAP_PLAN_APPLY | NODEBIT_CAP_AUTONOMY)

/* -------------------------------------------------------------------------
 * Types
 * ---------------------------------------------------------------------- */

typedef enum {
    NODEBIT_ACTION_PERMIT = 0,  /* All requested capabilities granted */
    NODEBIT_ACTION_DENY   = 1,  /* One or more capabilities refused */
    NODEBIT_ACTION_DEFER  = 2,  /* Decision deferred (health transient) */
    NODEBIT_ACTION_AUDIT  = 3,  /* Permitted but flagged for audit */
} nodebit_action_t;

typedef struct {
    uint16_t  node_id;
    char      label[NODEBIT_LABEL_MAX];
    uint64_t  caps_allowed;   /* Permitted capability bitmask */
    bool      health_gate;    /* Block RISKY_MASK when health < stable */
    bool      active;         /* Whether this entry slot is occupied */
} nodebit_entry_t;

typedef struct {
    nodebit_action_t action;
    uint16_t         node_id;
    uint64_t         caps_requested;
    uint64_t         caps_granted;
    bool             blocked_by_health;
    uint64_t         ts_ns;
} nodebit_decision_t;

/* -------------------------------------------------------------------------
 * Syscall argument structures
 * ---------------------------------------------------------------------- */

typedef struct {
    uint16_t  node_id;
    char      label[NODEBIT_LABEL_MAX];
    uint64_t  caps_allowed;
    bool      health_gate;
} syscall_nodebit_register_t;

typedef struct {
    uint16_t  node_id;
    uint64_t  caps_allowed;
} syscall_nodebit_update_t;

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

aios_status_t    nodebit_init(void);
aios_status_t    nodebit_register(uint16_t node_id, const char *label,
                                   uint64_t caps_allowed, bool health_gate);
aios_status_t    nodebit_update_caps(uint16_t node_id, uint64_t caps_allowed);
aios_status_t    nodebit_evaluate(uint16_t node_id, uint64_t caps_requested,
                                   nodebit_decision_t *decision_out);
aios_status_t    nodebit_lookup(uint16_t node_id, nodebit_entry_t *out);
uint32_t         nodebit_active_count(void);
uint32_t         nodebit_risky_entry_count(void);
const char      *nodebit_action_name(nodebit_action_t action);

#endif /* _AIOS_RUNTIME_NODEBIT_H */
