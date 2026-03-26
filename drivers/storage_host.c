/*
 * AIOS Kernel - Minimal Storage Host Bootstrap
 * AI-Native Operating System
 */

#include <drivers/storage_host.h>
#include <drivers/platform_probe.h>
#include <drivers/serial.h>
#include <drivers/vga.h>
#include <lib/string.h>

#define PCI_CONFIG_ADDR    0xCF8
#define PCI_CONFIG_DATA    0xCFC

#define PCI_COMMAND_OFFSET 0x04
#define PCI_BAR0_OFFSET    0x10
#define PCI_BAR_COUNT      6
#define PCI_CMD_IO         0x1
#define PCI_CMD_MEM        0x2
#define PCI_CMD_BUSMASTER  0x4

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
} storage_host_state_t;

static storage_host_state_t g_storage_host = {0};

static inline void outl(uint16_t port, uint32_t val) {
    __asm__ volatile ("outl %0, %1" : : "a"(val), "Nd"(port));
}

static inline uint32_t inl(uint16_t port) {
    uint32_t ret;
    __asm__ volatile ("inl %1, %0" : "=a"(ret) : "Nd"(port));
    return ret;
}

static uint32_t pci_config_read(uint8_t bus, uint8_t slot, uint8_t function,
                                uint8_t offset) {
    uint32_t address = (1U << 31) |
                       ((uint32_t)bus << 16) |
                       ((uint32_t)slot << 11) |
                       ((uint32_t)function << 8) |
                       (offset & 0xFC);
    outl(PCI_CONFIG_ADDR, address);
    return inl(PCI_CONFIG_DATA);
}

static void pci_config_write(uint8_t bus, uint8_t slot, uint8_t function,
                             uint8_t offset, uint32_t value) {
    uint32_t address = (1U << 31) |
                       ((uint32_t)bus << 16) |
                       ((uint32_t)slot << 11) |
                       ((uint32_t)function << 8) |
                       (offset & 0xFC);
    outl(PCI_CONFIG_ADDR, address);
    outl(PCI_CONFIG_DATA, value);
}

static storage_host_controller_kind_t classify_controller(uint8_t subclass,
                                                          uint8_t prog_if,
                                                          const char **label) {
    (void)prog_if;

    switch (subclass) {
        case 0x01:
            *label = "IDE";
            return STORAGE_HOST_CONTROLLER_IDE;
        case 0x00:
            *label = "SCSI";
            return STORAGE_HOST_CONTROLLER_SCSI;
        case 0x06:
            *label = "AHCI";
            return STORAGE_HOST_CONTROLLER_AHCI;
        case 0x08:
            *label = "NVMe";
            return STORAGE_HOST_CONTROLLER_NVME;
        default:
            *label = "Storage";
            return STORAGE_HOST_CONTROLLER_OTHER;
    }
}

aios_status_t storage_host_init(void) {
    memset(&g_storage_host, 0, sizeof(g_storage_host));

    const platform_device_t *candidate = NULL;
    for (uint32_t i = 0; i < platform_probe_count(); i++) {
        const platform_device_t *dev = platform_probe_get(i);
        if (!dev || dev->kind != PLATFORM_DEVICE_STORAGE) {
            continue;
        }
        candidate = dev;
        break;
    }

    if (!candidate) {
        kprintf("    No storage controller found.\n");
        serial_write("[STO] No storage controller found\n");
        return AIOS_OK;
    }

    g_storage_host.present = true;
    g_storage_host.subclass = candidate->subclass;
    g_storage_host.prog_if = candidate->prog_if;
    g_storage_host.bus = candidate->bus;
    g_storage_host.slot = candidate->slot;
    g_storage_host.function = candidate->function;
    g_storage_host.vendor_id = candidate->vendor_id;
    g_storage_host.device_id = candidate->device_id;

    const char *label = NULL;
    g_storage_host.controller_kind = classify_controller(candidate->subclass,
                                                         candidate->prog_if,
                                                         &label);

    uint32_t command_reg = pci_config_read(g_storage_host.bus, g_storage_host.slot,
                                           g_storage_host.function, PCI_COMMAND_OFFSET);
    command_reg |= (PCI_CMD_IO | PCI_CMD_MEM | PCI_CMD_BUSMASTER);
    pci_config_write(g_storage_host.bus, g_storage_host.slot, g_storage_host.function,
                     PCI_COMMAND_OFFSET, command_reg);
    g_storage_host.pci_command = command_reg & 0xFFFF;

    for (uint32_t i = 0; i < PCI_BAR_COUNT; i++) {
        uint32_t bar = pci_config_read(g_storage_host.bus, g_storage_host.slot,
                                       g_storage_host.function,
                                       (uint8_t)(PCI_BAR0_OFFSET + i * 4));
        if (!bar) {
            continue;
        }
        if ((bar & 0x1) && g_storage_host.io_base == 0) {
            g_storage_host.io_base = (uint16_t)(bar & 0xFFFCU);
        } else if (((bar & 0x1) == 0) && g_storage_host.mmio_base == 0) {
            g_storage_host.mmio_base = (uint64_t)(bar & 0xFFFFFFF0U);
        }
    }

    g_storage_host.ready = (g_storage_host.io_base != 0 || g_storage_host.mmio_base != 0);

    kprintf("    Storage %s host: pci=%u:%u.%u cmd=0x%x mmio=0x%x io=0x%x\n",
        (uint64_t)(uintptr_t)label,
        (uint64_t)g_storage_host.bus,
        (uint64_t)g_storage_host.slot,
        (uint64_t)g_storage_host.function,
        (uint64_t)g_storage_host.pci_command,
        (uint64_t)g_storage_host.mmio_base,
        (uint64_t)g_storage_host.io_base);
    serial_printf("[STO] %s ready=%u vendor=%x device=%x pci=%u:%u.%u cmd=%x mmio=%x io=%x\n",
        (uint64_t)(uintptr_t)label,
        g_storage_host.ready ? 1ULL : 0ULL,
        (uint64_t)g_storage_host.vendor_id,
        (uint64_t)g_storage_host.device_id,
        (uint64_t)g_storage_host.bus,
        (uint64_t)g_storage_host.slot,
        (uint64_t)g_storage_host.function,
        (uint64_t)g_storage_host.pci_command,
        (uint64_t)g_storage_host.mmio_base,
        (uint64_t)g_storage_host.io_base);

    return AIOS_OK;
}

bool storage_host_ready(void) {
    return g_storage_host.ready;
}

aios_status_t storage_host_info(storage_host_info_t *out) {
    if (!out) {
        return AIOS_ERR_INVAL;
    }

    out->present = g_storage_host.present;
    out->ready = g_storage_host.ready;
    out->controller_kind = g_storage_host.controller_kind;
    out->subclass = g_storage_host.subclass;
    out->prog_if = g_storage_host.prog_if;
    out->bus = g_storage_host.bus;
    out->slot = g_storage_host.slot;
    out->function = g_storage_host.function;
    out->vendor_id = g_storage_host.vendor_id;
    out->device_id = g_storage_host.device_id;
    out->io_base = g_storage_host.io_base;
    out->mmio_base = g_storage_host.mmio_base;
    out->pci_command = g_storage_host.pci_command;
    return AIOS_OK;
}

void storage_host_dump(void) {
    if (!g_storage_host.present) {
        serial_write("[STO] Host dump: controller not present\n");
        return;
    }

    serial_printf("[STO] dump type=%u ready=%u vendor=%x device=%x pci=%u:%u.%u mmio=%x io=%x\n",
        (uint64_t)g_storage_host.controller_kind,
        g_storage_host.ready ? 1ULL : 0ULL,
        (uint64_t)g_storage_host.vendor_id,
        (uint64_t)g_storage_host.device_id,
        (uint64_t)g_storage_host.bus,
        (uint64_t)g_storage_host.slot,
        (uint64_t)g_storage_host.function,
        (uint64_t)g_storage_host.mmio_base,
        (uint64_t)g_storage_host.io_base);
}
