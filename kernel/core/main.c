/*
 * AIOS Kernel - Main Entry Point
 * AI-Native Operating System Kernel
 * 
 * This is the C entry point called from the assembly bootloader.
 * It initializes all kernel subsystems in the correct order.
 */

#include <kernel/types.h>
#include <kernel/acpi.h>
#include <kernel/cpu_sec.h>
#include <kernel/health.h>
#include <kernel/stack_guard.h>
#include <kernel/user_exec.h>
#include <kernel/kernel_room.h>
#include <kernel/selftest.h>
#include <kernel/time.h>
#include <kernel/user_mode.h>
#include <kernel/process.h>
#include <lib/string.h>
#include <drivers/e1000.h>
#include <drivers/pci_core.h>
#include <drivers/storage_host.h>
#include <drivers/usb_host.h>
#include <drivers/vga.h>
#include <drivers/serial.h>
#include <drivers/platform_probe.h>
#include <interrupt/idt.h>
#include <interrupt/trapframe.h>
#include <mm/tensor_mm.h>
#include <mm/memory_fabric.h>
#include <sched/ai_sched.h>
#include <sched/kthread.h>
#include <hal/accel_hal.h>
#include <runtime/ai_syscall.h>
#include <runtime/autonomy.h>
#include <runtime/ai_resource.h>
#include <runtime/ai_pressure.h>
#include <runtime/nodebit.h>
#include <runtime/node_pipeline.h>
#include <runtime/slm_orchestrator.h>
#include <mm/heap.h>
#include <mm/address_space.h>
#include <drivers/keyboard.h>
#include <kernel/shell.h>

/* Kernel version info */
#define AIOS_VERSION_MAJOR  0
#define AIOS_VERSION_MINOR  2
#define AIOS_VERSION_PATCH  0
#define AIOS_VERSION_PRERELEASE "beta.6"
#define AIOS_CODENAME       "Genesis"

#define MULTIBOOT2_BOOT_MAGIC    0x36d76289UL
#define MULTIBOOT2_INFO_LIMIT    MB(1)
#define MULTIBOOT2_PHYS_LIMIT    0x100000000ULL

typedef struct PACKED {
    uint32_t total_size;
    uint32_t reserved;
} multiboot2_info_header_t;

typedef struct PACKED {
    uint32_t type;
    uint32_t size;
} multiboot2_tag_header_t;

/* Forward declarations */
static void print_banner(void);
static bool print_boot_protocol(uint64_t multiboot_magic, uint64_t multiboot_info);
static void print_system_info(void);
static void init_subsystems(uint64_t multiboot_magic, uint64_t multiboot_info);
static void run_selftests(void);
static void run_observe_dispatch_selftest(void);
static void finalize_runtime_health(void);
static void print_health_summary(void);
static void enforce_stability_policy(void);
static void print_boot_ready_banner(void);
static void init_subsystem(kernel_subsystem_id_t id, const char *name, aios_status_t status);

/* Subsystem init helper macro */
#define INIT_SUBSYSTEM(id, name, init_fn) do {                          \
    aios_status_t _st = (init_fn);                                     \
    init_subsystem((id), (name), _st);                                 \
} while (0)

/*
 * kernel_main - Primary C entry point for the AIOS kernel
 * @multiboot_magic: Multiboot2 magic number for verification
 * @multiboot_info: Pointer to multiboot2 information structure
 */
void kernel_main(uint64_t multiboot_magic, uint64_t multiboot_info) {
    bool multiboot2_handoff_ok;
    aios_status_t user_status;

    /* Initialize console first for output */
    console_init();
    
    /* Initialize serial console for headless debugging */
    serial_init();
    kernel_health_init();

    /* Arm CPU/compiler-level mitigations before any deeper init runs.
     * Safe here: kernel_main never returns, so re-seeding the stack
     * canary cannot break a live instrumented frame. */
    stack_guard_init();
    cpu_security_init();


    /* Display boot banner */
    print_banner();

    multiboot2_handoff_ok =
        print_boot_protocol(multiboot_magic, multiboot_info);
    
    /* Print system information */
    print_system_info();
    
    /* Initialize all kernel subsystems */
    /* A malformed handoff must never reach parsers that walk boot tags. */
    init_subsystems(multiboot2_handoff_ok ? multiboot_magic : 0,
        multiboot2_handoff_ok ? multiboot_info : 0);

    /* Timer-driven preemption check. Runs after the timer IRQ subsystem so
     * the PIC is remapped (a stray IRQ0 would otherwise be vector 8/#DF);
     * the selftest arms preemption, enables interrupts inside its worker
     * threads, and disarms before returning with IF still masked. */
    if (kthread_preempt_selftest() != AIOS_OK) {
        kernel_health_mark(KERNEL_SUBSYSTEM_SCHED,
            KERNEL_HEALTH_DEGRADED, AIOS_ERR_IO);
    }

    run_observe_dispatch_selftest();
    user_status = user_mode_scaffold_init();
    if (user_status == AIOS_OK) {
        user_status = bootstrap_process_init();
    }
    if (user_status != AIOS_OK) {
        kernel_health_mark(KERNEL_SUBSYSTEM_SCHED,
            KERNEL_HEALTH_DEGRADED, user_status);
    }

    /* Execute both static bootstrap processes sequentially through their own
     * private CR3, process run state, and TSS rsp0 entry stack. This remains
     * a bounded synchronous proof, not timer-preemptive ring3 scheduling. */
    if (user_mode_scaffold_ready() && bootstrap_process_ready()) {
        user_status = user_exec_run_bootstrap_pair();
        if (user_status != AIOS_OK) {
            kernel_health_mark(KERNEL_SUBSYSTEM_SCHED,
                KERNEL_HEALTH_DEGRADED, user_status);
        }
    } else {
        serial_write("[USER] ring3 exec SKIP: process scaffold not ready\n");
    }

    kernel_room_dump();
    print_health_summary();
    enforce_stability_policy();
    
    /* Kernel ready */
    print_boot_ready_banner();

    /* Launch interactive shell */
    kprintf("\n[KERNEL] Boot complete. Launching interactive shell...\n");
    serial_write("[KERNEL] Boot complete. Launching interactive shell...\n");

    shell_init();
    shell_run(); /* never returns */

    /* Unreachable — safety net */
    while (1) {
        __asm__ volatile ("hlt");
    }
}

static void init_subsystem(kernel_subsystem_id_t id, const char *name, aios_status_t status) {
    const kernel_subsystem_health_t *entry = kernel_health_get(id);
    bool required = !entry || entry->required;

    console_write_color("[INIT] ", VGA_YELLOW, VGA_BLUE);
    kprintf("%s... ", name);
    serial_printf("[INIT] %s... ", (uint64_t)(uintptr_t)name);

    kernel_health_mark(id,
        (status == AIOS_OK) ? KERNEL_HEALTH_OK
                            : (required ? KERNEL_HEALTH_FAILED : KERNEL_HEALTH_DEGRADED),
        status);

    if (status == AIOS_OK) {
        console_write_color("OK\n", VGA_LIGHT_GREEN, VGA_BLUE);
        serial_write("OK\n");
        return;
    }

    if (required) {
        console_write_color("FAIL\n", VGA_LIGHT_RED, VGA_BLUE);
        serial_printf("FAIL status=%d\n", (int64_t)status);
        kernel_panic("Critical subsystem initialization failed");
    }

    console_write_color("DEGRADED\n", VGA_YELLOW, VGA_BLUE);
    serial_printf("DEGRADED status=%d\n", (int64_t)status);
}

static bool multiboot2_handoff_valid(uint64_t info_addr, uint32_t *size_out) {
    const multiboot2_info_header_t *header;
    uint32_t total_size;
    uint64_t cursor;
    uint64_t limit;

    if (info_addr == 0 || (info_addr & 7UL) != 0 ||
        info_addr > MULTIBOOT2_PHYS_LIMIT - sizeof(*header)) {
        return false;
    }

    header = (const multiboot2_info_header_t *)(uintptr_t)info_addr;
    total_size = header->total_size;
    if (header->reserved != 0 ||
        total_size < sizeof(*header) + sizeof(multiboot2_tag_header_t) ||
        total_size > MULTIBOOT2_INFO_LIMIT ||
        (total_size & 7U) != 0 ||
        info_addr > MULTIBOOT2_PHYS_LIMIT - total_size) {
        return false;
    }

    cursor = info_addr + sizeof(*header);
    limit = info_addr + total_size;
    while (cursor <= limit - sizeof(multiboot2_tag_header_t)) {
        const multiboot2_tag_header_t *tag =
            (const multiboot2_tag_header_t *)(uintptr_t)cursor;
        uint64_t advance;

        if (tag->size < sizeof(*tag)) {
            return false;
        }
        if (tag->type == 0) {
            if (tag->size != sizeof(*tag) ||
                cursor != limit - sizeof(*tag)) {
                return false;
            }
            if (size_out) {
                *size_out = total_size;
            }
            return true;
        }

        advance = ((uint64_t)tag->size + 7ULL) & ~7ULL;
        if (advance > limit - cursor) {
            return false;
        }
        cursor += advance;
    }

    return false;
}

static bool print_boot_protocol(uint64_t multiboot_magic, uint64_t multiboot_info) {
    if (multiboot_magic == MULTIBOOT2_BOOT_MAGIC) {
        uint32_t info_size = 0;
        bool handoff_valid = multiboot2_handoff_valid(multiboot_info, &info_size);

        kprintf("[BOOT] Multiboot2 verified. Info struct at ");
        console_write_hex(multiboot_info);
        console_newline();
        serial_printf("[BOOT] Multiboot2 verified. Info at %x\n", multiboot_info);
        serial_printf("[BOOT] Multiboot2 handoff %s size=%u aligned=%u\n",
            handoff_valid ? "PASS" : "FAIL",
            (uint64_t)info_size,
            (multiboot_info & 7UL) == 0 ? 1ULL : 0ULL);
        if (!handoff_valid) {
            serial_write("[BOOT] Multiboot2 handoff rejected for consumers\n");
        }
        return handoff_valid;
    }

    if (multiboot_magic == 0x2badb002) {
        console_write_color("[BOOT] Multiboot1 compatibility path active\n",
            VGA_YELLOW, VGA_BLUE);
        serial_printf("[BOOT] Multiboot1 compatibility path active. Info at %x\n",
            multiboot_info);
        return false;
    }

    console_write_color("[BOOT] WARNING: Unknown boot handoff. Magic=",
        VGA_YELLOW, VGA_BLUE);
    console_write_hex(multiboot_magic);
    console_newline();
    serial_printf("[BOOT] WARNING: Unknown boot handoff. Magic=%x info=%x\n",
        multiboot_magic, multiboot_info);
    return false;
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

    kprintf("|    Version %d.%d.%d-%s \"%s\"",
        AIOS_VERSION_MAJOR, AIOS_VERSION_MINOR, 
        AIOS_VERSION_PATCH, AIOS_VERSION_PRERELEASE, AIOS_CODENAME);
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
    serial_printf("  Version %u.%u.%u-%s \"%s\"\n",
        (uint64_t)AIOS_VERSION_MAJOR, (uint64_t)AIOS_VERSION_MINOR,
        (uint64_t)AIOS_VERSION_PATCH,
        AIOS_VERSION_PRERELEASE,
        AIOS_CODENAME);
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
    /* 0. Kernel heap — available to all subsequent subsystems */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_HEAP,
        "Kernel Heap (kmalloc/kfree)", heap_init());
    if (heap_lock_selftest() != AIOS_OK) {
        kernel_health_mark(KERNEL_SUBSYSTEM_HEAP,
            KERNEL_HEALTH_DEGRADED, AIOS_ERR_IO);
        serial_write("[HEAP] lock selftest FAIL\n");
    }

    /* 1. IDT - must be first to catch any exceptions during init */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_IDT,
        "Interrupt Descriptor Table (IDT)", idt_init());

    /* Prove the C/NASM trapframe contract on the real isr_common_stub path
     * before anything else depends on frame layout. A broken contract means
     * every later exception frame would be misread: fail-stop. Safe here:
     * int3 is a software exception, so no sti/PIC ordering is involved. */
    if (trapframe_contract_selftest() != AIOS_OK) {
        kernel_health_mark(KERNEL_SUBSYSTEM_SELFTEST,
            KERNEL_HEALTH_FAILED, AIOS_ERR_IO);
        kernel_panic("Trapframe contract selftest failed");
    }

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
    if (kthread_selftest() != AIOS_OK) {
        kernel_health_mark(KERNEL_SUBSYSTEM_SCHED,
            KERNEL_HEALTH_DEGRADED, AIOS_ERR_IO);
    }
    if (address_space_selftest() != AIOS_OK) {
        kernel_health_mark(KERNEL_SUBSYSTEM_SCHED,
            KERNEL_HEALTH_DEGRADED, AIOS_ERR_IO);
    }
    if (address_space_user_isolation_selftest() != AIOS_OK) {
        kernel_health_mark(KERNEL_SUBSYSTEM_SCHED,
            KERNEL_HEALTH_DEGRADED, AIOS_ERR_IO);
    }

    /* 7. PIT IRQ0 tick source for scheduler accounting */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_TIME,
        "Kernel Timer IRQ", kernel_timer_irq_init(KERNEL_TIMER_DEFAULT_HZ));

    /* 8. Boot-time diagnostics and performance profiling */
    run_selftests();
    
    /* 9. Accelerator HAL */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_ACCEL, "Accelerator HAL", accel_hal_init());

    /* 10. Minimal peripheral discovery */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_PCI_PROBE,
        "Peripheral Probe Layer", platform_probe_init());

    /* 11. Multi-agent shared memory / zero-copy planning baseline */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_MEMORY_FABRIC,
        "Memory Fabric Foundation", memory_fabric_init());

    /* 12. Intel E1000 network bootstrap */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_NETWORK,
        "Intel E1000 Ethernet", e1000_driver_init());

    /* 13. Minimal USB host bootstrap */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_USB, "USB Host Bootstrap", usb_host_init());

    /* 14. Minimal storage host bootstrap */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_STORAGE,
        "Storage Host Bootstrap", storage_host_init());

    /* Finalize runtime subsystem health before control planes use it */
    finalize_runtime_health();

    /* 15. AI System Call Interface */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_SYSCALL,
        "AI System Call Interface", ai_syscall_init());

    /* 16. Autonomy Control Plane */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_AUTONOMY,
        "Autonomy Control Plane", autonomy_init());

    /* 17. SLM Hardware Orchestrator */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_SLM,
        "SLM Hardware Orchestrator", slm_orchestrator_init());
    if (slm_plan_apply_selftest() != AIOS_OK) {
        kernel_health_mark(KERNEL_SUBSYSTEM_SLM,
            KERNEL_HEALTH_DEGRADED, AIOS_ERR_IO);
        serial_write("[SLM] plan apply selftest FAIL\n");
    }

    /* 18. NodeBit Capability Policy Gate */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_NODEBIT,
        "NodeBit Policy Gate", nodebit_init());

    /* 19. Node Pipeline Orchestrator (SYS_PIPE_*, gated by NodeBit) */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_NODE_PIPELINE,
        "Node Pipeline Orchestrator", node_pipeline_init());
    if (node_pipeline_selftest() != AIOS_OK) {
        kernel_health_mark(KERNEL_SUBSYSTEM_NODE_PIPELINE,
            KERNEL_HEALTH_DEGRADED, AIOS_ERR_IO);
        serial_write("[PIPE] selftest FAIL\n");
    }

    /*
     * 20. AI Resource Ledger
     *
     * Five existing observers feed a fixed, versioned aggregate table. The
     * ledger is read-only and intentionally has no reserve/apply syscall.
     */
    aios_status_t resource_status = ai_resource_init();
    if (resource_status != AIOS_OK) {
        kernel_health_mark(KERNEL_SUBSYSTEM_SELFTEST,
            KERNEL_HEALTH_FAILED, resource_status);
        kernel_panic("AI resource ledger selftest failed");
    }

    /*
     * 21. AI Pressure Tracker
     *
     * This is observation-only and has no scheduler apply edge. Its invariant
     * selftest is part of the required boot proof, so an invalid reducer or
     * source snapshot fails through the existing SELFTEST health record.
     */
    aios_status_t pressure_status = ai_pressure_init();
    if (pressure_status != AIOS_OK) {
        kernel_health_mark(KERNEL_SUBSYSTEM_SELFTEST,
            KERNEL_HEALTH_FAILED, pressure_status);
        kernel_panic("AI pressure tracker selftest failed");
    }

    /* 22. PS/2 Keyboard (unmasks PIC IRQ1 — requires IDT + timer ready) */
    INIT_SUBSYSTEM(KERNEL_SUBSYSTEM_KEYBOARD,
        "PS/2 Keyboard", keyboard_init());
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

/*
 * Drive the observation syscalls through the real dispatcher once all
 * subsystems are up, proving the numbers a userspace agent would read
 * match what the boot selftests just produced (pipeline executed once,
 * one SLM plan applied with a non-zero timed latency).
 */
static void run_observe_dispatch_selftest(void) {
    node_pipeline_snapshot_t pipe_snap = {0};
    slm_plan_observation_t slm_obs = {0};
    ai_resource_snapshot_t resource_snap = {0};
    ai_resource_snapshot_request_t resource_req = {
        .schema_version = AI_RESOURCE_SCHEMA_VERSION,
        .output_size = (uint32_t)sizeof(resource_snap),
        .output_addr = (uint64_t)(uintptr_t)&resource_snap,
    };

    int64_t pipe_status = ai_syscall_dispatch(SYS_PIPE_STATS,
        (uint64_t)(uintptr_t)&pipe_snap, 0, 0, 0, 0);
    int64_t slm_status = ai_syscall_dispatch(SYS_SLM_PLAN_OBSERVE,
        (uint64_t)(uintptr_t)&slm_obs, 0, 0, 0, 0);
    int64_t resource_status = ai_syscall_dispatch(SYS_INFO_RESOURCE,
        (uint64_t)(uintptr_t)&resource_req, 0, 0, 0, 0);

    bool ok = pipe_status == (int64_t)AIOS_OK &&
              slm_status == (int64_t)AIOS_OK &&
              resource_status == (int64_t)AIOS_OK &&
              pipe_snap.max_pipelines == NODE_PIPELINE_MAX &&
              pipe_snap.total_executions >= 1 &&
              slm_obs.apply_ok >= 1 &&
              slm_obs.last_latency_ns > 0 &&
              ai_resource_snapshot_valid(&resource_snap) &&
              resource_snap.observation_only == 1U &&
              resource_snap.entry_count == AI_RESOURCE_KIND_COUNT;

    if (!ok) {
        kernel_health_mark(KERNEL_SUBSYSTEM_SYSCALL,
            KERNEL_HEALTH_DEGRADED, AIOS_ERR_IO);
        serial_write("[SYSCALL] observe dispatch selftest FAIL\n");
        return;
    }

    serial_printf("[SYSCALL] observe dispatch selftest PASS pipe_execs=%u slm_applies=%u slm_last_ns=%u resource_entries=%u resource_observation_only=%u\n",
        pipe_snap.total_executions,
        (uint64_t)slm_obs.apply_ok,
        slm_obs.last_latency_ns,
        (uint64_t)resource_snap.entry_count,
        (uint64_t)resource_snap.observation_only);
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

static void print_boot_ready_banner(void) {
    kernel_health_summary_t summary;
    kernel_health_get_summary(&summary);

    console_newline();
    console_write_color("=== AIOS Kernel Ready ===\n", VGA_LIGHT_GREEN, VGA_BLUE);
    console_write_color("AI-Native Operating System is operational.\n", VGA_WHITE, VGA_BLUE);

    serial_write("\n=== AIOS Kernel Ready ===\n");
    serial_write("AI-Native Operating System is operational.\n");

    if (summary.level == KERNEL_STABILITY_STABLE) {
        console_write_color("Core boot path and optional subsystems initialized.\n",
            VGA_LIGHT_CYAN, VGA_BLUE);
        serial_write("[BOOT] Core boot path and optional subsystems initialized\n");
        return;
    }

    if (summary.level == KERNEL_STABILITY_DEGRADED) {
        console_write_color("Core boot path initialized with degraded optional services.\n",
            VGA_YELLOW, VGA_BLUE);
        kprintf("[BOOT] degraded=%u failed=%u unknown=%u autonomy_safe=%u risky_io=%u\n",
            (uint64_t)summary.degraded_count,
            (uint64_t)summary.failed_count,
            (uint64_t)summary.unknown_count,
            (uint64_t)!summary.autonomy_allowed,
            (uint64_t)summary.risky_io_allowed);
        serial_printf("[BOOT] Core boot path ready with degraded services degraded=%u failed=%u unknown=%u autonomy_safe=%u risky_io=%u\n",
            (uint64_t)summary.degraded_count,
            (uint64_t)summary.failed_count,
            (uint64_t)summary.unknown_count,
            (uint64_t)!summary.autonomy_allowed,
            (uint64_t)summary.risky_io_allowed);
        return;
    }

    console_write_color("Core boot path reached ready banner in unsafe state.\n",
        VGA_LIGHT_RED, VGA_BLUE);
    serial_printf("[BOOT] WARNING ready banner reached with unsafe health failed=%u required_failures=%u\n",
        (uint64_t)summary.failed_count,
        (uint64_t)summary.required_failures);
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
