/*
 * AIOS Kernel - Minimal Peripheral Probe Layer
 * AI-Native Operating System
 */

#ifndef _AIOS_PLATFORM_PROBE_H
#define _AIOS_PLATFORM_PROBE_H

#include <kernel/types.h>
#include <kernel/selftest.h>

#define MAX_PLATFORM_DEVICES 32

typedef enum {
    PLATFORM_DEVICE_UNKNOWN = 0,
    PLATFORM_DEVICE_ETHERNET,
    PLATFORM_DEVICE_WIRELESS,
    PLATFORM_DEVICE_BLUETOOTH,
    PLATFORM_DEVICE_USB,
    PLATFORM_DEVICE_STORAGE,
} platform_device_kind_t;

typedef struct {
    uint16_t vendor_id;
    uint16_t device_id;
    uint8_t class_code;
    uint8_t subclass;
    uint8_t prog_if;
    uint8_t bus;
    uint8_t slot;
    uint8_t function;
    bool pcie_capable;
    uint8_t pcie_link_speed;
    uint8_t pcie_link_width;
    uint8_t init_priority;
    platform_device_kind_t kind;
    char name[64];
} platform_device_t;

typedef struct {
    uint32_t total_pci_devices;
    uint32_t matched_devices;
    uint32_t ethernet_count;
    uint32_t wireless_count;
    uint32_t bluetooth_count;
    uint32_t usb_count;
    uint32_t storage_count;
    boot_perf_tier_t boot_tier;
} platform_probe_summary_t;

aios_status_t platform_probe_init(void);
uint32_t platform_probe_count(void);
const platform_device_t *platform_probe_get(uint32_t index);
const platform_probe_summary_t *platform_probe_summary(void);
void platform_probe_dump(void);

#endif /* _AIOS_PLATFORM_PROBE_H */
