/*
 * AIOS Kernel - Interrupt Descriptor Table (IDT)
 * AI-Native Operating System
 *
 * Provides x86_64 IDT setup and basic exception/interrupt handling.
 */

#ifndef _AIOS_IDT_H
#define _AIOS_IDT_H

#include <kernel/types.h>

/* Number of IDT entries */
#define IDT_ENTRIES     256

/* IDT gate types */
#define IDT_GATE_INT    0x8E  /* Present, DPL=0, 64-bit Interrupt Gate */
#define IDT_GATE_TRAP   0x8F  /* Present, DPL=0, 64-bit Trap Gate */

/* x86_64 IDT entry (16 bytes) */
typedef struct PACKED {
    uint16_t offset_low;      /* Offset bits 0..15 */
    uint16_t selector;        /* Code segment selector in GDT */
    uint8_t  ist;             /* Interrupt Stack Table offset (bits 0..2) */
    uint8_t  type_attr;       /* Type and attributes */
    uint16_t offset_mid;      /* Offset bits 16..31 */
    uint32_t offset_high;     /* Offset bits 32..63 */
    uint32_t reserved;        /* Reserved, must be zero */
} idt_entry_t;

/* IDT pointer structure for LIDT instruction */
typedef struct PACKED {
    uint16_t limit;           /* Size of IDT - 1 */
    uint64_t base;            /* Base address of IDT */
} idt_ptr_t;

/* Interrupt frame pushed by CPU on exception/interrupt */
typedef struct {
    uint64_t r15, r14, r13, r12, r11, r10, r9, r8;
    uint64_t rbp, rdi, rsi, rdx, rcx, rbx, rax;
    uint64_t int_no, err_code;
    uint64_t rip, cs, rflags, rsp, ss;
} interrupt_frame_t;

/* Initialize IDT with default exception handlers */
aios_status_t idt_init(void);

/* Set a specific IDT entry */
void idt_set_gate(uint8_t num, uint64_t handler, uint16_t selector, uint8_t type_attr);

/* Generic exception handler (called from assembly stubs) */
void exception_handler(interrupt_frame_t *frame);

/* Kernel panic - halt the system with error message */
NORETURN void kernel_panic(const char *msg);

#endif /* _AIOS_IDT_H */
