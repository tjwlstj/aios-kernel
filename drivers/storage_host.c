/*
 * AIOS Kernel - Minimal Storage Host Bootstrap
 * AI-Native Operating System
 */

#include <drivers/storage_host.h>
#include <drivers/platform_probe.h>
#include <drivers/pci_core.h>
#include <drivers/serial.h>
#include <drivers/vga.h>
#include <lib/string.h>

#define PCI_BAR0_OFFSET    0x10

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
    uint16_t primary_cmd_base;
    uint16_t primary_ctrl_base;
    uint16_t secondary_cmd_base;
    uint16_t secondary_ctrl_base;
    uint64_t mmio_base;
    uint32_t pci_command;
    uint8_t primary_status;
    uint8_t secondary_status;
    bool primary_channel_live;
    bool secondary_channel_live;
    aios_status_t last_init_status;
} storage_host_state_t;

static storage_host_state_t g_storage_host = {0};

static inline uint8_t inb(uint16_t port) {
    uint8_t ret;
    __asm__ volatile ("inb %1, %0" : "=a"(ret) : "Nd"(port));
    return ret;
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

static uint16_t pci_bar_io_base(uint8_t bus, uint8_t slot, uint8_t function,
                                uint8_t offset) {
    pci_bar_t bar;
    uint8_t bar_index = (uint8_t)((offset - PCI_BAR0_OFFSET) / 4);
    if (pci_read_bar(bus, slot, function, bar_index, &bar) != AIOS_OK ||
        !bar.present ||
        !bar.io_space) {
        return 0;
    }
    return (uint16_t)bar.base;
}

static void storage_host_configure_ide_channels(void) {
    bool primary_native = (g_storage_host.prog_if & BIT(0)) != 0;
    bool secondary_native = (g_storage_host.prog_if & BIT(2)) != 0;

    g_storage_host.primary_cmd_base = primary_native
        ? pci_bar_io_base(g_storage_host.bus, g_storage_host.slot,
                          g_storage_host.function, PCI_BAR0_OFFSET)
        : 0x1F0;
    g_storage_host.primary_ctrl_base = primary_native
        ? pci_bar_io_base(g_storage_host.bus, g_storage_host.slot,
                          g_storage_host.function, PCI_BAR0_OFFSET + 4)
        : 0x3F6;
    g_storage_host.secondary_cmd_base = secondary_native
        ? pci_bar_io_base(g_storage_host.bus, g_storage_host.slot,
                          g_storage_host.function, PCI_BAR0_OFFSET + 8)
        : 0x170;
    g_storage_host.secondary_ctrl_base = secondary_native
        ? pci_bar_io_base(g_storage_host.bus, g_storage_host.slot,
                          g_storage_host.function, PCI_BAR0_OFFSET + 12)
        : 0x376;
}

static void storage_host_probe_ide_channels(void) {
    g_storage_host.primary_status = 0xFF;
    g_storage_host.secondary_status = 0xFF;
    g_storage_host.primary_channel_live = false;
    g_storage_host.secondary_channel_live = false;

    if (g_storage_host.primary_cmd_base != 0) {
        g_storage_host.primary_status = inb((uint16_t)(g_storage_host.primary_cmd_base + 7));
        g_storage_host.primary_channel_live = (g_storage_host.primary_status != 0xFF);
    }

    if (g_storage_host.secondary_cmd_base != 0) {
        g_storage_host.secondary_status = inb((uint16_t)(g_storage_host.secondary_cmd_base + 7));
        g_storage_host.secondary_channel_live = (g_storage_host.secondary_status != 0xFF);
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
        g_storage_host.last_init_status = AIOS_ERR_NODEV;
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

    g_storage_host.pci_command = pci_enable_device(g_storage_host.bus, g_storage_host.slot,
        g_storage_host.function, true, true, true);

    for (uint32_t i = 0; i < PCI_BAR_COUNT; i++) {
        pci_bar_t bar;
        if (pci_read_bar(g_storage_host.bus, g_storage_host.slot, g_storage_host.function,
                (uint8_t)i, &bar) != AIOS_OK || !bar.present) {
            continue;
        }
        if (bar.io_space && g_storage_host.io_base == 0) {
            g_storage_host.io_base = (uint16_t)bar.base;
        } else if (bar.mem_space && g_storage_host.mmio_base == 0) {
            g_storage_host.mmio_base = bar.base;
        }
        if (bar.is_64bit && i + 1 < PCI_BAR_COUNT) {
            i++;
        }
    }

    if (g_storage_host.controller_kind == STORAGE_HOST_CONTROLLER_IDE) {
        storage_host_configure_ide_channels();
        storage_host_probe_ide_channels();
    }

    g_storage_host.ready = (g_storage_host.io_base != 0 || g_storage_host.mmio_base != 0);
    g_storage_host.last_init_status = g_storage_host.ready ? AIOS_OK : AIOS_ERR_IO;

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
    if (g_storage_host.controller_kind == STORAGE_HOST_CONTROLLER_IDE) {
        serial_printf("[STO] IDE channels primary=%x/%x status=%x live=%u secondary=%x/%x status=%x live=%u\n",
            (uint64_t)g_storage_host.primary_cmd_base,
            (uint64_t)g_storage_host.primary_ctrl_base,
            (uint64_t)g_storage_host.primary_status,
            g_storage_host.primary_channel_live ? 1ULL : 0ULL,
            (uint64_t)g_storage_host.secondary_cmd_base,
            (uint64_t)g_storage_host.secondary_ctrl_base,
            (uint64_t)g_storage_host.secondary_status,
            g_storage_host.secondary_channel_live ? 1ULL : 0ULL);
    }

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
    out->primary_cmd_base = g_storage_host.primary_cmd_base;
    out->primary_ctrl_base = g_storage_host.primary_ctrl_base;
    out->secondary_cmd_base = g_storage_host.secondary_cmd_base;
    out->secondary_ctrl_base = g_storage_host.secondary_ctrl_base;
    out->mmio_base = g_storage_host.mmio_base;
    out->pci_command = g_storage_host.pci_command;
    out->primary_status = g_storage_host.primary_status;
    out->secondary_status = g_storage_host.secondary_status;
    out->primary_channel_live = g_storage_host.primary_channel_live;
    out->secondary_channel_live = g_storage_host.secondary_channel_live;
    out->last_init_status = g_storage_host.last_init_status;
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
    serial_printf("[STO] channels primary=%x/%x status=%x live=%u secondary=%x/%x status=%x live=%u\n",
        (uint64_t)g_storage_host.primary_cmd_base,
        (uint64_t)g_storage_host.primary_ctrl_base,
        (uint64_t)g_storage_host.primary_status,
        g_storage_host.primary_channel_live ? 1ULL : 0ULL,
        (uint64_t)g_storage_host.secondary_cmd_base,
        (uint64_t)g_storage_host.secondary_ctrl_base,
        (uint64_t)g_storage_host.secondary_status,
        g_storage_host.secondary_channel_live ? 1ULL : 0ULL);
    serial_printf("[STO] last_init_status=%d\n",
        (int64_t)g_storage_host.last_init_status);
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
