/*
 * AIOS Kernel - Health Registry
 * AI-Native Operating System
 */

#ifndef _AIOS_KERNEL_HEALTH_H
#define _AIOS_KERNEL_HEALTH_H

#include <kernel/types.h>

typedef enum {
    KERNEL_SUBSYSTEM_IDT = 0,
    KERNEL_SUBSYSTEM_TIME,
    KERNEL_SUBSYSTEM_SELFTEST,
    KERNEL_SUBSYSTEM_TENSOR_MM,
    KERNEL_SUBSYSTEM_SCHED,
    KERNEL_SUBSYSTEM_ACCEL,
    KERNEL_SUBSYSTEM_PCI_PROBE,
    KERNEL_SUBSYSTEM_NETWORK,
    KERNEL_SUBSYSTEM_USB,
    KERNEL_SUBSYSTEM_STORAGE,
    KERNEL_SUBSYSTEM_SYSCALL,
    KERNEL_SUBSYSTEM_AUTONOMY,
    KERNEL_SUBSYSTEM_SLM,
    KERNEL_SUBSYSTEM_COUNT
} kernel_subsystem_id_t;

typedef enum {
    KERNEL_HEALTH_UNKNOWN = 0,
    KERNEL_HEALTH_OK = 1,
    KERNEL_HEALTH_DEGRADED = 2,
    KERNEL_HEALTH_FAILED = 3,
} kernel_health_state_t;

typedef enum {
    KERNEL_STABILITY_UNSAFE = 0,
    KERNEL_STABILITY_DEGRADED = 1,
    KERNEL_STABILITY_STABLE = 2,
} kernel_stability_t;

typedef struct {
    kernel_subsystem_id_t id;
    const char *name;
    bool required;
    bool io_path;
    kernel_health_state_t state;
    aios_status_t last_status;
    uint64_t updated_ts_ns;
} kernel_subsystem_health_t;

typedef struct {
    kernel_stability_t level;
    uint32_t ok_count;
    uint32_t degraded_count;
    uint32_t failed_count;
    uint32_t unknown_count;
    uint32_t required_failures;
    uint32_t io_degraded;
    bool autonomy_allowed;
    bool risky_io_allowed;
} kernel_health_summary_t;

aios_status_t kernel_health_init(void);
void kernel_health_mark(kernel_subsystem_id_t id, kernel_health_state_t state,
                        aios_status_t status);
const kernel_subsystem_health_t *kernel_health_get(kernel_subsystem_id_t id);
void kernel_health_get_summary(kernel_health_summary_t *out);
bool kernel_health_allows_autonomy(void);
bool kernel_health_allows_risky_io(void);
const char *kernel_health_state_name(kernel_health_state_t state);
const char *kernel_stability_name(kernel_stability_t level);
void kernel_health_dump(void);

#endif /* _AIOS_KERNEL_HEALTH_H */
