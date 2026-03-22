/*
 * AIOS Kernel - Main Entry Point
 * AI-Native Operating System Kernel
 * 
 * This is the C entry point called from the assembly bootloader.
 * It initializes all kernel subsystems in the correct order.
 */

#include <kernel/types.h>
#include <drivers/vga.h>
#include <mm/tensor_mm.h>
#include <sched/ai_sched.h>
#include <hal/accel_hal.h>
#include <runtime/ai_syscall.h>
#include <runtime/autonomy.h>

/* Kernel version info */
#define AIOS_VERSION_MAJOR  0
#define AIOS_VERSION_MINOR  1
#define AIOS_VERSION_PATCH  0
#define AIOS_CODENAME       "Genesis"

/* Forward declarations */
static void print_banner(void);
static void print_system_info(void);
static void init_subsystems(void);

/*
 * kernel_main - Primary C entry point for the AIOS kernel
 * @multiboot_info: Pointer to multiboot2 information structure
 * @multiboot_magic: Multiboot2 magic number for verification
 */
void kernel_main(uint64_t multiboot_magic, uint64_t multiboot_info) {
    /* Initialize console first for output */
    console_init();
    
    /* Display boot banner */
    print_banner();
    
    /* Verify multiboot2 boot */
    if (multiboot_magic == 0x36d76289) {
        kprintf("[BOOT] Multiboot2 verified. Info struct at ");
        console_write_hex(multiboot_info);
        console_newline();
    } else {
        console_write_color("[BOOT] WARNING: Non-standard boot detected\n", 
                          VGA_YELLOW, VGA_BLUE);
    }
    
    /* Print system information */
    print_system_info();
    
    /* Initialize all kernel subsystems */
    init_subsystems();
    
    /* Kernel ready */
    console_newline();
    console_write_color("=== AIOS Kernel Ready ===\n", VGA_LIGHT_GREEN, VGA_BLUE);
    console_write_color("AI-Native Operating System is operational.\n", VGA_WHITE, VGA_BLUE);
    console_write_color("All subsystems initialized successfully.\n", VGA_LIGHT_CYAN, VGA_BLUE);
    
    /* Enter kernel idle loop */
    kprintf("\n[KERNEL] Entering idle loop. System awaiting AI workloads...\n");
    
    /* Halt - in a real OS this would be the scheduler idle task */
    while (1) {
        __asm__ volatile ("hlt");
    }
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
}

static void init_subsystems(void) {
    aios_status_t status;
    
    /* 1. Initialize Tensor Memory Manager */
    console_write_color("[INIT] ", VGA_YELLOW, VGA_BLUE);
    kprintf("Tensor Memory Manager... ");
    status = tensor_mm_init();
    if (status == AIOS_OK) {
        console_write_color("OK\n", VGA_LIGHT_GREEN, VGA_BLUE);
    } else {
        console_write_color("FAIL\n", VGA_LIGHT_RED, VGA_BLUE);
    }
    
    /* 2. Initialize AI Workload Scheduler */
    console_write_color("[INIT] ", VGA_YELLOW, VGA_BLUE);
    kprintf("AI Workload Scheduler... ");
    status = ai_sched_init();
    if (status == AIOS_OK) {
        console_write_color("OK\n", VGA_LIGHT_GREEN, VGA_BLUE);
    } else {
        console_write_color("FAIL\n", VGA_LIGHT_RED, VGA_BLUE);
    }
    
    /* 3. Initialize Accelerator HAL */
    console_write_color("[INIT] ", VGA_YELLOW, VGA_BLUE);
    kprintf("Accelerator HAL... ");
    status = accel_hal_init();
    if (status == AIOS_OK) {
        console_write_color("OK\n", VGA_LIGHT_GREEN, VGA_BLUE);
    } else {
        console_write_color("FAIL\n", VGA_LIGHT_RED, VGA_BLUE);
    }
    
    /* 4. Initialize AI System Call Interface */
    console_write_color("[INIT] ", VGA_YELLOW, VGA_BLUE);
    kprintf("AI System Call Interface... ");
    status = ai_syscall_init();
    if (status == AIOS_OK) {
        console_write_color("OK\n", VGA_LIGHT_GREEN, VGA_BLUE);
    } else {
        console_write_color("FAIL\n", VGA_LIGHT_RED, VGA_BLUE);
    }

    /* 5. Initialize Autonomy Control Plane */
    console_write_color("[INIT] ", VGA_YELLOW, VGA_BLUE);
    kprintf("Autonomy Control Plane... ");
    status = autonomy_init();
    if (status == AIOS_OK) {
        console_write_color("OK\n", VGA_LIGHT_GREEN, VGA_BLUE);
    } else {
        console_write_color("FAIL\n", VGA_LIGHT_RED, VGA_BLUE);
    }
}
