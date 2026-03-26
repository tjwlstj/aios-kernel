/*
 * AIOS Kernel - Minimal Storage Host Bootstrap
 * AI-Native Operating System
 */

#ifndef _AIOS_STORAGE_HOST_H
#define _AIOS_STORAGE_HOST_H

#include <kernel/types.h>

typedef enum {
    STORAGE_HOST_CONTROLLER_NONE = 0,
    STORAGE_HOST_CONTROLLER_IDE = 1,
    STORAGE_HOST_CONTROLLER_AHCI = 2,
    STORAGE_HOST_CONTROLLER_NVME = 3,
    STORAGE_HOST_CONTROLLER_SCSI = 4,
    STORAGE_HOST_CONTROLLER_OTHER = 5,
} storage_host_controller_kind_t;

typedef struct {
    bool present;
    bool ready;
    storage_host_controller_kind_t controller_kind;
    uint8_t subclass;
    uint8_t prog_if;
    uint8_t bus;
    uint8_t slot;
    uint8_t function;
    uint16_t vendor_id;
    uint16_t device_id;
    uint16_t io_base;
    uint64_t mmio_base;
    uint32_t pci_command;
} storage_host_info_t;

aios_status_t storage_host_init(void);
bool storage_host_ready(void);
aios_status_t storage_host_info(storage_host_info_t *out);
void storage_host_dump(void);

#endif /* _AIOS_STORAGE_HOST_H */
