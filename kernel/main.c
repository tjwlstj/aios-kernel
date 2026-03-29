/*
 * AIOS Kernel - Main Entry Point
 * AI-Native Operating System Kernel
 * 
 * This is the C entry point called from the assembly bootloader.
 * It initializes all kernel subsystems in the correct order.
 */

#include <kernel/types.h>
#include <kernel/acpi.h>
#include <kernel/health.h>
#include <kernel/selftest.h>
#include <kernel/time.h>
#include <lib/string.h>
#include <drivers/e1000.h>
#include <drivers/pci_core.h>
#include <drivers/storage_host.h>
#include <drivers/usb_host.h>
#include <drivers/vga.h>
#include <drivers/serial.h>
#include <drivers/platform_probe.h>
#include <interrupt/idt.h>
#include <mm/tensor_mm.h>
#include <mm/memory_fabric.h>
#include <sched/ai_sched.h>
#include <hal/accel_hal.h>
#include <runtime/ai_syscall.h>
#include <runtime/autonomy.h>
#include <runtime/slm_orchestrator.h>

/* Kernel version info */
#define AIOS_VERSION_MAJOR  0
#define AIOS_VERSION_MINOR  2
#define AIOS_VERSION_PATCH  0
#define AIOS_CODENAME       "Genesis"

/* Forward declarations */
static void print_banner(void);
static void print_boot_protocol(uint64_t multiboot_magic, uint64_t multiboot_info);
static void print_system_info(void);
static void init_subsystems(uint64_t multiboot_magic, uint64_t multiboot_info);
static void run_selftests(void);
static void finalize_runtime_health(void);
static void print_health_summary(void);
static void enforce_stability_policy(void);

/* Subsystem init helper macro */
#define INIT_SUBSYSTEM(id, name, init_fn) do {                          \
    console_write_color("[INIT] ", VGA_YELLOW, VGA_BLUE);               \
    kprintf("%s... ", name);                                            \
    serial_printf("[INIT] %s... ", (uint64_t)(uintptr_t)name);         \
    aios_status_t _st = (init_fn);                                     \
    kernel_health_mark(id,                                             \
        (_st == AIOS_OK) ? KERNEL_HEALTH_OK : KERNEL_HEALTH_FAILED,    \
        _st);                                                          \
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
    kernel_health_init();
    
    /* Display boot banner */
    print_banner();

    print_boot_protocol(multiboot_magic, multiboot_info);
    
    /* Print system information */
    print_system_info();
    
    /* Initialize all kernel subsystems */
    init_subsystems(multiboot_magic, multiboot_info);
    print_health_summary();
    enforce_stability_policy();
    
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

static void init_subsystems(uint64_t multiboot_magic, uint64_t multiboot_info) {
    /* 1. IDT - must be first to catch any exceptions during init */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_IDT,
        "Interrupt Descriptor Table (IDT)", idt_init());

    /* 2. Common monotonic time source */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_TIME,
        "Kernel Time Source", kernel_time_init());

    /* 3. ACPI fabric discovery */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_ACPI,
        "ACPI Fabric Parser", acpi_init(multiboot_magic, multiboot_info));

    /* 4. PCI core */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_PCI_CORE,
        "PCI Core", pci_core_init());

    /* 5. Tensor Memory Manager */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_TENSOR_MM,
        "Tensor Memory Manager", tensor_mm_init());
    
    /* 6. AI Workload Scheduler */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_SCHED,
        "AI Workload Scheduler", ai_sched_init());

    /* 7. Boot-time diagnostics and performance profiling */
    run_selftests();
    
    /* 8. Accelerator HAL */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_ACCEL, "Accelerator HAL", accel_hal_init());

    /* 9. Minimal peripheral discovery */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_PCI_PROBE,
        "Peripheral Probe Layer", platform_probe_init());

    /* 10. Multi-agent shared memory / zero-copy planning baseline */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_MEMORY_FABRIC,
        "Memory Fabric Foundation", memory_fabric_init());

    /* 11. Intel E1000 network bootstrap */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_NETWORK,
        "Intel E1000 Ethernet", e1000_driver_init());

    /* 12. Minimal USB host bootstrap */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_USB, "USB Host Bootstrap", usb_host_init());

    /* 13. Minimal storage host bootstrap */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_STORAGE,
        "Storage Host Bootstrap", storage_host_init());

    /* Finalize runtime subsystem health before control planes use it */
    finalize_runtime_health();

    /* 14. AI System Call Interface */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_SYSCALL,
        "AI System Call Interface", ai_syscall_init());

    /* 15. Autonomy Control Plane */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_AUTONOMY,
        "Autonomy Control Plane", autonomy_init());

    /* 16. SLM Hardware Orchestrator */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_SLM,
        "SLM Hardware Orchestrator", slm_orchestrator_init());
}

static void run_selftests(void) {
    memory_selftest_result_t mem_result;
    aios_status_t status = kernel_memory_selftest_run(&mem_result);

    if (status != AIOS_OK) {
        kernel_health_mark(KERNEL_SUBSYSTEM_SELFTEST, KERNEL_HEALTH_FAILED, status);
        console_write_color("[SELFTEST] Memory microbench FAIL\n",
            VGA_LIGHT_RED, VGA_BLUE);
        serial_write("[SELFTEST] Memory microbench FAIL\n");
        kernel_panic("Boot-time memory selftest failed");
    }

    kernel_health_mark(KERNEL_SUBSYSTEM_SELFTEST, KERNEL_HEALTH_OK, AIOS_OK);
    kernel_memory_selftest_print(&mem_result);
}

static void finalize_runtime_health(void) {
    const acpi_info_t *acpi = acpi_info();
    const pci_core_summary_t *pci = pci_core_summary();
    const platform_probe_summary_t *probe = platform_probe_summary();
    e1000_driver_info_t nic;
    usb_host_info_t usb;
    storage_host_info_t storage;

    if (acpi_ready()) {
        kernel_health_mark(KERNEL_SUBSYSTEM_ACPI, KERNEL_HEALTH_OK, AIOS_OK);
    } else if (acpi->rsdp_found) {
        kernel_health_mark(KERNEL_SUBSYSTEM_ACPI, KERNEL_HEALTH_DEGRADED,
            AIOS_ERR_IO);
    } else {
        kernel_health_mark(KERNEL_SUBSYSTEM_ACPI, KERNEL_HEALTH_UNKNOWN,
            AIOS_ERR_NODEV);
    }

    if (probe->total_pci_devices > 0) {
        kernel_health_mark(KERNEL_SUBSYSTEM_PCI_CORE,
            (pci->ecam_available || pci->total_functions > 0) ? KERNEL_HEALTH_OK
                                                              : KERNEL_HEALTH_DEGRADED,
            AIOS_OK);
    } else {
        kernel_health_mark(KERNEL_SUBSYSTEM_PCI_CORE,
            KERNEL_HEALTH_DEGRADED, AIOS_ERR_NODEV);
    }

    if (e1000_driver_info(&nic) == AIOS_OK && nic.present) {
        if (!nic.ready || !nic.link_up || nic.last_tx_status != AIOS_OK) {
            kernel_health_mark(KERNEL_SUBSYSTEM_NETWORK,
                KERNEL_HEALTH_DEGRADED,
                (nic.last_tx_status != AIOS_OK) ? nic.last_tx_status : AIOS_ERR_IO);
        } else {
            kernel_health_mark(KERNEL_SUBSYSTEM_NETWORK,
                KERNEL_HEALTH_OK, AIOS_OK);
        }
    } else if (e1000_driver_ready()) {
        kernel_health_mark(KERNEL_SUBSYSTEM_NETWORK,
            KERNEL_HEALTH_OK, AIOS_OK);
    } else {
        kernel_health_mark(KERNEL_SUBSYSTEM_NETWORK,
            KERNEL_HEALTH_UNKNOWN, AIOS_ERR_NODEV);
    }

    if (usb_host_info(&usb) == AIOS_OK && usb.present) {
        kernel_health_mark(KERNEL_SUBSYSTEM_USB,
            usb.ready ? KERNEL_HEALTH_OK : KERNEL_HEALTH_DEGRADED,
            usb.last_init_status);
    } else {
        kernel_health_mark(KERNEL_SUBSYSTEM_USB,
            KERNEL_HEALTH_UNKNOWN, AIOS_ERR_NODEV);
    }

    if (storage_host_info(&storage) == AIOS_OK && storage.present) {
        kernel_health_mark(KERNEL_SUBSYSTEM_STORAGE,
            storage.ready ? KERNEL_HEALTH_OK : KERNEL_HEALTH_DEGRADED,
            storage.last_init_status);
    } else {
        kernel_health_mark(KERNEL_SUBSYSTEM_STORAGE,
            KERNEL_HEALTH_UNKNOWN, AIOS_ERR_NODEV);
    }
}

static void print_health_summary(void) {
    kernel_health_summary_t summary;
    kernel_health_get_summary(&summary);

    console_write_color("[HEALTH] ", VGA_LIGHT_GREEN, VGA_BLUE);
    kprintf("stability=%s ok=%u degraded=%u failed=%u unknown=%u io_degraded=%u\n",
        (uint64_t)(uintptr_t)kernel_stability_name(summary.level),
        (uint64_t)summary.ok_count,
        (uint64_t)summary.degraded_count,
        (uint64_t)summary.failed_count,
        (uint64_t)summary.unknown_count,
        (uint64_t)summary.io_degraded);
    serial_printf("[HEALTH] stability=%s ok=%u degraded=%u failed=%u unknown=%u io_degraded=%u\n",
        (uint64_t)(uintptr_t)kernel_stability_name(summary.level),
        (uint64_t)summary.ok_count,
        (uint64_t)summary.degraded_count,
        (uint64_t)summary.failed_count,
        (uint64_t)summary.unknown_count,
        (uint64_t)summary.io_degraded);
}

static void enforce_stability_policy(void) {
    kernel_health_summary_t summary;
    kernel_health_get_summary(&summary);

    if (!summary.autonomy_allowed) {
        autonomy_set_safe_mode(true);
        serial_write("[HEALTH] Autonomy forced into safe mode by stability gate\n");
        return;
    }

    serial_write("[HEALTH] Stability gate allows autonomy escalation\n");
}
