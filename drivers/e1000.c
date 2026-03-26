/*
 * AIOS Kernel - Intel E1000 Minimal Driver
 * AI-Native Operating System
 */

#include <drivers/e1000.h>
#include <drivers/platform_probe.h>
#include <drivers/vga.h>
#include <drivers/serial.h>
#include <lib/string.h>

#define PCI_CONFIG_ADDR      0xCF8
#define PCI_CONFIG_DATA      0xCFC

#define PCI_VENDOR_INTEL     0x8086
#define PCI_COMMAND_OFFSET   0x04
#define PCI_BAR0_OFFSET      0x10
#define PCI_BAR1_OFFSET      0x14
#define PCI_BAR2_OFFSET      0x18
#define PCI_CMD_IO           0x1
#define PCI_CMD_MEM          0x2
#define PCI_CMD_BUSMASTER    0x4

#define E1000_DEV_82540EM    0x100E
#define E1000_DEV_82545EM    0x100F
#define E1000_DEV_82574L     0x10D3

#define E1000_REG_CTRL       0x0000
#define E1000_REG_STATUS     0x0008
#define E1000_REG_EERD       0x0014
#define E1000_REG_RAL        0x5400
#define E1000_REG_RAH        0x5404

#define E1000_STATUS_LINK_UP BIT(1)
#define E1000_CTRL_SLU       BIT(6)
#define E1000_EERD_START     BIT(0)
#define E1000_EERD_DONE      BIT(4)

typedef struct {
    bool present;
    bool ready;
    bool link_up;
    bool has_eeprom;
    uint8_t bus;
    uint8_t slot;
    uint8_t function;
    uint16_t vendor_id;
    uint16_t device_id;
    uint16_t io_base;
    uint64_t mmio_base;
    uint8_t mac[6];
    uint32_t status;
} e1000_device_t;

static e1000_device_t g_e1000 = {0};

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

static bool e1000_is_supported(uint16_t vendor_id, uint16_t device_id) {
    if (vendor_id != PCI_VENDOR_INTEL) {
        return false;
    }

    switch (device_id) {
        case E1000_DEV_82540EM:
        case E1000_DEV_82545EM:
        case E1000_DEV_82574L:
            return true;
        default:
            return false;
    }
}

static uint32_t e1000_read_reg(uint32_t reg) {
    outl((uint16_t)(g_e1000.io_base + 0), reg);
    return inl((uint16_t)(g_e1000.io_base + 4));
}

static void e1000_write_reg(uint32_t reg, uint32_t value) {
    outl((uint16_t)(g_e1000.io_base + 0), reg);
    outl((uint16_t)(g_e1000.io_base + 4), value);
}

static bool e1000_detect_eeprom(void) {
    for (uint32_t i = 0; i < 1000; i++) {
        e1000_write_reg(E1000_REG_EERD, E1000_EERD_START);
        if (e1000_read_reg(E1000_REG_EERD) & E1000_EERD_DONE) {
            return true;
        }
    }
    return false;
}

static uint16_t e1000_read_eeprom_word(uint8_t addr) {
    uint32_t cmd = E1000_EERD_START | ((uint32_t)addr << 8);
    e1000_write_reg(E1000_REG_EERD, cmd);

    for (uint32_t i = 0; i < 100000; i++) {
        uint32_t val = e1000_read_reg(E1000_REG_EERD);
        if (val & E1000_EERD_DONE) {
            return (uint16_t)((val >> 16) & 0xFFFF);
        }
    }

    return 0;
}

static void e1000_read_mac(void) {
    if (g_e1000.has_eeprom) {
        uint16_t word0 = e1000_read_eeprom_word(0);
        uint16_t word1 = e1000_read_eeprom_word(1);
        uint16_t word2 = e1000_read_eeprom_word(2);

        g_e1000.mac[0] = (uint8_t)(word0 & 0xFF);
        g_e1000.mac[1] = (uint8_t)(word0 >> 8);
        g_e1000.mac[2] = (uint8_t)(word1 & 0xFF);
        g_e1000.mac[3] = (uint8_t)(word1 >> 8);
        g_e1000.mac[4] = (uint8_t)(word2 & 0xFF);
        g_e1000.mac[5] = (uint8_t)(word2 >> 8);
        return;
    }

    uint32_t ral = e1000_read_reg(E1000_REG_RAL);
    uint32_t rah = e1000_read_reg(E1000_REG_RAH);
    g_e1000.mac[0] = (uint8_t)(ral & 0xFF);
    g_e1000.mac[1] = (uint8_t)((ral >> 8) & 0xFF);
    g_e1000.mac[2] = (uint8_t)((ral >> 16) & 0xFF);
    g_e1000.mac[3] = (uint8_t)((ral >> 24) & 0xFF);
    g_e1000.mac[4] = (uint8_t)(rah & 0xFF);
    g_e1000.mac[5] = (uint8_t)((rah >> 8) & 0xFF);
}

static void e1000_print_mac(const char *prefix) {
    serial_printf("%s%x:%x:%x:%x:%x:%x\n",
        (uint64_t)(uintptr_t)prefix,
        (uint64_t)g_e1000.mac[0],
        (uint64_t)g_e1000.mac[1],
        (uint64_t)g_e1000.mac[2],
        (uint64_t)g_e1000.mac[3],
        (uint64_t)g_e1000.mac[4],
        (uint64_t)g_e1000.mac[5]);
}

aios_status_t e1000_driver_init(void) {
    memset(&g_e1000, 0, sizeof(g_e1000));

    const platform_device_t *candidate = NULL;
    for (uint32_t i = 0; i < platform_probe_count(); i++) {
        const platform_device_t *dev = platform_probe_get(i);
        if (!dev || dev->kind != PLATFORM_DEVICE_ETHERNET) {
            continue;
        }
        if (e1000_is_supported(dev->vendor_id, dev->device_id)) {
            candidate = dev;
            break;
        }
    }

    if (!candidate) {
        kprintf("    No Intel E1000-compatible controller found.\n");
        serial_write("[NET] No Intel E1000-compatible controller found\n");
        return AIOS_OK;
    }

    g_e1000.present = true;
    g_e1000.bus = candidate->bus;
    g_e1000.slot = candidate->slot;
    g_e1000.function = candidate->function;
    g_e1000.vendor_id = candidate->vendor_id;
    g_e1000.device_id = candidate->device_id;

    uint32_t command_reg = pci_config_read(g_e1000.bus, g_e1000.slot, g_e1000.function,
                                           PCI_COMMAND_OFFSET);
    command_reg |= (PCI_CMD_IO | PCI_CMD_MEM | PCI_CMD_BUSMASTER);
    pci_config_write(g_e1000.bus, g_e1000.slot, g_e1000.function,
                     PCI_COMMAND_OFFSET, command_reg);

    uint32_t bar0 = pci_config_read(g_e1000.bus, g_e1000.slot, g_e1000.function, PCI_BAR0_OFFSET);
    uint32_t bar1 = pci_config_read(g_e1000.bus, g_e1000.slot, g_e1000.function, PCI_BAR1_OFFSET);
    uint32_t bar2 = pci_config_read(g_e1000.bus, g_e1000.slot, g_e1000.function, PCI_BAR2_OFFSET);

    if (bar0 && ((bar0 & 0x1) == 0)) {
        g_e1000.mmio_base = (uint64_t)(bar0 & 0xFFFFFFF0U);
    } else if (bar1 && ((bar1 & 0x1) == 0)) {
        g_e1000.mmio_base = (uint64_t)(bar1 & 0xFFFFFFF0U);
    }

    if (bar2 & 0x1) {
        g_e1000.io_base = (uint16_t)(bar2 & 0xFFFCU);
    } else if (bar1 & 0x1) {
        g_e1000.io_base = (uint16_t)(bar1 & 0xFFFCU);
    } else if (bar0 & 0x1) {
        g_e1000.io_base = (uint16_t)(bar0 & 0xFFFCU);
    }

    if (g_e1000.io_base == 0) {
        serial_write("[NET] E1000 found but no usable I/O BAR exposed\n");
        return AIOS_OK;
    }

    e1000_write_reg(E1000_REG_CTRL, e1000_read_reg(E1000_REG_CTRL) | E1000_CTRL_SLU);
    g_e1000.status = e1000_read_reg(E1000_REG_STATUS);
    g_e1000.link_up = (g_e1000.status & E1000_STATUS_LINK_UP) != 0;
    g_e1000.has_eeprom = e1000_detect_eeprom();
    e1000_read_mac();
    g_e1000.ready = true;

    kprintf("    Intel E1000 ready: io=0x%x status=0x%x link=%s\n",
        (uint64_t)g_e1000.io_base,
        (uint64_t)g_e1000.status,
        g_e1000.link_up ? "up" : "down");
    serial_printf("[NET] E1000 ready io=%x status=%x link=%s eeprom=%u\n",
        (uint64_t)g_e1000.io_base,
        (uint64_t)g_e1000.status,
        (uint64_t)(uintptr_t)(g_e1000.link_up ? "up" : "down"),
        g_e1000.has_eeprom ? 1ULL : 0ULL);
    e1000_print_mac("[NET] E1000 MAC=");

    return AIOS_OK;
}

bool e1000_driver_ready(void) {
    return g_e1000.ready;
}

void e1000_driver_dump(void) {
    if (!g_e1000.present) {
        serial_write("[NET] E1000 dump: controller not present\n");
        return;
    }

    serial_printf("[NET] E1000 dump vendor=%x device=%x io=%x status=%x link=%s\n",
        (uint64_t)g_e1000.vendor_id,
        (uint64_t)g_e1000.device_id,
        (uint64_t)g_e1000.io_base,
        (uint64_t)g_e1000.status,
        (uint64_t)(uintptr_t)(g_e1000.link_up ? "up" : "down"));
    e1000_print_mac("[NET] E1000 MAC=");
}

__asm__(".section .note.GNU-stack,\"\",@progbits");
