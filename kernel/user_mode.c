/*
 * AIOS Kernel - User Mode Scaffold
 * AI-Native Operating System
 */

#include <kernel/user_mode.h>
#include <drivers/serial.h>
#include <drivers/vga.h>

extern tss64_t aios_boot_tss64;

typedef struct PACKED {
    uint16_t limit;
    uint64_t base;
} descriptor_table_ptr_t;

static user_mode_scaffold_info_t g_user_mode = {
    .kernel_cs = AIOS_GDT_KERNEL_CS,
    .kernel_ds = AIOS_GDT_KERNEL_DS,
    .user_cs = AIOS_USER_CS_RPL3,
    .user_ds = AIOS_USER_DS_RPL3,
    .tss_selector = AIOS_GDT_TSS,
};

static inline void read_gdt(descriptor_table_ptr_t *out) {
    __asm__ volatile ("sgdt %0" : "=m"(*out));
}

static inline uint16_t read_task_register(void) {
    uint16_t selector;
    __asm__ volatile ("str %0" : "=r"(selector));
    return selector;
}

static uint64_t gdt_read_entry(uint64_t gdt_base, uint16_t selector) {
    const uint64_t *entry = (const uint64_t *)(uintptr_t)(gdt_base + selector);
    return *entry;
}

static bool gdt_code_descriptor_valid(uint64_t descriptor, uint8_t expected_dpl) {
    uint8_t dpl = (uint8_t)((descriptor >> 45) & 0x3);
    bool present = (descriptor & BIT(47)) != 0;
    bool executable = (descriptor & BIT(43)) != 0;
    bool long_mode = (descriptor & BIT(53)) != 0;
    return present && executable && long_mode && dpl == expected_dpl;
}

static bool gdt_data_descriptor_valid(uint64_t descriptor, uint8_t expected_dpl) {
    uint8_t dpl = (uint8_t)((descriptor >> 45) & 0x3);
    bool present = (descriptor & BIT(47)) != 0;
    bool writable = (descriptor & BIT(41)) != 0;
    bool executable = (descriptor & BIT(43)) != 0;
    return present && writable && !executable && dpl == expected_dpl;
}

static bool gdt_tss_descriptor_valid(uint64_t descriptor) {
    uint8_t type = (uint8_t)((descriptor >> 40) & 0xF);
    bool present = (descriptor & BIT(47)) != 0;
    return present && (type == 0x9 || type == 0xB);
}

aios_status_t user_mode_scaffold_init(void) {
    descriptor_table_ptr_t gdtr;
    uint64_t kernel_code = 0;
    uint64_t kernel_data = 0;
    uint64_t user_data = 0;
    uint64_t user_code = 0;
    uint64_t tss_desc = 0;

    read_gdt(&gdtr);

    g_user_mode.gdt_base = gdtr.base;
    g_user_mode.gdt_limit = gdtr.limit;
    g_user_mode.task_register = read_task_register();
    g_user_mode.rsp0 = aios_boot_tss64.rsp0;
    g_user_mode.gdt_present = gdtr.base != 0 &&
                              gdtr.limit >= (uint16_t)(AIOS_GDT_TSS + 15);

    if (g_user_mode.gdt_present) {
        kernel_code = gdt_read_entry(gdtr.base, AIOS_GDT_KERNEL_CS);
        kernel_data = gdt_read_entry(gdtr.base, AIOS_GDT_KERNEL_DS);
        user_data = gdt_read_entry(gdtr.base, AIOS_GDT_USER_DS);
        user_code = gdt_read_entry(gdtr.base, AIOS_GDT_USER_CS);
        tss_desc = gdt_read_entry(gdtr.base, AIOS_GDT_TSS);
    }

    g_user_mode.user_segments_present =
        g_user_mode.gdt_present &&
        gdt_code_descriptor_valid(kernel_code, 0) &&
        gdt_data_descriptor_valid(kernel_data, 0) &&
        gdt_data_descriptor_valid(user_data, 3) &&
        gdt_code_descriptor_valid(user_code, 3);

    g_user_mode.tss_loaded =
        g_user_mode.gdt_present &&
        gdt_tss_descriptor_valid(tss_desc) &&
        g_user_mode.task_register == AIOS_GDT_TSS &&
        g_user_mode.rsp0 != 0 &&
        aios_boot_tss64.iomap_base == AIOS_TSS_IOPB_BASE;

    g_user_mode.ready = g_user_mode.gdt_present &&
                        g_user_mode.user_segments_present &&
                        g_user_mode.tss_loaded;

    console_write_color("[USER] ", VGA_LIGHT_CYAN, VGA_BLUE);
    kprintf("Ring3 scaffold ready=%u tr=%x user_cs=%x user_ds=%x rsp0=%x gdt_base=%x gdt_limit=%u\n",
        (uint64_t)g_user_mode.ready,
        (uint64_t)g_user_mode.task_register,
        (uint64_t)g_user_mode.user_cs,
        (uint64_t)g_user_mode.user_ds,
        g_user_mode.rsp0,
        g_user_mode.gdt_base,
        (uint64_t)g_user_mode.gdt_limit);
    serial_printf("[USER] Ring3 scaffold ready=%u tr=%x user_cs=%x user_ds=%x rsp0=%x gdt_base=%x gdt_limit=%u\n",
        (uint64_t)g_user_mode.ready,
        (uint64_t)g_user_mode.task_register,
        (uint64_t)g_user_mode.user_cs,
        (uint64_t)g_user_mode.user_ds,
        g_user_mode.rsp0,
        g_user_mode.gdt_base,
        (uint64_t)g_user_mode.gdt_limit);

    return g_user_mode.ready ? AIOS_OK : AIOS_ERR_IO;
}

aios_status_t user_mode_scaffold_info(user_mode_scaffold_info_t *out) {
    if (!out) {
        return AIOS_ERR_INVAL;
    }
    *out = g_user_mode;
    return AIOS_OK;
}

bool user_mode_scaffold_ready(void) {
    return g_user_mode.ready;
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
