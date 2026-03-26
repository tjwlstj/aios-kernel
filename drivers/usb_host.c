/*
 * AIOS Kernel - Minimal USB Host Bootstrap
 * AI-Native Operating System
 */

#include <drivers/usb_host.h>
#include <drivers/platform_probe.h>
#include <drivers/serial.h>
#include <drivers/vga.h>
#include <lib/string.h>

#define PCI_CONFIG_ADDR    0xCF8
#define PCI_CONFIG_DATA    0xCFC

#define PCI_COMMAND_OFFSET 0x04
#define PCI_BAR0_OFFSET    0x10
#define PCI_BAR1_OFFSET    0x14
#define PCI_CMD_IO         0x1
#define PCI_CMD_MEM        0x2
#define PCI_CMD_BUSMASTER  0x4

typedef struct {
    bool present;
    bool ready;
    usb_host_controller_kind_t controller_kind;
    uint8_t prog_if;
    uint8_t bus;
    uint8_t slot;
    uint8_t function;
    uint16_t vendor_id;
    uint16_t device_id;
    uint16_t io_base;
    uint64_t mmio_base;
    uint32_t pci_command;
} usb_host_state_t;

static usb_host_state_t g_usb_host = {0};

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

static usb_host_controller_kind_t classify_controller(uint8_t prog_if,
                                                      const char **label) {
    switch (prog_if) {
        case 0x00:
            *label = "UHCI";
            return USB_HOST_CONTROLLER_UHCI;
        case 0x10:
            *label = "OHCI";
            return USB_HOST_CONTROLLER_OHCI;
        case 0x20:
            *label = "EHCI";
            return USB_HOST_CONTROLLER_EHCI;
        case 0x30:
            *label = "XHCI";
            return USB_HOST_CONTROLLER_XHCI;
        default:
            *label = "USB";
            return USB_HOST_CONTROLLER_NONE;
    }
}

aios_status_t usb_host_init(void) {
    memset(&g_usb_host, 0, sizeof(g_usb_host));

    const platform_device_t *candidate = NULL;
    for (uint32_t i = 0; i < platform_probe_count(); i++) {
        const platform_device_t *dev = platform_probe_get(i);
        if (!dev || dev->kind != PLATFORM_DEVICE_USB) {
            continue;
        }
        candidate = dev;
        break;
    }

    if (!candidate) {
        kprintf("    No USB host controller found.\n");
        serial_write("[USB] No USB host controller found\n");
        return AIOS_OK;
    }

    g_usb_host.present = true;
    g_usb_host.bus = candidate->bus;
    g_usb_host.slot = candidate->slot;
    g_usb_host.function = candidate->function;
    g_usb_host.vendor_id = candidate->vendor_id;
    g_usb_host.device_id = candidate->device_id;
    g_usb_host.prog_if = candidate->prog_if;

    const char *label = NULL;
    g_usb_host.controller_kind = classify_controller(candidate->prog_if, &label);

    uint32_t command_reg = pci_config_read(g_usb_host.bus, g_usb_host.slot,
                                           g_usb_host.function, PCI_COMMAND_OFFSET);
    command_reg |= (PCI_CMD_IO | PCI_CMD_MEM | PCI_CMD_BUSMASTER);
    pci_config_write(g_usb_host.bus, g_usb_host.slot, g_usb_host.function,
                     PCI_COMMAND_OFFSET, command_reg);
    g_usb_host.pci_command = command_reg & 0xFFFF;

    uint32_t bar0 = pci_config_read(g_usb_host.bus, g_usb_host.slot,
                                    g_usb_host.function, PCI_BAR0_OFFSET);
    uint32_t bar1 = pci_config_read(g_usb_host.bus, g_usb_host.slot,
                                    g_usb_host.function, PCI_BAR1_OFFSET);

    if (bar0 && ((bar0 & 0x1) == 0)) {
        g_usb_host.mmio_base = (uint64_t)(bar0 & 0xFFFFFFF0U);
    } else if (bar1 && ((bar1 & 0x1) == 0)) {
        g_usb_host.mmio_base = (uint64_t)(bar1 & 0xFFFFFFF0U);
    }

    if (bar0 & 0x1) {
        g_usb_host.io_base = (uint16_t)(bar0 & 0xFFFCU);
    } else if (bar1 & 0x1) {
        g_usb_host.io_base = (uint16_t)(bar1 & 0xFFFCU);
    }

    g_usb_host.ready = (g_usb_host.mmio_base != 0 || g_usb_host.io_base != 0);

    kprintf("    USB %s host: pci=%u:%u cmd=0x%x mmio=0x%x io=0x%x\n",
        (uint64_t)(uintptr_t)label,
        (uint64_t)g_usb_host.bus,
        (uint64_t)g_usb_host.slot,
        (uint64_t)g_usb_host.pci_command,
        (uint64_t)g_usb_host.mmio_base,
        (uint64_t)g_usb_host.io_base);
    serial_printf("[USB] %s ready=%u vendor=%x device=%x pci=%u:%u cmd=%x mmio=%x io=%x\n",
        (uint64_t)(uintptr_t)label,
        g_usb_host.ready ? 1ULL : 0ULL,
        (uint64_t)g_usb_host.vendor_id,
        (uint64_t)g_usb_host.device_id,
        (uint64_t)g_usb_host.bus,
        (uint64_t)g_usb_host.slot,
        (uint64_t)g_usb_host.pci_command,
        (uint64_t)g_usb_host.mmio_base,
        (uint64_t)g_usb_host.io_base);

    return AIOS_OK;
}

bool usb_host_ready(void) {
    return g_usb_host.ready;
}

aios_status_t usb_host_info(usb_host_info_t *out) {
    if (!out) {
        return AIOS_ERR_INVAL;
    }

    out->present = g_usb_host.present;
    out->ready = g_usb_host.ready;
    out->controller_kind = g_usb_host.controller_kind;
    out->prog_if = g_usb_host.prog_if;
    out->bus = g_usb_host.bus;
    out->slot = g_usb_host.slot;
    out->function = g_usb_host.function;
    out->vendor_id = g_usb_host.vendor_id;
    out->device_id = g_usb_host.device_id;
    out->io_base = g_usb_host.io_base;
    out->mmio_base = g_usb_host.mmio_base;
    out->pci_command = g_usb_host.pci_command;
    return AIOS_OK;
}

void usb_host_dump(void) {
    if (!g_usb_host.present) {
        serial_write("[USB] Host dump: controller not present\n");
        return;
    }

    serial_printf("[USB] dump type=%u ready=%u vendor=%x device=%x pci=%u:%u mmio=%x io=%x\n",
        (uint64_t)g_usb_host.controller_kind,
        g_usb_host.ready ? 1ULL : 0ULL,
        (uint64_t)g_usb_host.vendor_id,
        (uint64_t)g_usb_host.device_id,
        (uint64_t)g_usb_host.bus,
        (uint64_t)g_usb_host.slot,
        (uint64_t)g_usb_host.mmio_base,
        (uint64_t)g_usb_host.io_base);
}
