/*
 * AIOS Kernel - Main Entry Point
 * AI-Native Operating System Kernel
 * 
 * This is the C entry point called from the assembly bootloader.
 * It initializes all kernel subsystems in the correct order.
 */

#include <kernel/types.h>
#include <kernel/selftest.h>
#include <lib/string.h>
#include <drivers/e1000.h>
#include <drivers/vga.h>
#include <drivers/serial.h>
#include <drivers/platform_probe.h>
#include <interrupt/idt.h>
#include <mm/tensor_mm.h>
#include <sched/ai_sched.h>
#include <hal/accel_hal.h>
#include <runtime/ai_syscall.h>
#include <runtime/autonomy.h>

/* Kernel version info */
#define AIOS_VERSION_MAJOR  0
#define AIOS_VERSION_MINOR  2
#define AIOS_VERSION_PATCH  0
#define AIOS_CODENAME       "Genesis"

/* Forward declarations */
static void print_banner(void);
static void print_boot_protocol(uint64_t multiboot_magic, uint64_t multiboot_info);
static void print_system_info(void);
static void init_subsystems(void);
static void run_selftests(void);

/* Subsystem init helper macro */
#define INIT_SUBSYSTEM(name, init_fn) do {                              \
    console_write_color("[INIT] ", VGA_YELLOW, VGA_BLUE);               \
    kprintf("%s... ", name);                                            \
    serial_printf("[INIT] %s... ", (uint64_t)(uintptr_t)name);         \
    aios_status_t _st = (init_fn);                                     \
    if (_st == AIOS_OK) {                                              \
        console_write_color("OK\n", VGA_LIGHT_GREEN, VGA_BLUE);        \
        serial_write("OK\n");                                           \
    } else {                                                            \
        console_write_color("FAIL\n", VGA_LIGHT_RED, VGA_BLUE);        \
        serial_write("FAIL\n");                                         \
        kernel_panic("Critical subsystem initialization failed");       \
    }                                                                   \
} while (0)

/*
 * kernel_main - Primary C entry point for the AIOS kernel
 * @multiboot_magic: Multiboot2 magic number for verification
 * @multiboot_info: Pointer to multiboot2 information structure
 */
void kernel_main(uint64_t multiboot_magic, uint64_t multiboot_info) {
    /* Initialize console first for output */
    console_init();
    
    /* Initialize serial console for headless debugging */
    serial_init();
    
    /* Display boot banner */
    print_banner();

    print_boot_protocol(multiboot_magic, multiboot_info);
    
    /* Print system information */
    print_system_info();
    
    /* Initialize all kernel subsystems */
    init_subsystems();
    
    /* Kernel ready */
    console_newline();
    console_write_color("=== AIOS Kernel Ready ===\n", VGA_LIGHT_GREEN, VGA_BLUE);
    console_write_color("AI-Native Operating System is operational.\n", VGA_WHITE, VGA_BLUE);
    console_write_color("All subsystems initialized successfully.\n", VGA_LIGHT_CYAN, VGA_BLUE);
    
    serial_write("\n=== AIOS Kernel Ready ===\n");
    serial_write("AI-Native Operating System is operational.\n");

    /* Enter kernel idle loop */
    kprintf("\n[KERNEL] Entering idle loop. System awaiting AI workloads...\n");
    serial_write("[KERNEL] Entering idle loop. System awaiting AI workloads...\n");
    
    /* Halt - in a real OS this would be the scheduler idle task */
    while (1) {
        __asm__ volatile ("hlt");
    }
}

static void print_boot_protocol(uint64_t multiboot_magic, uint64_t multiboot_info) {
    if (multiboot_magic == 0x36d76289) {
        kprintf("[BOOT] Multiboot2 verified. Info struct at ");
        console_write_hex(multiboot_info);
        console_newline();
        serial_printf("[BOOT] Multiboot2 verified. Info at %x\n", multiboot_info);
        return;
    }

    if (multiboot_magic == 0x2badb002) {
        console_write_color("[BOOT] Multiboot1 compatibility path active\n",
            VGA_YELLOW, VGA_BLUE);
        serial_printf("[BOOT] Multiboot1 compatibility path active. Info at %x\n",
            multiboot_info);
        return;
    }

    console_write_color("[BOOT] WARNING: Unknown boot handoff. Magic=",
        VGA_YELLOW, VGA_BLUE);
    console_write_hex(multiboot_magic);
    console_newline();
    serial_printf("[BOOT] WARNING: Unknown boot handoff. Magic=%x info=%x\n",
        multiboot_magic, multiboot_info);
}

static void print_banner(void) {
    console_write_color(
        "+======================================================+\n",
        VGA_LIGHT_CYAN, VGA_BLUE);
    console_write_color(
        "|                                                      |\n",
        VGA_LIGHT_CYAN, VGA_BLUE);
    console_write_color(
        "|     █████╗ ██╗ ██████╗ ███████╗                     |\n",
        VGA_WHITE, VGA_BLUE);
    console_write_color(
        "|    ██╔══██╗██║██╔═══██╗██╔════╝                     |\n",
        VGA_WHITE, VGA_BLUE);
    console_write_color(
        "|    ███████║██║██║   ██║███████╗                     |\n",
        VGA_WHITE, VGA_BLUE);
    console_write_color(
        "|    ██╔══██║██║██║   ██║╚════██║                     |\n",
        VGA_WHITE, VGA_BLUE);
    console_write_color(
        "|    ██║  ██║██║╚██████╔╝███████║                     |\n",
        VGA_WHITE, VGA_BLUE);
    console_write_color(
        "|    ╚═╝  ╚═╝╚═╝ ╚═════╝ ╚══════╝                     |\n",
        VGA_WHITE, VGA_BLUE);
    console_write_color(
        "|                                                      |\n",
        VGA_LIGHT_CYAN, VGA_BLUE);
    console_write_color(
        "|    AI-Native Operating System Kernel                 |\n",
        VGA_YELLOW, VGA_BLUE);

    kprintf("|    Version %d.%d.%d \"%s\"",
        AIOS_VERSION_MAJOR, AIOS_VERSION_MINOR, 
        AIOS_VERSION_PATCH, AIOS_CODENAME);
    console_write("                        |\n");

    console_write_color(
        "|                                                      |\n",
        VGA_LIGHT_CYAN, VGA_BLUE);
    console_write_color(
        "+======================================================+\n\n",
        VGA_LIGHT_CYAN, VGA_BLUE);

    /* Serial banner */
    serial_write("\n========================================\n");
    serial_write("  AIOS - AI-Native Operating System\n");
    serial_printf("  Version %u.%u.%u \"%s\"\n",
        (uint64_t)AIOS_VERSION_MAJOR, (uint64_t)AIOS_VERSION_MINOR,
        (uint64_t)AIOS_VERSION_PATCH, (uint64_t)(uintptr_t)AIOS_CODENAME);
    serial_write("========================================\n\n");
}

static void print_system_info(void) {
    console_write_color("[INFO] ", VGA_LIGHT_GREEN, VGA_BLUE);
    kprintf("Architecture: x86_64 (Long Mode)\n");
    
    console_write_color("[INFO] ", VGA_LIGHT_GREEN, VGA_BLUE);
    kprintf("Page Size: %u bytes | Huge Page: %u MB\n", 
        (uint64_t)PAGE_SIZE, (uint64_t)(HUGE_PAGE_SIZE / MB(1)));
    
    console_write_color("[INFO] ", VGA_LIGHT_GREEN, VGA_BLUE);
    kprintf("Tensor Alignment: %u bytes (AVX-512 optimized)\n", 
        (uint64_t)TENSOR_ALIGN);
    
    console_write_color("[INFO] ", VGA_LIGHT_GREEN, VGA_BLUE);
    kprintf("Max AI Tasks: %u | Max Accelerators: %u\n",
        (uint64_t)MAX_AI_TASKS, (uint64_t)MAX_ACCELERATORS);
    
    console_newline();

    serial_printf("[INFO] Architecture: x86_64 (Long Mode)\n");
    serial_printf("[INFO] Page Size: %u bytes | Huge Page: %u MB\n",
        (uint64_t)PAGE_SIZE, (uint64_t)(HUGE_PAGE_SIZE / MB(1)));
    serial_printf("[INFO] Tensor Alignment: %u bytes (AVX-512)\n",
        (uint64_t)TENSOR_ALIGN);
    serial_printf("[INFO] Max AI Tasks: %u | Max Accelerators: %u\n",
        (uint64_t)MAX_AI_TASKS, (uint64_t)MAX_ACCELERATORS);
}

static void init_subsystems(void) {
    /* 1. IDT - must be first to catch any exceptions during init */
    INIT_SUBSYSTEM("Interrupt Descriptor Table (IDT)", idt_init());

    /* 2. Tensor Memory Manager */
    INIT_SUBSYSTEM("Tensor Memory Manager", tensor_mm_init());
    
    /* 3. AI Workload Scheduler */
    INIT_SUBSYSTEM("AI Workload Scheduler", ai_sched_init());

    /* 4. Boot-time diagnostics and performance profiling */
    run_selftests();
    
    /* 5. Accelerator HAL */
    INIT_SUBSYSTEM("Accelerator HAL", accel_hal_init());

    /* 6. Minimal peripheral discovery */
    INIT_SUBSYSTEM("Peripheral Probe Layer", platform_probe_init());

    /* 7. Intel E1000 network bootstrap */
    INIT_SUBSYSTEM("Intel E1000 Ethernet", e1000_driver_init());

    /* 8. AI System Call Interface */
    INIT_SUBSYSTEM("AI System Call Interface", ai_syscall_init());

    /* 9. Autonomy Control Plane */
    INIT_SUBSYSTEM("Autonomy Control Plane", autonomy_init());
}

static void run_selftests(void) {
    memory_selftest_result_t mem_result;
    aios_status_t status = kernel_memory_selftest_run(&mem_result);

    if (status != AIOS_OK) {
        console_write_color("[SELFTEST] Memory microbench FAIL\n",
            VGA_LIGHT_RED, VGA_BLUE);
        serial_write("[SELFTEST] Memory microbench FAIL\n");
        kernel_panic("Boot-time memory selftest failed");
    }

    kernel_memory_selftest_print(&mem_result);
}
