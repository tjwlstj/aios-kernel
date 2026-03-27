/*
 * AIOS Kernel - Minimal ACPI Fabric Parser
 * AI-Native Operating System
 */

#include <kernel/acpi.h>
#include <drivers/serial.h>
#include <drivers/vga.h>
#include <lib/string.h>

#define MULTIBOOT2_MAGIC            0x36d76289
#define MULTIBOOT_TAG_TYPE_END      0
#define MULTIBOOT_TAG_TYPE_ACPI_OLD 14
#define MULTIBOOT_TAG_TYPE_ACPI_NEW 15

typedef struct PACKED {
    uint32_t total_size;
    uint32_t reserved;
} multiboot_info_header_t;

typedef struct PACKED {
    uint32_t type;
    uint32_t size;
} multiboot_tag_t;

typedef struct PACKED {
    char signature[8];
    uint8_t checksum;
    char oem_id[6];
    uint8_t revision;
    uint32_t rsdt_address;
} acpi_rsdp_v1_t;

typedef struct PACKED {
    acpi_rsdp_v1_t first_part;
    uint32_t length;
    uint64_t xsdt_address;
    uint8_t extended_checksum;
    uint8_t reserved[3];
} acpi_rsdp_v2_t;

typedef struct PACKED {
    char signature[4];
    uint32_t length;
    uint8_t revision;
    uint8_t checksum;
    char oem_id[6];
    char oem_table_id[8];
    uint32_t oem_revision;
    uint32_t creator_id;
    uint32_t creator_revision;
} acpi_sdt_header_t;

typedef struct PACKED {
    acpi_sdt_header_t header;
    uint64_t reserved;
} acpi_mcfg_table_t;

typedef struct PACKED {
    uint64_t base_address;
    uint16_t segment_group;
    uint8_t start_bus;
    uint8_t end_bus;
    uint32_t reserved;
} acpi_mcfg_entry_t;

static acpi_info_t g_acpi_info = {0};

static void parse_root_table(acpi_info_t *info, uint64_t root_addr, uint32_t entry_size);

static uint8_t checksum8(const void *ptr, uint32_t length) {
    const uint8_t *bytes = (const uint8_t *)ptr;
    uint8_t sum = 0;
    for (uint32_t i = 0; i < length; i++) {
        sum = (uint8_t)(sum + bytes[i]);
    }
    return sum;
}

static bool phys_addr_mapped(uint64_t phys_addr, uint32_t length) {
    return phys_addr != 0 && phys_addr < GB(4) && (phys_addr + length) <= GB(4);
}

static const void *phys_ptr(uint64_t phys_addr, uint32_t length) {
    if (!phys_addr_mapped(phys_addr, length)) {
        return NULL;
    }
    return (const void *)(uintptr_t)phys_addr;
}

static bool signature_is(const char *actual, const char *expected, uint32_t length) {
    return memcmp(actual, expected, length) == 0;
}

static uint16_t load_bda16(uint16_t offset) {
    uint16_t value;
    uintptr_t addr = (uintptr_t)(0x400 + offset);
    __asm__ volatile ("movw (%1), %0" : "=r"(value) : "r"(addr));
    return value;
}

static bool load_rsdp(const acpi_rsdp_v1_t *rsdp, uint64_t rsdp_addr, uint32_t payload_size) {
    if (!rsdp || payload_size < sizeof(acpi_rsdp_v1_t) ||
        !signature_is(rsdp->signature, "RSD PTR ", 8)) {
        return false;
    }

    g_acpi_info.rsdp_copy_addr = rsdp_addr;
    g_acpi_info.rsdp_found = true;
    g_acpi_info.revision = rsdp->revision;
    g_acpi_info.rsdt_addr = rsdp->rsdt_address;
    g_acpi_info.checksum_valid = (checksum8(rsdp, sizeof(acpi_rsdp_v1_t)) == 0);

    if (rsdp->revision >= 2 && payload_size >= sizeof(acpi_rsdp_v2_t)) {
        const acpi_rsdp_v2_t *rsdp2 = (const acpi_rsdp_v2_t *)rsdp;
        g_acpi_info.xsdt_present = rsdp2->xsdt_address != 0;
        g_acpi_info.xsdt_addr = rsdp2->xsdt_address;
        g_acpi_info.checksum_valid = g_acpi_info.checksum_valid &&
                                     rsdp2->length >= sizeof(acpi_rsdp_v2_t) &&
                                     rsdp2->length <= payload_size &&
                                     (checksum8(rsdp2, rsdp2->length) == 0);
    }

    g_acpi_info.rsdt_present = g_acpi_info.rsdt_addr != 0;
    if (g_acpi_info.xsdt_present) {
        parse_root_table(&g_acpi_info, g_acpi_info.xsdt_addr, 8);
    }
    if (!g_acpi_info.root_table_valid && g_acpi_info.rsdt_present) {
        parse_root_table(&g_acpi_info, g_acpi_info.rsdt_addr, 4);
    }

    return true;
}

static const acpi_rsdp_v1_t *scan_rsdp_range(uint64_t start, uint64_t end) {
    if (end <= start || !phys_addr_mapped(start, (uint32_t)(end - start))) {
        return NULL;
    }

    for (uint64_t addr = start; addr + sizeof(acpi_rsdp_v1_t) <= end; addr += 16) {
        const acpi_rsdp_v1_t *candidate =
            (const acpi_rsdp_v1_t *)(uintptr_t)addr;
        if (!signature_is(candidate->signature, "RSD PTR ", 8)) {
            continue;
        }

        uint32_t payload_size = (candidate->revision >= 2) ? sizeof(acpi_rsdp_v2_t)
                                                           : sizeof(acpi_rsdp_v1_t);
        if (addr + payload_size > end) {
            continue;
        }
        if (load_rsdp(candidate, addr, payload_size)) {
            return candidate;
        }
    }

    return NULL;
}

static void parse_root_table(acpi_info_t *info, uint64_t root_addr, uint32_t entry_size) {
    const acpi_sdt_header_t *root = (const acpi_sdt_header_t *)phys_ptr(root_addr,
        sizeof(acpi_sdt_header_t));
    if (!info || !root) {
        return;
    }

    if (root->length < sizeof(acpi_sdt_header_t) ||
        !phys_addr_mapped(root_addr, root->length) ||
        checksum8(root, root->length) != 0) {
        return;
    }

    info->root_table_valid = true;
    info->root_table_entries = (root->length - sizeof(acpi_sdt_header_t)) / entry_size;

    const uint8_t *entry_base = (const uint8_t *)root + sizeof(acpi_sdt_header_t);
    for (uint32_t i = 0; i < info->root_table_entries; i++) {
        uint64_t child_addr = 0;
        if (entry_size == 8) {
            child_addr = ((const uint64_t *)entry_base)[i];
        } else {
            child_addr = ((const uint32_t *)entry_base)[i];
        }

        const acpi_sdt_header_t *child = (const acpi_sdt_header_t *)phys_ptr(child_addr,
            sizeof(acpi_sdt_header_t));
        if (!child || child->length < sizeof(acpi_sdt_header_t) ||
            !phys_addr_mapped(child_addr, child->length) ||
            checksum8(child, child->length) != 0) {
            continue;
        }

        if (signature_is(child->signature, "MCFG", 4)) {
            info->mcfg_present = true;
            info->mcfg_addr = child_addr;

            const acpi_mcfg_table_t *mcfg = (const acpi_mcfg_table_t *)child;
            uint32_t alloc_count = 0;
            if (mcfg->header.length > sizeof(acpi_mcfg_table_t)) {
                alloc_count = (mcfg->header.length - sizeof(acpi_mcfg_table_t)) /
                              sizeof(acpi_mcfg_entry_t);
            }
            if (alloc_count > 0) {
                const acpi_mcfg_entry_t *entry =
                    (const acpi_mcfg_entry_t *)((const uint8_t *)mcfg + sizeof(acpi_mcfg_table_t));
                info->mcfg_base_addr = entry->base_address;
                info->mcfg_segment_group = entry->segment_group;
                info->mcfg_start_bus = entry->start_bus;
                info->mcfg_end_bus = entry->end_bus;
            }
        } else if (signature_is(child->signature, "APIC", 4)) {
            info->madt_present = true;
            info->madt_addr = child_addr;
        } else if (signature_is(child->signature, "FACP", 4)) {
            info->fadt_present = true;
            info->fadt_addr = child_addr;
        }
    }
}

aios_status_t acpi_init(uint64_t multiboot_magic, uint64_t multiboot_info) {
    memset(&g_acpi_info, 0, sizeof(g_acpi_info));

    if (multiboot_magic != MULTIBOOT2_MAGIC ||
        multiboot_info == 0 ||
        !phys_addr_mapped(multiboot_info, sizeof(multiboot_info_header_t))) {
        serial_write("[ACPI] Multiboot2 ACPI tags unavailable\n");
        return AIOS_OK;
    }

    const multiboot_info_header_t *mbi = (const multiboot_info_header_t *)(uintptr_t)multiboot_info;
    uint64_t cursor = multiboot_info + sizeof(multiboot_info_header_t);
    uint64_t limit = multiboot_info + mbi->total_size;
    const multiboot_tag_t *chosen_tag = NULL;

    while (cursor + sizeof(multiboot_tag_t) <= limit) {
        const multiboot_tag_t *tag = (const multiboot_tag_t *)(uintptr_t)cursor;
        if (tag->type == MULTIBOOT_TAG_TYPE_END) {
            break;
        }
        if (tag->size < sizeof(multiboot_tag_t)) {
            break;
        }

        if (tag->type == MULTIBOOT_TAG_TYPE_ACPI_NEW) {
            chosen_tag = tag;
            break;
        }
        if (tag->type == MULTIBOOT_TAG_TYPE_ACPI_OLD && !chosen_tag) {
            chosen_tag = tag;
        }

        cursor += ALIGN_UP(tag->size, 8);
    }

    if (chosen_tag) {
        const acpi_rsdp_v1_t *rsdp = (const acpi_rsdp_v1_t *)((const uint8_t *)chosen_tag +
                                                               sizeof(multiboot_tag_t));
        uint32_t payload_size = chosen_tag->size - sizeof(multiboot_tag_t);
        if (!load_rsdp(rsdp, (uint64_t)(uintptr_t)rsdp, payload_size)) {
            serial_write("[ACPI] RSDP signature invalid\n");
        }
    } else {
        uint16_t ebda_segment = load_bda16(0x0E);
        uint64_t ebda_base = (uint64_t)ebda_segment << 4;
        bool found = false;

        if (ebda_base >= 0x80000 && ebda_base < 0xA0000) {
            found = scan_rsdp_range(ebda_base, ebda_base + KB(1)) != NULL;
        }
        if (!found) {
            found = scan_rsdp_range(0xE0000, 0x100000) != NULL;
        }
        if (!found) {
            serial_write("[ACPI] No ACPI RSDP tag in multiboot info or BIOS scan\n");
            return AIOS_OK;
        }
    }

    serial_printf("[ACPI] rsdp=%u rev=%u checksum=%u root=%u mcfg=%u madt=%u fadt=%u\n",
        g_acpi_info.rsdp_found ? 1ULL : 0ULL,
        (uint64_t)g_acpi_info.revision,
        g_acpi_info.checksum_valid ? 1ULL : 0ULL,
        g_acpi_info.root_table_valid ? 1ULL : 0ULL,
        g_acpi_info.mcfg_present ? 1ULL : 0ULL,
        g_acpi_info.madt_present ? 1ULL : 0ULL,
        g_acpi_info.fadt_present ? 1ULL : 0ULL);
    if (g_acpi_info.mcfg_present) {
        serial_printf("[ACPI] mcfg base=%x seg=%u bus=%u-%u\n",
            g_acpi_info.mcfg_base_addr,
            (uint64_t)g_acpi_info.mcfg_segment_group,
            (uint64_t)g_acpi_info.mcfg_start_bus,
            (uint64_t)g_acpi_info.mcfg_end_bus);
    }

    return AIOS_OK;
}

const acpi_info_t *acpi_info(void) {
    return &g_acpi_info;
}

bool acpi_ready(void) {
    return g_acpi_info.rsdp_found &&
           g_acpi_info.checksum_valid &&
           g_acpi_info.root_table_valid;
}

void acpi_dump(void) {
    kprintf("\n=== ACPI Fabric ===\n");
    kprintf("RSDP: found=%u checksum=%u rev=%u copy=%x\n",
        (uint64_t)g_acpi_info.rsdp_found,
        (uint64_t)g_acpi_info.checksum_valid,
        (uint64_t)g_acpi_info.revision,
        g_acpi_info.rsdp_copy_addr);
    kprintf("Root: valid=%u xsdt=%x rsdt=%x entries=%u\n",
        (uint64_t)g_acpi_info.root_table_valid,
        g_acpi_info.xsdt_addr,
        g_acpi_info.rsdt_addr,
        g_acpi_info.root_table_entries);
    kprintf("Tables: mcfg=%u madt=%u fadt=%u\n",
        (uint64_t)g_acpi_info.mcfg_present,
        (uint64_t)g_acpi_info.madt_present,
        (uint64_t)g_acpi_info.fadt_present);
    if (g_acpi_info.mcfg_present) {
        kprintf("MCFG: base=%x seg=%u bus=%u-%u\n",
            g_acpi_info.mcfg_base_addr,
            (uint64_t)g_acpi_info.mcfg_segment_group,
            (uint64_t)g_acpi_info.mcfg_start_bus,
            (uint64_t)g_acpi_info.mcfg_end_bus);
    }
    kprintf("===================\n");
}
