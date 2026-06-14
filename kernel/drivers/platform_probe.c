/*
 * AIOS Kernel - Minimal Peripheral Probe Layer
 * AI-Native Operating System
 */

#include <drivers/platform_probe.h>
#include <drivers/pci_core.h>
#include <drivers/vga.h>
#include <drivers/serial.h>

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

static void probe_bars(platform_device_t *dev) {
    if (!dev) {
        return;
    }

    dev->bar_count = 0;
    dev->mmio_bar_count = 0;
    dev->io_bar_count = 0;
    dev->has_64bit_bar = false;

    for (uint8_t i = 0; i < PCI_BAR_COUNT; i++) {
        pci_bar_t bar;
        if (pci_read_bar(dev->bus, dev->slot, dev->function, i, &bar) != AIOS_OK ||
            !bar.present) {
            continue;
        }

        dev->bar_count++;
        if (bar.io_space) {
            dev->io_bar_count++;
        } else if (bar.mem_space) {
            dev->mmio_bar_count++;
        }
        if (bar.is_64bit) {
            dev->has_64bit_bar = true;
            if (i + 1 < PCI_BAR_COUNT) {
                i++;
            }
        }
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
    pci_core_begin_enumeration();

    kprintf("    Probing PCI peripherals (net/wireless/bluetooth/usb)...\n");
    serial_write("[DEV] Probing PCI peripherals...\n");

    for (uint16_t bus = 0; bus < 256; bus++) {
        if (!pci_bus_in_range((uint8_t)bus)) {
            continue;
        }
        for (uint16_t slot = 0; slot < 32; slot++) {
            uint8_t function_count = pci_function_count((uint8_t)bus, (uint8_t)slot);
            if (function_count == 0) {
                continue;
            }

            for (uint8_t function = 0; function < function_count; function++) {
                pci_identity_t ident;
                pci_capabilities_t caps;
                if (pci_read_identity((uint8_t)bus, (uint8_t)slot, function, &ident) != AIOS_OK) {
                    continue;
                }
                (void)pci_probe_capabilities((uint8_t)bus, (uint8_t)slot, function, &caps);
                pci_core_account_function(&ident, &caps);

                probe_summary.total_pci_devices++;
                const char *label = NULL;
                platform_device_kind_t kind = classify_device(ident.class_code, ident.subclass,
                    ident.prog_if, &label);

                if (kind == PLATFORM_DEVICE_UNKNOWN || probed_device_count >= MAX_PLATFORM_DEVICES) {
                    continue;
                }

                platform_device_t *dev = &probed_devices[probed_device_count++];
                dev->vendor_id = ident.vendor_id;
                dev->device_id = ident.device_id;
                dev->class_code = ident.class_code;
                dev->subclass = ident.subclass;
                dev->prog_if = ident.prog_if;
                dev->bus = (uint8_t)bus;
                dev->slot = (uint8_t)slot;
                dev->function = function;
                dev->multifunction = ident.multifunction;
                dev->bridge = ident.bridge;
                dev->kind = kind;
                dev->pcie_capable = caps.has_pcie;
                dev->msi_capable = caps.has_msi;
                dev->msix_capable = caps.has_msix;
                dev->power_manage_capable = caps.has_pm;
                dev->pcie_link_speed = caps.pcie_link_speed;
                dev->pcie_link_width = caps.pcie_link_width;
                dev->irq_line = ident.irq_line;
                dev->irq_pin = ident.irq_pin;
                dev->init_priority = classify_priority(kind, probe_summary.boot_tier);
                copy_string(dev->name, label, sizeof(dev->name));
                probe_bars(dev);

                account_device(kind);

                if (dev->pcie_capable) {
                    kprintf("    %s [%x:%x] PCI %u:%u.%u PCIe gen%u x%u\n",
                        dev->name,
                        (uint64_t)dev->vendor_id,
                        (uint64_t)dev->device_id,
                        (uint64_t)dev->bus,
                        (uint64_t)dev->slot,
                        (uint64_t)dev->function,
                        (uint64_t)dev->pcie_link_speed,
                        (uint64_t)dev->pcie_link_width);
                    serial_printf("[DEV] %s [%x:%x] PCI %u:%u.%u PCIe gen%u x%u\n",
                        (uint64_t)(uintptr_t)dev->name,
                        (uint64_t)dev->vendor_id,
                        (uint64_t)dev->device_id,
                        (uint64_t)dev->bus,
                        (uint64_t)dev->slot,
                        (uint64_t)dev->function,
                        (uint64_t)dev->pcie_link_speed,
                        (uint64_t)dev->pcie_link_width);
                } else {
                    kprintf("    %s [%x:%x] PCI %u:%u.%u\n",
                        dev->name,
                        (uint64_t)dev->vendor_id,
                        (uint64_t)dev->device_id,
                        (uint64_t)dev->bus,
                        (uint64_t)dev->slot,
                        (uint64_t)dev->function);
                    serial_printf("[DEV] %s [%x:%x] PCI %u:%u.%u\n",
                        (uint64_t)(uintptr_t)dev->name,
                        (uint64_t)dev->vendor_id,
                        (uint64_t)dev->device_id,
                        (uint64_t)dev->bus,
                        (uint64_t)dev->slot,
                        (uint64_t)dev->function);
                }

                serial_printf("[DEV] caps irq=%u pin=%u msi=%u msix=%u bars=%u mmio=%u io=%u 64=%u\n",
                    (uint64_t)dev->irq_line,
                    (uint64_t)dev->irq_pin,
                    dev->msi_capable ? 1ULL : 0ULL,
                    dev->msix_capable ? 1ULL : 0ULL,
                    (uint64_t)dev->bar_count,
                    (uint64_t)dev->mmio_bar_count,
                    (uint64_t)dev->io_bar_count,
                    dev->has_64bit_bar ? 1ULL : 0ULL);
            }
        }
    }

    sort_devices_by_priority();
    print_pipeline_plan();

    const pci_core_summary_t *pci = pci_core_summary();
    serial_printf("[DEV] Core: mode=%s ecam=%u total_fn=%u bridges=%u pcie=%u msi=%u msix=%u\n",
        (uint64_t)(uintptr_t)pci_cfg_access_mode_name(pci->access_mode),
        pci->ecam_available ? 1ULL : 0ULL,
        pci->total_functions,
        pci->bridges,
        pci->pcie_functions,
        pci->msi_capable_functions,
        pci->msix_capable_functions);
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
        kprintf("  %s [%x:%x] PCI %u:%u.%u irq=%u pin=%u msi=%u msix=%u bars=%u mmio=%u io=%u\n",
            dev->name,
            (uint64_t)dev->vendor_id,
            (uint64_t)dev->device_id,
            (uint64_t)dev->bus,
            (uint64_t)dev->slot,
            (uint64_t)dev->function,
            (uint64_t)dev->irq_line,
            (uint64_t)dev->irq_pin,
            (uint64_t)dev->msi_capable,
            (uint64_t)dev->msix_capable,
            (uint64_t)dev->bar_count,
            (uint64_t)dev->mmio_bar_count,
            (uint64_t)dev->io_bar_count);
    }
}

__asm__(".section .note.GNU-stack,\"\",@progbits");
