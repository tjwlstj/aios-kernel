/*
 * AIOS Kernel - NodeBit Capability-Based Policy Gate
 * AI-Native Operating System
 *
 * Maintains a fixed-size table of node policy entries. Each entry binds a
 * node_id to a capability bitmask and an optional health gate. evaluate()
 * checks the requested capabilities against the allowed set and, for risky
 * capabilities, also verifies that the kernel is in a stable health state
 * before issuing a PERMIT decision.
 *
 * Thread safety: all mutations are protected by g_lock (spinlock).
 */

#include <runtime/nodebit.h>
#include <kernel/health.h>
#include <kernel/spinlock.h>
#include <kernel/time.h>
#include <drivers/serial.h>
#include <drivers/vga.h>
#include <lib/string.h>

/* -------------------------------------------------------------------------
 * State
 * ---------------------------------------------------------------------- */

static nodebit_entry_t  g_entries[NODEBIT_MAX_ENTRIES];
static spinlock_t       g_lock = SPINLOCK_INIT;
static bool             g_ready = false;

/* -------------------------------------------------------------------------
 * Internal helpers
 * ---------------------------------------------------------------------- */

static nodebit_entry_t *find_entry_locked(uint16_t node_id) {
    for (uint32_t i = 0; i < NODEBIT_MAX_ENTRIES; i++) {
        if (g_entries[i].active && g_entries[i].node_id == node_id) {
            return &g_entries[i];
        }
    }
    return NULL;
}

static nodebit_entry_t *find_free_slot_locked(void) {
    for (uint32_t i = 0; i < NODEBIT_MAX_ENTRIES; i++) {
        if (!g_entries[i].active) {
            return &g_entries[i];
        }
    }
    return NULL;
}

static bool caps_mask_valid(uint64_t caps) {
    return (caps & ~NODEBIT_CAP_ALL) == 0;
}

/* Copy at most NODEBIT_LABEL_MAX-1 characters and NUL-terminate. */
static void copy_label(char *dst, const char *src) {
    uint32_t i = 0;
    while (src[i] && i < NODEBIT_LABEL_MAX - 1) {
        dst[i] = src[i];
        i++;
    }
    dst[i] = '\0';
}

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

aios_status_t nodebit_init(void) {
    memset(g_entries, 0, sizeof(g_entries));
    g_ready = true;
    kprintf("[NODEBIT] Policy gate ready entries=0\n");
    serial_write("[NODEBIT] Policy gate ready entries=0\n");
    return AIOS_OK;
}

aios_status_t nodebit_register(uint16_t node_id, const char *label,
                                uint64_t caps_allowed, bool health_gate) {
    nodebit_entry_t *slot;

    if (!g_ready || !label || !caps_mask_valid(caps_allowed)) {
        return AIOS_ERR_INVAL;
    }

    spinlock_lock(&g_lock);

    if (find_entry_locked(node_id)) {
        spinlock_unlock(&g_lock);
        return AIOS_ERR_BUSY;   /* node_id already registered */
    }

    slot = find_free_slot_locked();
    if (!slot) {
        spinlock_unlock(&g_lock);
        return AIOS_ERR_NOMEM;
    }

    slot->node_id     = node_id;
    slot->caps_allowed = caps_allowed;
    slot->health_gate  = health_gate;
    slot->active       = true;
    copy_label(slot->label, label);

    spinlock_unlock(&g_lock);

    serial_printf("[NODEBIT] Registered node_id=%u label=%s caps=%x health_gate=%u\n",
        (uint64_t)node_id,
        (uint64_t)(uintptr_t)label,
        caps_allowed,
        (uint64_t)health_gate);
    return AIOS_OK;
}

aios_status_t nodebit_update_caps(uint16_t node_id, uint64_t caps_allowed) {
    nodebit_entry_t *entry;

    if (!g_ready || !caps_mask_valid(caps_allowed)) {
        return AIOS_ERR_INVAL;
    }

    spinlock_lock(&g_lock);
    entry = find_entry_locked(node_id);
    if (!entry) {
        spinlock_unlock(&g_lock);
        return AIOS_ERR_NODEV;
    }
    entry->caps_allowed = caps_allowed;
    spinlock_unlock(&g_lock);

    serial_printf("[NODEBIT] Updated node_id=%u caps=%x\n",
        (uint64_t)node_id, caps_allowed);
    return AIOS_OK;
}

aios_status_t nodebit_evaluate(uint16_t node_id, uint64_t caps_requested,
                                nodebit_decision_t *decision_out) {
    uint64_t caps_allowed;
    bool     health_gate;

    if (!g_ready || !decision_out) {
        return AIOS_ERR_INVAL;
    }

    memset(decision_out, 0, sizeof(*decision_out));
    decision_out->node_id       = node_id;
    decision_out->caps_requested = caps_requested;
    decision_out->ts_ns         = kernel_time_monotonic_ns();

    spinlock_lock(&g_lock);
    const nodebit_entry_t *entry = find_entry_locked(node_id);
    if (!entry) {
        spinlock_unlock(&g_lock);
        decision_out->action = NODEBIT_ACTION_DENY;
        return AIOS_ERR_NODEV;
    }
    caps_allowed = entry->caps_allowed;
    health_gate  = entry->health_gate;
    spinlock_unlock(&g_lock);

    /* Requested capabilities must be a subset of the allowed set. */
    if ((caps_requested & caps_allowed) != caps_requested) {
        decision_out->action = NODEBIT_ACTION_DENY;
        return AIOS_OK;
    }

    /* For risky capabilities, gate on kernel health when requested. */
    if (health_gate && (caps_requested & NODEBIT_CAP_RISKY_MASK)) {
        kernel_health_summary_t health;
        kernel_health_get_summary(&health);
        if (health.level != KERNEL_STABILITY_STABLE || !health.risky_io_allowed) {
            decision_out->blocked_by_health = true;
            decision_out->action = NODEBIT_ACTION_DENY;
            return AIOS_OK;
        }
    }

    decision_out->caps_granted = caps_requested;
    decision_out->action = NODEBIT_ACTION_PERMIT;
    return AIOS_OK;
}

aios_status_t nodebit_lookup(uint16_t node_id, nodebit_entry_t *out) {
    if (!g_ready || !out) {
        return AIOS_ERR_INVAL;
    }

    spinlock_lock(&g_lock);
    const nodebit_entry_t *entry = find_entry_locked(node_id);
    if (!entry) {
        spinlock_unlock(&g_lock);
        return AIOS_ERR_NODEV;
    }
    *out = *entry;
    spinlock_unlock(&g_lock);
    return AIOS_OK;
}

uint32_t nodebit_active_count(void) {
    uint32_t count = 0;
    spinlock_lock(&g_lock);
    for (uint32_t i = 0; i < NODEBIT_MAX_ENTRIES; i++) {
        if (g_entries[i].active) {
            count++;
        }
    }
    spinlock_unlock(&g_lock);
    return count;
}

uint32_t nodebit_risky_entry_count(void) {
    uint32_t count = 0;
    spinlock_lock(&g_lock);
    for (uint32_t i = 0; i < NODEBIT_MAX_ENTRIES; i++) {
        if (g_entries[i].active &&
            (g_entries[i].caps_allowed & NODEBIT_CAP_RISKY_MASK)) {
            count++;
        }
    }
    spinlock_unlock(&g_lock);
    return count;
}

const char *nodebit_action_name(nodebit_action_t action) {
    switch (action) {
        case NODEBIT_ACTION_PERMIT: return "permit";
        case NODEBIT_ACTION_DENY:   return "deny";
        case NODEBIT_ACTION_DEFER:  return "defer";
        case NODEBIT_ACTION_AUDIT:  return "audit";
        default:                    return "unknown";
    }
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
