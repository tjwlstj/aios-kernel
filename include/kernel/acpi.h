/*
 * AIOS Kernel - Minimal ACPI Fabric Parser
 * AI-Native Operating System
 */

#ifndef _AIOS_ACPI_H
#define _AIOS_ACPI_H

#include <kernel/types.h>

typedef struct {
    bool rsdp_found;
    bool checksum_valid;
    bool root_table_valid;
    bool xsdt_present;
    bool rsdt_present;
    bool mcfg_present;
    bool madt_present;
    bool fadt_present;
    uint8_t revision;
    uint32_t root_table_entries;
    uint64_t rsdp_copy_addr;
    uint64_t rsdt_addr;
    uint64_t xsdt_addr;
    uint64_t mcfg_addr;
    uint64_t madt_addr;
    uint64_t fadt_addr;
    uint64_t mcfg_base_addr;
    uint16_t mcfg_segment_group;
    uint8_t mcfg_start_bus;
    uint8_t mcfg_end_bus;
} acpi_info_t;

aios_status_t acpi_init(uint64_t multiboot_magic, uint64_t multiboot_info);
const acpi_info_t *acpi_info(void);
bool acpi_ready(void);
void acpi_dump(void);

#endif /* _AIOS_ACPI_H */
