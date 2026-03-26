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
#define E1000_REG_TCTL       0x0400
#define E1000_REG_TIPG       0x0410
#define E1000_REG_TDBAL      0x3800
#define E1000_REG_TDBAH      0x3804
#define E1000_REG_TDLEN      0x3808
#define E1000_REG_TDH        0x3810
#define E1000_REG_TDT        0x3818
#define E1000_REG_RAL        0x5400
#define E1000_REG_RAH        0x5404

#define E1000_STATUS_LINK_UP BIT(1)
#define E1000_CTRL_SLU       BIT(6)
#define E1000_EERD_START     BIT(0)
#define E1000_EERD_DONE      BIT(4)
#define E1000_TCTL_EN        BIT(1)
#define E1000_TCTL_PSP       BIT(3)
#define E1000_TX_CMD_EOP     BIT(0)
#define E1000_TX_CMD_IFCS    BIT(1)
#define E1000_TX_CMD_RS      BIT(3)
#define E1000_TX_STATUS_DD   BIT(0)

#define E1000_TX_RING_SIZE   8
#define E1000_TX_BUF_SIZE    2048
#define E1000_TEST_FRAME_LEN 60

typedef struct {
    uint64_t addr;
    uint16_t length;
    uint8_t cso;
    uint8_t cmd;
    uint8_t status;
    uint8_t css;
    uint16_t special;
} PACKED e1000_tx_desc_t;

typedef struct {
    bool present;
    bool ready;
    bool link_up;
    bool has_eeprom;
    bool tx_ready;
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
static e1000_tx_desc_t g_tx_ring[E1000_TX_RING_SIZE] ALIGNED(16);
static uint8_t g_tx_buffers[E1000_TX_RING_SIZE][E1000_TX_BUF_SIZE] ALIGNED(16);
static uint32_t g_tx_tail = 0;

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

static uint32_t e1000_refresh_status(void) {
    g_e1000.status = e1000_read_reg(E1000_REG_STATUS);
    g_e1000.link_up = (g_e1000.status & E1000_STATUS_LINK_UP) != 0;
    return g_e1000.status;
}

static void e1000_wait_for_link(void) {
    for (uint32_t spin = 0; spin < 200000; spin++) {
        if (e1000_refresh_status() & E1000_STATUS_LINK_UP) {
            return;
        }
    }
}

static void e1000_build_test_src_mac(uint8_t mac[6]) {
    bool zero = true;
    for (uint32_t i = 0; i < 6; i++) {
        mac[i] = g_e1000.mac[i];
        if (mac[i] != 0) {
            zero = false;
        }
    }

    if (!zero) {
        return;
    }

    mac[0] = 0x02;
    mac[1] = 0xA1;
    mac[2] = 0x05;
    mac[3] = g_e1000.bus;
    mac[4] = g_e1000.slot;
    mac[5] = g_e1000.function;
}

static aios_status_t e1000_init_tx_ring(void) {
    memset(g_tx_ring, 0, sizeof(g_tx_ring));
    memset(g_tx_buffers, 0, sizeof(g_tx_buffers));

    for (uint32_t i = 0; i < E1000_TX_RING_SIZE; i++) {
        g_tx_ring[i].addr = (uint64_t)(uintptr_t)&g_tx_buffers[i][0];
        g_tx_ring[i].status = E1000_TX_STATUS_DD;
    }

    e1000_write_reg(E1000_REG_TDBAL, (uint32_t)(uintptr_t)&g_tx_ring[0]);
    e1000_write_reg(E1000_REG_TDBAH, 0);
    e1000_write_reg(E1000_REG_TDLEN, sizeof(g_tx_ring));
    e1000_write_reg(E1000_REG_TDH, 0);
    e1000_write_reg(E1000_REG_TDT, 0);
    e1000_write_reg(E1000_REG_TCTL,
        E1000_TCTL_EN |
        E1000_TCTL_PSP |
        (0x0F << 4) |
        (0x40 << 12));
    e1000_write_reg(E1000_REG_TIPG, 0x0060200A);

    g_tx_tail = 0;
    g_e1000.tx_ready = true;
    return AIOS_OK;
}

static aios_status_t e1000_send_frame(const uint8_t *frame, uint16_t length) {
    if (!g_e1000.ready || !g_e1000.tx_ready || !frame) {
        return AIOS_ERR_INVAL;
    }

    e1000_refresh_status();

    if (length == 0 || length > E1000_TX_BUF_SIZE) {
        return AIOS_ERR_INVAL;
    }

    e1000_tx_desc_t *desc = &g_tx_ring[g_tx_tail];
    if ((desc->status & E1000_TX_STATUS_DD) == 0) {
        return AIOS_ERR_BUSY;
    }

    memcpy(g_tx_buffers[g_tx_tail], frame, length);
    desc->length = length;
    desc->cso = 0;
    desc->cmd = E1000_TX_CMD_EOP | E1000_TX_CMD_IFCS | E1000_TX_CMD_RS;
    desc->status = 0;
    desc->css = 0;
    desc->special = 0;

    uint32_t desc_index = g_tx_tail;
    g_tx_tail = (g_tx_tail + 1) % E1000_TX_RING_SIZE;
    e1000_write_reg(E1000_REG_TDT, g_tx_tail);

    for (uint32_t spin = 0; spin < 1000000; spin++) {
        if (g_tx_ring[desc_index].status & E1000_TX_STATUS_DD) {
            return AIOS_OK;
        }
    }

    return AIOS_ERR_TIMEOUT;
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
    e1000_wait_for_link();
    g_e1000.has_eeprom = e1000_detect_eeprom();
    e1000_read_mac();
    e1000_init_tx_ring();
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
    if (e1000_driver_tx_smoke() == AIOS_OK) {
        serial_write("[NET] E1000 TX smoke PASS\n");
    } else {
        serial_write("[NET] E1000 TX smoke FAIL\n");
    }

    return AIOS_OK;
}

bool e1000_driver_ready(void) {
    return g_e1000.ready;
}

aios_status_t e1000_driver_tx_smoke(void) {
    uint8_t frame[E1000_TEST_FRAME_LEN];
    uint8_t src_mac[6];
    memset(frame, 0, sizeof(frame));

    for (uint32_t i = 0; i < 6; i++) {
        frame[i] = 0xFF;
    }

    e1000_build_test_src_mac(src_mac);
    memcpy(&frame[6], src_mac, 6);
    frame[12] = 0x88;
    frame[13] = 0xB5;

    const char payload[] = "AIOS e1000 tx smoke";
    memcpy(&frame[14], payload, sizeof(payload) - 1);

    return e1000_send_frame(frame, sizeof(frame));
}

aios_status_t e1000_driver_info(e1000_driver_info_t *out) {
    if (!out) {
        return AIOS_ERR_INVAL;
    }

    out->present = g_e1000.present;
    out->ready = g_e1000.ready;
    out->link_up = g_e1000.link_up;
    out->has_eeprom = g_e1000.has_eeprom;
    out->tx_ready = g_e1000.tx_ready;
    out->bus = g_e1000.bus;
    out->slot = g_e1000.slot;
    out->function = g_e1000.function;
    out->vendor_id = g_e1000.vendor_id;
    out->device_id = g_e1000.device_id;
    out->io_base = g_e1000.io_base;
    out->mmio_base = g_e1000.mmio_base;
    out->status = g_e1000.status;
    for (uint32_t i = 0; i < 6; i++) {
        out->mac[i] = g_e1000.mac[i];
    }

    return AIOS_OK;
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
    serial_printf("[NET] E1000 tx_ready=%u tail=%u\n",
        g_e1000.tx_ready ? 1ULL : 0ULL,
        (uint64_t)g_tx_tail);
    e1000_print_mac("[NET] E1000 MAC=");
}
