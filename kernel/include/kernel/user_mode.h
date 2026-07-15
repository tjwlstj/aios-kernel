/*
 * AIOS Kernel - User Mode Scaffold
 * AI-Native Operating System
 *
 * Declares the minimal ring3/TSS scaffold that prepares the kernel
 * for a later user-space handoff without claiming full userspace support.
 */

#ifndef _AIOS_KERNEL_USER_MODE_H
#define _AIOS_KERNEL_USER_MODE_H

#include <kernel/types.h>

#define AIOS_GDT_KERNEL_CS  0x08
#define AIOS_GDT_KERNEL_DS  0x10
#define AIOS_GDT_USER_DS    0x18
#define AIOS_GDT_USER_CS    0x20
#define AIOS_GDT_TSS        0x28

#define AIOS_USER_DS_RPL3   (AIOS_GDT_USER_DS | 0x3)
#define AIOS_USER_CS_RPL3   (AIOS_GDT_USER_CS | 0x3)
#define AIOS_TSS_IOPB_BASE  104
#define AIOS_USER_KERNEL_STACK_SIZE KB(16)
#define AIOS_RING3_ENTRY_FRAME_SIZE (5ULL * sizeof(uint64_t))

typedef struct PACKED {
    uint32_t reserved0;
    uint64_t rsp0;
    uint64_t rsp1;
    uint64_t rsp2;
    uint64_t reserved1;
    uint64_t ist1;
    uint64_t ist2;
    uint64_t ist3;
    uint64_t ist4;
    uint64_t ist5;
    uint64_t ist6;
    uint64_t ist7;
    uint64_t reserved2;
    uint16_t reserved3;
    uint16_t iomap_base;
} tss64_t;

AIOS_STATIC_ASSERT(sizeof(tss64_t) == AIOS_TSS_IOPB_BASE,
    "64-bit TSS layout must stay 104 bytes");

typedef struct {
    bool ready;
    bool gdt_present;
    bool user_segments_present;
    bool tss_loaded;
    uint16_t gdt_limit;
    uint16_t task_register;
    uint16_t kernel_cs;
    uint16_t kernel_ds;
    uint16_t user_cs;
    uint16_t user_ds;
    uint16_t tss_selector;
    uint64_t gdt_base;
    uint64_t rsp0;
} user_mode_scaffold_info_t;

/* Non-nestable guard for publishing one static ring3->ring0 entry stack to
 * the BSP boot TSS. Publish/restore require IF=0 and never change IF. */
typedef struct {
    uint64_t previous_rsp0;
    uint64_t published_rsp0;
    bool active;
    bool published;
    bool restored;
} user_mode_rsp0_guard_t;

AIOS_STATIC_ASSERT(AIOS_USER_KERNEL_STACK_SIZE == KB(16),
    "bootstrap process kernel stack contract must stay 16KiB");
AIOS_STATIC_ASSERT((AIOS_USER_KERNEL_STACK_SIZE % PAGE_SIZE) == 0,
    "bootstrap process kernel stack must be page-sized");

aios_status_t user_mode_scaffold_init(void);
aios_status_t user_mode_scaffold_info(user_mode_scaffold_info_t *out);
bool user_mode_scaffold_ready(void);
uint64_t user_mode_rsp0_read(void);
aios_status_t user_mode_rsp0_publish(
    uint64_t stack_base, uint64_t stack_size,
    user_mode_rsp0_guard_t *guard);
aios_status_t user_mode_rsp0_restore(user_mode_rsp0_guard_t *guard);

#endif /* _AIOS_KERNEL_USER_MODE_H */
