/*
 * AIOS Kernel - Minimal Peripheral Probe Layer
 * AI-Native Operating System
 */

#include <drivers/platform_probe.h>
#include <drivers/vga.h>
#include <drivers/serial.h>

#define PCI_CONFIG_ADDR 0xCF8
#define PCI_CONFIG_DATA 0xCFC

#define PCI_CLASS_STORAGE    0x01
#define PCI_CLASS_NETWORK    0x02
#define PCI_CLASS_SERIALBUS  0x0C
#define PCI_CLASS_WIRELESS   0x0D

#define PCI_SUBCLASS_ETHERNET 0x00
#define PCI_SUBCLASS_USB      0x03
#define PCI_SUBCLASS_BLUETOOTH 0x11

static platform_device_t probed_devices[MAX_PLATFORM_DEVICES];
static uint32_t probed_device_count = 0;
static platform_probe_summary_t probe_summary = {0};

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

static uint8_t pci_config_read8(uint8_t bus, uint8_t slot, uint8_t function,
                                uint8_t offset) {
    uint32_t val = pci_config_read(bus, slot, function, offset);
    return (uint8_t)((val >> ((offset & 3) * 8)) & 0xFF);
}

static void copy_string(char *dst, const char *src, uint32_t max_len) {
    uint32_t i = 0;
    while (src[i] && i + 1 < max_len) {
        dst[i] = src[i];
        i++;
    }
    dst[i] = '\0';
}

static const char *usb_prog_if_name(uint8_t prog_if) {
    switch (prog_if) {
        case 0x00: return "USB UHCI controller";
        case 0x10: return "USB OHCI controller";
        case 0x20: return "USB EHCI controller";
        case 0x30: return "USB XHCI controller";
        default:   return "USB controller";
    }
}

static platform_device_kind_t classify_device(uint8_t class_code, uint8_t subclass,
                                              uint8_t prog_if, const char **name) {
    (void)prog_if;

    if (class_code == PCI_CLASS_NETWORK && subclass == PCI_SUBCLASS_ETHERNET) {
        *name = "Ethernet controller";
        return PLATFORM_DEVICE_ETHERNET;
    }

    if (class_code == PCI_CLASS_NETWORK && subclass == 0x80) {
        *name = "Wireless network controller";
        return PLATFORM_DEVICE_WIRELESS;
    }

    if (class_code == PCI_CLASS_WIRELESS && subclass == PCI_SUBCLASS_BLUETOOTH) {
        *name = "Bluetooth controller";
        return PLATFORM_DEVICE_BLUETOOTH;
    }

    if (class_code == PCI_CLASS_WIRELESS) {
        *name = "Wireless controller";
        return PLATFORM_DEVICE_WIRELESS;
    }

    if (class_code == PCI_CLASS_SERIALBUS && subclass == PCI_SUBCLASS_USB) {
        *name = usb_prog_if_name(prog_if);
        return PLATFORM_DEVICE_USB;
    }

    if (class_code == PCI_CLASS_STORAGE) {
        *name = "Storage controller";
        return PLATFORM_DEVICE_STORAGE;
    }

    *name = NULL;
    return PLATFORM_DEVICE_UNKNOWN;
}

static void account_device(platform_device_kind_t kind) {
    probe_summary.matched_devices++;
    switch (kind) {
        case PLATFORM_DEVICE_ETHERNET:  probe_summary.ethernet_count++; break;
        case PLATFORM_DEVICE_WIRELESS:  probe_summary.wireless_count++; break;
        case PLATFORM_DEVICE_BLUETOOTH: probe_summary.bluetooth_count++; break;
        case PLATFORM_DEVICE_USB:       probe_summary.usb_count++; break;
        case PLATFORM_DEVICE_STORAGE:   probe_summary.storage_count++; break;
        default: break;
    }
}

static uint8_t classify_priority(platform_device_kind_t kind, boot_perf_tier_t tier) {
    if (tier == BOOT_PERF_TIER_LOW) {
        switch (kind) {
            case PLATFORM_DEVICE_STORAGE:   return 10;
            case PLATFORM_DEVICE_ETHERNET:  return 20;
            case PLATFORM_DEVICE_USB:       return 30;
            case PLATFORM_DEVICE_WIRELESS:  return 40;
            case PLATFORM_DEVICE_BLUETOOTH: return 50;
            default:                        return 90;
        }
    }

    if (tier == BOOT_PERF_TIER_MID) {
        switch (kind) {
            case PLATFORM_DEVICE_ETHERNET:  return 10;
            case PLATFORM_DEVICE_STORAGE:   return 20;
            case PLATFORM_DEVICE_USB:       return 30;
            case PLATFORM_DEVICE_WIRELESS:  return 40;
            case PLATFORM_DEVICE_BLUETOOTH: return 50;
            default:                        return 90;
        }
    }

    switch (kind) {
        case PLATFORM_DEVICE_ETHERNET:  return 10;
        case PLATFORM_DEVICE_USB:       return 20;
        case PLATFORM_DEVICE_STORAGE:   return 30;
        case PLATFORM_DEVICE_WIRELESS:  return 40;
        case PLATFORM_DEVICE_BLUETOOTH: return 50;
        default:                        return 90;
    }
}

static void probe_pcie_link(platform_device_t *dev) {
    uint32_t status_reg = pci_config_read(dev->bus, dev->slot, dev->function, 0x04);
    if (((status_reg >> 16) & BIT(4)) == 0) {
        return;
    }

    uint8_t cap_ptr = pci_config_read8(dev->bus, dev->slot, dev->function, 0x34) & 0xFC;
    for (uint32_t guard = 0; guard < 48 && cap_ptr >= 0x40; guard++) {
        uint8_t cap_id = pci_config_read8(dev->bus, dev->slot, dev->function, cap_ptr);
        uint8_t next = pci_config_read8(dev->bus, dev->slot, dev->function, cap_ptr + 1) & 0xFC;

        if (cap_id == 0x10) {
            uint32_t link_reg = pci_config_read(dev->bus, dev->slot, dev->function, cap_ptr + 0x10);
            uint32_t link_status_reg = pci_config_read(dev->bus, dev->slot, dev->function, cap_ptr + 0x14);
            uint16_t link_status = (uint16_t)(link_status_reg >> 16);
            (void)link_reg;
            dev->pcie_capable = true;
            dev->pcie_link_speed = (uint8_t)(link_status & 0x0F);
            dev->pcie_link_width = (uint8_t)((link_status >> 4) & 0x3F);
            return;
        }

        if (next == 0 || next == cap_ptr) {
            break;
        }
        cap_ptr = next;
    }
}

static void sort_devices_by_priority(void) {
    for (uint32_t i = 0; i < probed_device_count; i++) {
        for (uint32_t j = i + 1; j < probed_device_count; j++) {
            if (probed_devices[j].init_priority < probed_devices[i].init_priority) {
                platform_device_t tmp = probed_devices[i];
                probed_devices[i] = probed_devices[j];
                probed_devices[j] = tmp;
            }
        }
    }
}

static void print_pipeline_plan(void) {
    kprintf("    PCI init pipeline (%s tier):\n",
        kernel_boot_perf_tier_name(probe_summary.boot_tier));
    serial_printf("[DEV] PCI init pipeline tier=%s\n",
        (uint64_t)(uintptr_t)kernel_boot_perf_tier_name(probe_summary.boot_tier));

    for (uint32_t i = 0; i < probed_device_count; i++) {
        const platform_device_t *dev = &probed_devices[i];
        if (dev->pcie_capable) {
            kprintf("    [%u] %s PCIe gen%u x%u\n",
                (uint64_t)dev->init_priority,
                dev->name,
                (uint64_t)dev->pcie_link_speed,
                (uint64_t)dev->pcie_link_width);
            serial_printf("[DEV] pipeline[%u]=%s pcie=gen%u x%u\n",
                (uint64_t)dev->init_priority,
                (uint64_t)(uintptr_t)dev->name,
                (uint64_t)dev->pcie_link_speed,
                (uint64_t)dev->pcie_link_width);
        } else {
            kprintf("    [%u] %s legacy/unknown link\n",
                (uint64_t)dev->init_priority,
                dev->name);
            serial_printf("[DEV] pipeline[%u]=%s link=legacy\n",
                (uint64_t)dev->init_priority,
                (uint64_t)(uintptr_t)dev->name);
        }
    }
}

aios_status_t platform_probe_init(void) {
    const memory_selftest_result_t *profile = kernel_memory_selftest_last();

    probed_device_count = 0;
    probe_summary.total_pci_devices = 0;
    probe_summary.matched_devices = 0;
    probe_summary.ethernet_count = 0;
    probe_summary.wireless_count = 0;
    probe_summary.bluetooth_count = 0;
    probe_summary.usb_count = 0;
    probe_summary.storage_count = 0;
    probe_summary.boot_tier = profile->tier;

    kprintf("    Probing PCI peripherals (net/wireless/bluetooth/usb)...\n");
    serial_write("[DEV] Probing PCI peripherals...\n");

    for (uint16_t bus = 0; bus < 256; bus++) {
        for (uint16_t slot = 0; slot < 32; slot++) {
            uint32_t reg0 = pci_config_read((uint8_t)bus, (uint8_t)slot, 0, 0x00);
            uint16_t vendor_id = (uint16_t)(reg0 & 0xFFFF);
            uint16_t device_id = (uint16_t)((reg0 >> 16) & 0xFFFF);

            if (vendor_id == 0xFFFF) {
                continue;
            }

            probe_summary.total_pci_devices++;

            uint32_t reg2 = pci_config_read((uint8_t)bus, (uint8_t)slot, 0, 0x08);
            uint8_t prog_if = (uint8_t)((reg2 >> 8) & 0xFF);
            uint8_t subclass = (uint8_t)((reg2 >> 16) & 0xFF);
            uint8_t class_code = (uint8_t)((reg2 >> 24) & 0xFF);
            const char *label = NULL;
            platform_device_kind_t kind = classify_device(class_code, subclass, prog_if, &label);

            if (kind == PLATFORM_DEVICE_UNKNOWN || probed_device_count >= MAX_PLATFORM_DEVICES) {
                continue;
            }

            platform_device_t *dev = &probed_devices[probed_device_count++];
            dev->vendor_id = vendor_id;
            dev->device_id = device_id;
            dev->class_code = class_code;
            dev->subclass = subclass;
            dev->prog_if = prog_if;
            dev->bus = (uint8_t)bus;
            dev->slot = (uint8_t)slot;
            dev->function = 0;
            dev->kind = kind;
            dev->pcie_capable = false;
            dev->pcie_link_speed = 0;
            dev->pcie_link_width = 0;
            dev->init_priority = classify_priority(kind, probe_summary.boot_tier);
            copy_string(dev->name, label, sizeof(dev->name));
            probe_pcie_link(dev);

            account_device(kind);

            if (dev->pcie_capable) {
                kprintf("    %s [%x:%x] PCI %u:%u PCIe gen%u x%u\n",
                    dev->name,
                    (uint64_t)vendor_id,
                    (uint64_t)device_id,
                    (uint64_t)dev->bus,
                    (uint64_t)dev->slot,
                    (uint64_t)dev->pcie_link_speed,
                    (uint64_t)dev->pcie_link_width);
                serial_printf("[DEV] %s [%x:%x] PCI %u:%u PCIe gen%u x%u\n",
                    (uint64_t)(uintptr_t)dev->name,
                    (uint64_t)vendor_id,
                    (uint64_t)device_id,
                    (uint64_t)dev->bus,
                    (uint64_t)dev->slot,
                    (uint64_t)dev->pcie_link_speed,
                    (uint64_t)dev->pcie_link_width);
            } else {
                kprintf("    %s [%x:%x] PCI %u:%u\n",
                    dev->name,
                    (uint64_t)vendor_id,
                    (uint64_t)device_id,
                    (uint64_t)dev->bus,
                    (uint64_t)dev->slot);
                serial_printf("[DEV] %s [%x:%x] PCI %u:%u\n",
                    (uint64_t)(uintptr_t)dev->name,
                    (uint64_t)vendor_id,
                    (uint64_t)device_id,
                    (uint64_t)dev->bus,
                    (uint64_t)dev->slot);
            }
        }
    }

    sort_devices_by_priority();
    print_pipeline_plan();

    serial_printf("[DEV] Summary: pci=%u matched=%u eth=%u wifi=%u bt=%u usb=%u storage=%u\n",
        probe_summary.total_pci_devices,
        probe_summary.matched_devices,
        probe_summary.ethernet_count,
        probe_summary.wireless_count,
        probe_summary.bluetooth_count,
        probe_summary.usb_count,
        probe_summary.storage_count);
    serial_write("[DEV] Peripheral probe ready\n");

    return AIOS_OK;
}

uint32_t platform_probe_count(void) {
    return probed_device_count;
}

const platform_device_t *platform_probe_get(uint32_t index) {
    if (index >= probed_device_count) {
        return NULL;
    }
    return &probed_devices[index];
}

const platform_probe_summary_t *platform_probe_summary(void) {
    return &probe_summary;
}

void platform_probe_dump(void) {
    kprintf("Detected PCI devices: total=%u matched=%u\n",
        probe_summary.total_pci_devices, probe_summary.matched_devices);
    for (uint32_t i = 0; i < probed_device_count; i++) {
        const platform_device_t *dev = &probed_devices[i];
        kprintf("  %s [%x:%x] PCI %u:%u\n",
            dev->name,
            (uint64_t)dev->vendor_id,
            (uint64_t)dev->device_id,
            (uint64_t)dev->bus,
            (uint64_t)dev->slot);
    }
}

__asm__(".section .note.GNU-stack,\"\",@progbits");
