/*
 * AIOS Kernel - Interrupt Descriptor Table (IDT)
 * AI-Native Operating System
 */

#include <interrupt/idt.h>
#include <kernel/time.h>
#include <drivers/vga.h>
#include <drivers/serial.h>

/* IDT array and pointer */
static idt_entry_t idt[IDT_ENTRIES];
static idt_ptr_t   idt_ptr;

#define PIC1_COMMAND 0x20
#define PIC2_COMMAND 0xA0
#define PIC_EOI      0x20
#define IRQ_BASE     32
#define IRQ_COUNT    16

/* Exception names for debugging */
static const char *exception_names[32] = {
    "Divide Error (#DE)",
    "Debug Exception (#DB)",
    "Non-Maskable Interrupt (NMI)",
    "Breakpoint (#BP)",
    "Overflow (#OF)",
    "Bound Range Exceeded (#BR)",
    "Invalid Opcode (#UD)",
    "Device Not Available (#NM)",
    "Double Fault (#DF)",
    "Coprocessor Segment Overrun",
    "Invalid TSS (#TS)",
    "Segment Not Present (#NP)",
    "Stack-Segment Fault (#SS)",
    "General Protection Fault (#GP)",
    "Page Fault (#PF)",
    "Reserved",
    "x87 FPU Error (#MF)",
    "Alignment Check (#AC)",
    "Machine Check (#MC)",
    "SIMD Floating-Point (#XM)",
    "Virtualization Exception (#VE)",
    "Control Protection (#CP)",
    "Reserved", "Reserved", "Reserved", "Reserved",
    "Reserved", "Reserved", "Reserved", "Reserved",
    "Reserved", "Reserved"
};

/* External ISR stubs from isr_stub.asm */
extern void isr0(void);
extern void isr1(void);
extern void isr2(void);
extern void isr3(void);
extern void isr4(void);
extern void isr5(void);
extern void isr6(void);
extern void isr7(void);
extern void isr8(void);
extern void isr9(void);
extern void isr10(void);
extern void isr11(void);
extern void isr12(void);
extern void isr13(void);
extern void isr14(void);
extern void isr15(void);
extern void isr16(void);
extern void isr17(void);
extern void isr18(void);
extern void isr19(void);
extern void isr20(void);
extern void isr21(void);
extern void isr22(void);
extern void isr23(void);
extern void isr24(void);
extern void isr25(void);
extern void isr26(void);
extern void isr27(void);
extern void isr28(void);
extern void isr29(void);
extern void isr30(void);
extern void isr31(void);
extern void isr32(void);
extern void isr33(void);
extern void isr34(void);
extern void isr35(void);
extern void isr36(void);
extern void isr37(void);
extern void isr38(void);
extern void isr39(void);
extern void isr40(void);
extern void isr41(void);
extern void isr42(void);
extern void isr43(void);
extern void isr44(void);
extern void isr45(void);
extern void isr46(void);
extern void isr47(void);

/* Load IDT using LIDT instruction */
static inline void idt_load(idt_ptr_t *ptr) {
    __asm__ volatile ("lidt (%0)" : : "r"(ptr));
}

static inline void outb(uint16_t port, uint8_t val) {
    __asm__ volatile ("outb %0, %1" : : "a"(val), "Nd"(port));
}

static void pic_send_eoi(uint64_t int_no) {
    if (int_no >= 40) {
        outb(PIC2_COMMAND, PIC_EOI);
    }
    outb(PIC1_COMMAND, PIC_EOI);
}

void idt_set_gate(uint8_t num, uint64_t handler, uint16_t selector, uint8_t type_attr) {
    idt[num].offset_low  = (uint16_t)(handler & 0xFFFF);
    idt[num].selector    = selector;
    idt[num].ist         = 0;
    idt[num].type_attr   = type_attr;
    idt[num].offset_mid  = (uint16_t)((handler >> 16) & 0xFFFF);
    idt[num].offset_high = (uint32_t)((handler >> 32) & 0xFFFFFFFF);
    idt[num].reserved    = 0;
}

/* Register all 32 exception ISRs */
static void idt_register_exceptions(void) {
    /* Code segment selector = 0x08 (first GDT code segment) */
    uint16_t cs = 0x08;

    idt_set_gate(0,  (uint64_t)isr0,  cs, IDT_GATE_INT);
    idt_set_gate(1,  (uint64_t)isr1,  cs, IDT_GATE_INT);
    idt_set_gate(2,  (uint64_t)isr2,  cs, IDT_GATE_INT);
    idt_set_gate(3,  (uint64_t)isr3,  cs, IDT_GATE_INT);
    idt_set_gate(4,  (uint64_t)isr4,  cs, IDT_GATE_INT);
    idt_set_gate(5,  (uint64_t)isr5,  cs, IDT_GATE_INT);
    idt_set_gate(6,  (uint64_t)isr6,  cs, IDT_GATE_INT);
    idt_set_gate(7,  (uint64_t)isr7,  cs, IDT_GATE_INT);
    idt_set_gate(8,  (uint64_t)isr8,  cs, IDT_GATE_INT);
    idt_set_gate(9,  (uint64_t)isr9,  cs, IDT_GATE_INT);
    idt_set_gate(10, (uint64_t)isr10, cs, IDT_GATE_INT);
    idt_set_gate(11, (uint64_t)isr11, cs, IDT_GATE_INT);
    idt_set_gate(12, (uint64_t)isr12, cs, IDT_GATE_INT);
    idt_set_gate(13, (uint64_t)isr13, cs, IDT_GATE_INT);
    idt_set_gate(14, (uint64_t)isr14, cs, IDT_GATE_INT);
    idt_set_gate(15, (uint64_t)isr15, cs, IDT_GATE_INT);
    idt_set_gate(16, (uint64_t)isr16, cs, IDT_GATE_INT);
    idt_set_gate(17, (uint64_t)isr17, cs, IDT_GATE_INT);
    idt_set_gate(18, (uint64_t)isr18, cs, IDT_GATE_INT);
    idt_set_gate(19, (uint64_t)isr19, cs, IDT_GATE_INT);
    idt_set_gate(20, (uint64_t)isr20, cs, IDT_GATE_INT);
    idt_set_gate(21, (uint64_t)isr21, cs, IDT_GATE_INT);
    idt_set_gate(22, (uint64_t)isr22, cs, IDT_GATE_INT);
    idt_set_gate(23, (uint64_t)isr23, cs, IDT_GATE_INT);
    idt_set_gate(24, (uint64_t)isr24, cs, IDT_GATE_INT);
    idt_set_gate(25, (uint64_t)isr25, cs, IDT_GATE_INT);
    idt_set_gate(26, (uint64_t)isr26, cs, IDT_GATE_INT);
    idt_set_gate(27, (uint64_t)isr27, cs, IDT_GATE_INT);
    idt_set_gate(28, (uint64_t)isr28, cs, IDT_GATE_INT);
    idt_set_gate(29, (uint64_t)isr29, cs, IDT_GATE_INT);
    idt_set_gate(30, (uint64_t)isr30, cs, IDT_GATE_INT);
    idt_set_gate(31, (uint64_t)isr31, cs, IDT_GATE_INT);
}

/* Register remapped legacy PIC IRQ handlers. */
static void idt_register_irqs(void) {
    uint16_t cs = 0x08;

    idt_set_gate(32, (uint64_t)isr32, cs, IDT_GATE_INT);
    idt_set_gate(33, (uint64_t)isr33, cs, IDT_GATE_INT);
    idt_set_gate(34, (uint64_t)isr34, cs, IDT_GATE_INT);
    idt_set_gate(35, (uint64_t)isr35, cs, IDT_GATE_INT);
    idt_set_gate(36, (uint64_t)isr36, cs, IDT_GATE_INT);
    idt_set_gate(37, (uint64_t)isr37, cs, IDT_GATE_INT);
    idt_set_gate(38, (uint64_t)isr38, cs, IDT_GATE_INT);
    idt_set_gate(39, (uint64_t)isr39, cs, IDT_GATE_INT);
    idt_set_gate(40, (uint64_t)isr40, cs, IDT_GATE_INT);
    idt_set_gate(41, (uint64_t)isr41, cs, IDT_GATE_INT);
    idt_set_gate(42, (uint64_t)isr42, cs, IDT_GATE_INT);
    idt_set_gate(43, (uint64_t)isr43, cs, IDT_GATE_INT);
    idt_set_gate(44, (uint64_t)isr44, cs, IDT_GATE_INT);
    idt_set_gate(45, (uint64_t)isr45, cs, IDT_GATE_INT);
    idt_set_gate(46, (uint64_t)isr46, cs, IDT_GATE_INT);
    idt_set_gate(47, (uint64_t)isr47, cs, IDT_GATE_INT);
}

aios_status_t idt_init(void) {
    /* Zero out entire IDT */
    for (int i = 0; i < IDT_ENTRIES; i++) {
        idt[i].offset_low  = 0;
        idt[i].selector    = 0;
        idt[i].ist         = 0;
        idt[i].type_attr   = 0;
        idt[i].offset_mid  = 0;
        idt[i].offset_high = 0;
        idt[i].reserved    = 0;
    }

    /* Register exception handlers */
    idt_register_exceptions();
    idt_register_irqs();

    /* Set up IDT pointer */
    idt_ptr.limit = (uint16_t)(sizeof(idt) - 1);
    idt_ptr.base  = (uint64_t)&idt;

    /* Load IDT */
    idt_load(&idt_ptr);

    return AIOS_OK;
}

void exception_handler(interrupt_frame_t *frame) {
    uint64_t int_no = frame->int_no;

    if (int_no >= IRQ_BASE && int_no < (IRQ_BASE + IRQ_COUNT)) {
        if (int_no == KERNEL_TIMER_IRQ_VECTOR) {
            kernel_timer_irq_handler();
        }
        pic_send_eoi(int_no);
        return;
    }

    /* Print exception info to VGA */
    console_write_color("\n!!! EXCEPTION: ", VGA_LIGHT_RED, VGA_BLUE);
    if (int_no < 32) {
        console_write_color(exception_names[int_no], VGA_WHITE, VGA_RED);
    } else {
        kprintf("Unknown (%u)", int_no);
    }
    console_newline();

    kprintf("  Error Code: ");
    console_write_hex(frame->err_code);
    console_newline();
    kprintf("  RIP: ");
    console_write_hex(frame->rip);
    console_newline();
    kprintf("  CS:  ");
    console_write_hex(frame->cs);
    kprintf("  RFLAGS: ");
    console_write_hex(frame->rflags);
    console_newline();
    kprintf("  RSP: ");
    console_write_hex(frame->rsp);
    kprintf("  SS: ");
    console_write_hex(frame->ss);
    console_newline();

    /* Also output to serial for headless debugging */
    serial_printf("\n!!! EXCEPTION %u: %s\n", int_no,
                  (int_no < 32) ? exception_names[int_no] : "Unknown");
    serial_printf("  Error Code: %x\n", frame->err_code);
    serial_printf("  RIP: %x  CS: %x  RFLAGS: %x\n",
                  frame->rip, frame->cs, frame->rflags);
    serial_printf("  RSP: %x  SS: %x\n", frame->rsp, frame->ss);
    serial_printf("  RAX: %x  RBX: %x  RCX: %x  RDX: %x\n",
                  frame->rax, frame->rbx, frame->rcx, frame->rdx);

    /* Fatal exceptions: halt */
    if (int_no == 8 || int_no == 13 || int_no == 14 || int_no == 18) {
        kernel_panic("Fatal CPU exception - system halted");
    }
}

NORETURN void kernel_panic(const char *msg) {
    /* Disable interrupts */
    __asm__ volatile ("cli");

    /* VGA output */
    console_write_color("\n\n", VGA_WHITE, VGA_RED);
    console_write_color("*** KERNEL PANIC ***\n", VGA_WHITE, VGA_RED);
    console_write_color(msg, VGA_WHITE, VGA_RED);
    console_write_color("\n", VGA_WHITE, VGA_RED);
    console_write_color("System halted. Please reboot.\n", VGA_YELLOW, VGA_RED);

    /* Serial output */
    serial_printf("\n*** KERNEL PANIC ***\n");
    serial_printf("%s\n", (uint64_t)(uintptr_t)msg);
    serial_write("System halted. Please reboot.\n");

    /* Halt forever */
    while (1) {
        __asm__ volatile ("hlt");
    }
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
