/*
 * AIOS Kernel - PCI Core Access Layer
 * AI-Native Operating System
 */

#ifndef _AIOS_PCI_CORE_H
#define _AIOS_PCI_CORE_H

#include <kernel/types.h>

#define PCI_BAR_COUNT 6

typedef enum {
    PCI_CFG_ACCESS_LEGACY = 0,
    PCI_CFG_ACCESS_ECAM = 1,
} pci_cfg_access_mode_t;

typedef struct {
    uint8_t bus;
    uint8_t slot;
    uint8_t function;
    bool present;
    bool multifunction;
    bool bridge;
    uint16_t vendor_id;
    uint16_t device_id;
    uint16_t command;
    uint16_t status;
    uint8_t class_code;
    uint8_t subclass;
    uint8_t prog_if;
    uint8_t revision_id;
    uint8_t header_type;
    uint8_t irq_line;
    uint8_t irq_pin;
} pci_identity_t;

typedef struct {
    bool present;
    bool io_space;
    bool mem_space;
    bool prefetchable;
    bool is_64bit;
    uint64_t base;
} pci_bar_t;

typedef struct {
    bool has_cap_list;
    bool has_pcie;
    bool has_msi;
    bool has_msix;
    bool has_pm;
    uint8_t first_cap_ptr;
    uint8_t pcie_link_speed;
    uint8_t pcie_link_width;
} pci_capabilities_t;

typedef struct {
    pci_cfg_access_mode_t access_mode;
    bool ecam_available;
    bool mcfg_present;
    uint64_t ecam_base;
    uint16_t ecam_segment_group;
    uint8_t ecam_start_bus;
    uint8_t ecam_end_bus;
    uint32_t total_functions;
    uint32_t multifunction_slots;
    uint32_t bridges;
    uint32_t pcie_functions;
    uint32_t msi_capable_functions;
    uint32_t msix_capable_functions;
    uint32_t legacy_irq_functions;
} pci_core_summary_t;

aios_status_t pci_core_init(void);
void pci_core_begin_enumeration(void);
const pci_core_summary_t *pci_core_summary(void);
const char *pci_cfg_access_mode_name(pci_cfg_access_mode_t mode);
bool pci_bus_in_range(uint8_t bus);
bool pci_device_present(uint8_t bus, uint8_t slot, uint8_t function);
uint8_t pci_function_count(uint8_t bus, uint8_t slot);
uint32_t pci_config_read32(uint8_t bus, uint8_t slot, uint8_t function, uint8_t offset);
uint16_t pci_config_read16(uint8_t bus, uint8_t slot, uint8_t function, uint8_t offset);
uint8_t pci_config_read8(uint8_t bus, uint8_t slot, uint8_t function, uint8_t offset);
void pci_config_write32(uint8_t bus, uint8_t slot, uint8_t function, uint8_t offset,
                        uint32_t value);
void pci_config_write16(uint8_t bus, uint8_t slot, uint8_t function, uint8_t offset,
                        uint16_t value);
uint16_t pci_enable_device(uint8_t bus, uint8_t slot, uint8_t function,
                           bool io_enable, bool mem_enable, bool busmaster_enable);
aios_status_t pci_read_identity(uint8_t bus, uint8_t slot, uint8_t function,
                                pci_identity_t *out);
aios_status_t pci_read_bar(uint8_t bus, uint8_t slot, uint8_t function, uint8_t bar_index,
                           pci_bar_t *out);
aios_status_t pci_probe_capabilities(uint8_t bus, uint8_t slot, uint8_t function,
                                     pci_capabilities_t *out);
void pci_core_account_function(const pci_identity_t *identity,
                               const pci_capabilities_t *caps);
void pci_core_dump(void);

#endif /* _AIOS_PCI_CORE_H */
