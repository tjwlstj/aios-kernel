/*
 * AIOS Kernel - PCI Core Access Layer
 * AI-Native Operating System
 */

#include <drivers/pci_core.h>
#include <drivers/serial.h>
#include <drivers/vga.h>
#include <kernel/acpi.h>
#include <lib/string.h>

#define PCI_CONFIG_ADDR_PORT 0xCF8
#define PCI_CONFIG_DATA_PORT 0xCFC
#define PCI_CAP_ID_PM        0x01
#define PCI_CAP_ID_MSI       0x05
#define PCI_CAP_ID_MSIX      0x11
#define PCI_CAP_ID_PCIE      0x10

static pci_core_summary_t g_pci_core = {0};

static inline void outl(uint16_t port, uint32_t val) {
    __asm__ volatile ("outl %0, %1" : : "a"(val), "Nd"(port));
}

static inline uint32_t inl(uint16_t port) {
    uint32_t ret;
    __asm__ volatile ("inl %1, %0" : "=a"(ret) : "Nd"(port));
    return ret;
}

static bool pci_ecam_usable(uint8_t bus) {
    return g_pci_core.ecam_available &&
           bus >= g_pci_core.ecam_start_bus &&
           bus <= g_pci_core.ecam_end_bus &&
           g_pci_core.ecam_base < GB(4);
}

static uint64_t pci_ecam_address(uint8_t bus, uint8_t slot, uint8_t function, uint8_t offset) {
    uint64_t bus_offset = (uint64_t)(bus - g_pci_core.ecam_start_bus) << 20;
    uint64_t slot_offset = (uint64_t)slot << 15;
    uint64_t function_offset = (uint64_t)function << 12;
    return g_pci_core.ecam_base + bus_offset + slot_offset + function_offset + (offset & 0xFC);
}

static uint32_t pci_legacy_read32(uint8_t bus, uint8_t slot, uint8_t function, uint8_t offset) {
    uint32_t address = (1U << 31) |
                       ((uint32_t)bus << 16) |
                       ((uint32_t)slot << 11) |
                       ((uint32_t)function << 8) |
                       (offset & 0xFC);
    outl(PCI_CONFIG_ADDR_PORT, address);
    return inl(PCI_CONFIG_DATA_PORT);
}

static void pci_legacy_write32(uint8_t bus, uint8_t slot, uint8_t function, uint8_t offset,
                               uint32_t value) {
    uint32_t address = (1U << 31) |
                       ((uint32_t)bus << 16) |
                       ((uint32_t)slot << 11) |
                       ((uint32_t)function << 8) |
                       (offset & 0xFC);
    outl(PCI_CONFIG_ADDR_PORT, address);
    outl(PCI_CONFIG_DATA_PORT, value);
}

aios_status_t pci_core_init(void) {
    memset(&g_pci_core, 0, sizeof(g_pci_core));

    const acpi_info_t *acpi = acpi_info();
    g_pci_core.mcfg_present = acpi->mcfg_present;
    g_pci_core.ecam_available = acpi->mcfg_present &&
                                acpi->mcfg_base_addr != 0 &&
                                acpi->mcfg_base_addr < GB(4);
    g_pci_core.ecam_base = acpi->mcfg_base_addr;
    g_pci_core.ecam_segment_group = acpi->mcfg_segment_group;
    g_pci_core.ecam_start_bus = acpi->mcfg_start_bus;
    g_pci_core.ecam_end_bus = acpi->mcfg_end_bus;
    g_pci_core.access_mode = g_pci_core.ecam_available
        ? PCI_CFG_ACCESS_ECAM
        : PCI_CFG_ACCESS_LEGACY;

    serial_printf("[PCI] Core ready mode=%s ecam=%u base=%x bus=%u-%u\n",
        (uint64_t)(uintptr_t)pci_cfg_access_mode_name(g_pci_core.access_mode),
        g_pci_core.ecam_available ? 1ULL : 0ULL,
        g_pci_core.ecam_base,
        (uint64_t)g_pci_core.ecam_start_bus,
        (uint64_t)g_pci_core.ecam_end_bus);
    return AIOS_OK;
}

void pci_core_begin_enumeration(void) {
    g_pci_core.total_functions = 0;
    g_pci_core.multifunction_slots = 0;
    g_pci_core.bridges = 0;
    g_pci_core.pcie_functions = 0;
    g_pci_core.msi_capable_functions = 0;
    g_pci_core.msix_capable_functions = 0;
    g_pci_core.legacy_irq_functions = 0;
}

const pci_core_summary_t *pci_core_summary(void) {
    return &g_pci_core;
}

const char *pci_cfg_access_mode_name(pci_cfg_access_mode_t mode) {
    switch (mode) {
        case PCI_CFG_ACCESS_ECAM:   return "ecam";
        case PCI_CFG_ACCESS_LEGACY:
        default:                    return "legacy";
    }
}

bool pci_bus_in_range(uint8_t bus) {
    if (!g_pci_core.ecam_available) {
        return true;
    }
    return bus >= g_pci_core.ecam_start_bus && bus <= g_pci_core.ecam_end_bus;
}

uint32_t pci_config_read32(uint8_t bus, uint8_t slot, uint8_t function, uint8_t offset) {
    if (pci_ecam_usable(bus)) {
        volatile uint32_t *ptr =
            (volatile uint32_t *)(uintptr_t)pci_ecam_address(bus, slot, function, offset);
        return *ptr;
    }
    return pci_legacy_read32(bus, slot, function, offset);
}

uint16_t pci_config_read16(uint8_t bus, uint8_t slot, uint8_t function, uint8_t offset) {
    uint32_t val = pci_config_read32(bus, slot, function, offset);
    return (uint16_t)((val >> ((offset & 2) * 8)) & 0xFFFF);
}

uint8_t pci_config_read8(uint8_t bus, uint8_t slot, uint8_t function, uint8_t offset) {
    uint32_t val = pci_config_read32(bus, slot, function, offset);
    return (uint8_t)((val >> ((offset & 3) * 8)) & 0xFF);
}

void pci_config_write32(uint8_t bus, uint8_t slot, uint8_t function, uint8_t offset,
                        uint32_t value) {
    if (pci_ecam_usable(bus)) {
        volatile uint32_t *ptr =
            (volatile uint32_t *)(uintptr_t)pci_ecam_address(bus, slot, function, offset);
        *ptr = value;
        return;
    }
    pci_legacy_write32(bus, slot, function, offset, value);
}

void pci_config_write16(uint8_t bus, uint8_t slot, uint8_t function, uint8_t offset,
                        uint16_t value) {
    uint32_t reg = pci_config_read32(bus, slot, function, (uint8_t)(offset & 0xFC));
    uint32_t shift = (offset & 2) * 8;
    reg &= ~(0xFFFFU << shift);
    reg |= (uint32_t)value << shift;
    pci_config_write32(bus, slot, function, (uint8_t)(offset & 0xFC), reg);
}

bool pci_device_present(uint8_t bus, uint8_t slot, uint8_t function) {
    return pci_config_read16(bus, slot, function, 0x00) != 0xFFFF;
}

uint8_t pci_function_count(uint8_t bus, uint8_t slot) {
    if (!pci_device_present(bus, slot, 0)) {
        return 0;
    }
    return (pci_config_read8(bus, slot, 0, 0x0E) & BIT(7)) ? 8 : 1;
}

uint16_t pci_enable_device(uint8_t bus, uint8_t slot, uint8_t function,
                           bool io_enable, bool mem_enable, bool busmaster_enable) {
    uint16_t command = pci_config_read16(bus, slot, function, 0x04);
    if (io_enable) {
        command |= BIT(0);
    }
    if (mem_enable) {
        command |= BIT(1);
    }
    if (busmaster_enable) {
        command |= BIT(2);
    }
    pci_config_write16(bus, slot, function, 0x04, command);
    return command;
}

aios_status_t pci_read_identity(uint8_t bus, uint8_t slot, uint8_t function,
                                pci_identity_t *out) {
    if (!out) {
        return AIOS_ERR_INVAL;
    }
    memset(out, 0, sizeof(*out));

    uint32_t reg0 = pci_config_read32(bus, slot, function, 0x00);
    uint16_t vendor_id = (uint16_t)(reg0 & 0xFFFF);
    if (vendor_id == 0xFFFF) {
        return AIOS_ERR_NODEV;
    }

    uint32_t reg2 = pci_config_read32(bus, slot, function, 0x08);
    uint32_t reg3 = pci_config_read32(bus, slot, function, 0x0C);
    uint32_t regf = pci_config_read32(bus, slot, function, 0x3C);

    out->bus = bus;
    out->slot = slot;
    out->function = function;
    out->present = true;
    out->vendor_id = vendor_id;
    out->device_id = (uint16_t)((reg0 >> 16) & 0xFFFF);
    out->revision_id = (uint8_t)(reg2 & 0xFF);
    out->prog_if = (uint8_t)((reg2 >> 8) & 0xFF);
    out->subclass = (uint8_t)((reg2 >> 16) & 0xFF);
    out->class_code = (uint8_t)((reg2 >> 24) & 0xFF);
    out->command = (uint16_t)(pci_config_read32(bus, slot, function, 0x04) & 0xFFFF);
    out->status = (uint16_t)(pci_config_read32(bus, slot, function, 0x04) >> 16);
    out->header_type = (uint8_t)((reg3 >> 16) & 0xFF);
    out->multifunction = (out->header_type & BIT(7)) != 0;
    out->header_type &= 0x7F;
    out->bridge = (out->header_type == 0x01);
    out->irq_line = (uint8_t)(regf & 0xFF);
    out->irq_pin = (uint8_t)((regf >> 8) & 0xFF);
    return AIOS_OK;
}

aios_status_t pci_read_bar(uint8_t bus, uint8_t slot, uint8_t function, uint8_t bar_index,
                           pci_bar_t *out) {
    if (!out || bar_index >= PCI_BAR_COUNT) {
        return AIOS_ERR_INVAL;
    }
    memset(out, 0, sizeof(*out));

    uint8_t offset = (uint8_t)(0x10 + bar_index * 4);
    uint32_t low = pci_config_read32(bus, slot, function, offset);
    if (low == 0) {
        return AIOS_OK;
    }

    out->present = true;
    out->io_space = (low & BIT(0)) != 0;
    if (out->io_space) {
        out->base = (uint64_t)(low & 0xFFFFFFFCU);
        return AIOS_OK;
    }

    out->mem_space = true;
    out->prefetchable = (low & BIT(3)) != 0;
    out->is_64bit = ((low >> 1) & 0x3) == 0x2;
    out->base = (uint64_t)(low & 0xFFFFFFF0U);
    if (out->is_64bit && bar_index + 1 < PCI_BAR_COUNT) {
        uint32_t high = pci_config_read32(bus, slot, function, (uint8_t)(offset + 4));
        out->base |= (uint64_t)high << 32;
    }

    return AIOS_OK;
}

aios_status_t pci_probe_capabilities(uint8_t bus, uint8_t slot, uint8_t function,
                                     pci_capabilities_t *out) {
    if (!out) {
        return AIOS_ERR_INVAL;
    }
    memset(out, 0, sizeof(*out));

    uint16_t status = (uint16_t)(pci_config_read32(bus, slot, function, 0x04) >> 16);
    out->has_cap_list = (status & BIT(4)) != 0;
    if (!out->has_cap_list) {
        return AIOS_OK;
    }

    uint8_t cap_ptr = pci_config_read8(bus, slot, function, 0x34) & 0xFC;
    out->first_cap_ptr = cap_ptr;

    for (uint32_t guard = 0; guard < 48 && cap_ptr >= 0x40; guard++) {
        uint8_t cap_id = pci_config_read8(bus, slot, function, cap_ptr);
        uint8_t next = pci_config_read8(bus, slot, function, (uint8_t)(cap_ptr + 1)) & 0xFC;

        if (cap_id == PCI_CAP_ID_PM) {
            out->has_pm = true;
        } else if (cap_id == PCI_CAP_ID_MSI) {
            out->has_msi = true;
        } else if (cap_id == PCI_CAP_ID_MSIX) {
            out->has_msix = true;
        } else if (cap_id == PCI_CAP_ID_PCIE) {
            uint32_t link_status_reg = pci_config_read32(bus, slot, function,
                (uint8_t)(cap_ptr + 0x14));
            uint16_t link_status = (uint16_t)(link_status_reg >> 16);
            out->has_pcie = true;
            out->pcie_link_speed = (uint8_t)(link_status & 0x0F);
            out->pcie_link_width = (uint8_t)((link_status >> 4) & 0x3F);
        }

        if (next == 0 || next == cap_ptr) {
            break;
        }
        cap_ptr = next;
    }

    return AIOS_OK;
}

void pci_core_account_function(const pci_identity_t *identity,
                               const pci_capabilities_t *caps) {
    if (!identity || !identity->present) {
        return;
    }

    g_pci_core.total_functions++;
    if (identity->multifunction && identity->function == 0) {
        g_pci_core.multifunction_slots++;
    }
    if (identity->bridge) {
        g_pci_core.bridges++;
    }
    if (identity->irq_pin != 0) {
        g_pci_core.legacy_irq_functions++;
    }

    if (!caps) {
        return;
    }
    if (caps->has_pcie) {
        g_pci_core.pcie_functions++;
    }
    if (caps->has_msi) {
        g_pci_core.msi_capable_functions++;
    }
    if (caps->has_msix) {
        g_pci_core.msix_capable_functions++;
    }
}

void pci_core_dump(void) {
    kprintf("\n=== PCI Core ===\n");
    kprintf("Access: mode=%s ecam=%u base=%x seg=%u bus=%u-%u\n",
        (uint64_t)(uintptr_t)pci_cfg_access_mode_name(g_pci_core.access_mode),
        (uint64_t)g_pci_core.ecam_available,
        g_pci_core.ecam_base,
        (uint64_t)g_pci_core.ecam_segment_group,
        (uint64_t)g_pci_core.ecam_start_bus,
        (uint64_t)g_pci_core.ecam_end_bus);
    kprintf("Inventory: total=%u multi=%u bridges=%u pcie=%u msi=%u msix=%u irq=%u\n",
        g_pci_core.total_functions,
        g_pci_core.multifunction_slots,
        g_pci_core.bridges,
        g_pci_core.pcie_functions,
        g_pci_core.msi_capable_functions,
        g_pci_core.msix_capable_functions,
        g_pci_core.legacy_irq_functions);
    kprintf("================\n");
}
