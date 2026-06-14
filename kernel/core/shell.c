/*
 * AIOS Kernel - Interactive Kernel Shell
 * AI-Native Operating System
 *
 * A minimal line-oriented REPL layered on top of the PS/2 keyboard driver
 * and the VGA text console.  Implements basic line editing (backspace,
 * Ctrl+C) and a small set of built-in commands sufficient for a developer
 * to inspect and reboot the system.
 *
 * Built-in commands
 * -----------------
 *   help     — list available commands
 *   clear    — clear the screen
 *   version  — print kernel version string
 *   info     — print CPU/timer information
 *   mem      — print kernel heap statistics
 *   uptime   — print time since boot (ticks / Hz)
 *   reboot   — reset the machine via the keyboard controller
 */

#include <kernel/shell.h>
#include <kernel/time.h>
#include <drivers/keyboard.h>
#include <drivers/vga.h>
#include <drivers/serial.h>
#include <mm/heap.h>
#include <lib/string.h>

/* -------------------------------------------------------------------------
 * Version string (keep in sync with main.c defines)
 * ---------------------------------------------------------------------- */

#define SHELL_VERSION  "0.2.0-beta.6 \"Genesis\""

/* -------------------------------------------------------------------------
 * Line-editing state
 * ---------------------------------------------------------------------- */

#define CMD_MAX  256

static char     cmd_buf[CMD_MAX];
static uint32_t cmd_len;

/* -------------------------------------------------------------------------
 * I/O helper
 * ---------------------------------------------------------------------- */

static inline void shell_outb(uint16_t port, uint8_t val) {
    __asm__ volatile ("outb %0, %1" : : "a"(val), "Nd"(port));
}

/* -------------------------------------------------------------------------
 * Prompt
 * ---------------------------------------------------------------------- */

static void shell_prompt(void) {
    console_write_color("aios", VGA_LIGHT_GREEN,  VGA_BLACK);
    console_write_color("# ",   VGA_LIGHT_GREY,   VGA_BLACK);
}

/* -------------------------------------------------------------------------
 * Built-in command handlers
 * ---------------------------------------------------------------------- */

static void cmd_help(void) {
    console_write_color(
        "Available commands:\n", VGA_LIGHT_CYAN, VGA_BLACK);
    kprintf("  help     - show this help\n");
    kprintf("  clear    - clear the screen\n");
    kprintf("  version  - show kernel version\n");
    kprintf("  info     - show CPU and timer info\n");
    kprintf("  mem      - show kernel heap statistics\n");
    kprintf("  uptime   - show uptime since boot\n");
    kprintf("  reboot   - reboot the system\n");
}

static void cmd_version(void) {
    console_write_color(
        "AIOS - AI-Native Operating System\n", VGA_LIGHT_CYAN, VGA_BLACK);
    kprintf("Version      : %s\n", SHELL_VERSION);
    kprintf("Architecture : x86_64 (Long Mode)\n");
    kprintf("Build target : bare-metal, -O2, no-stdlib\n");
}

static void cmd_info(void) {
    kprintf("Architecture : x86_64 (Long Mode)\n");
    kprintf("Page size    : %u bytes\n",  (uint64_t)PAGE_SIZE);
    kprintf("Tensor align : %u bytes\n",  (uint64_t)TENSOR_ALIGN);
    kprintf("Timer freq   : %u Hz\n",     (uint64_t)kernel_timer_irq_hz());
    kprintf("TSC freq     : %u kHz\n",    kernel_time_tsc_khz());
    kprintf("Invariant TSC: %s\n",
            kernel_time_invariant_tsc() ? "yes" : "no");
}

static void cmd_mem(void) {
    heap_stats_t s;
    heap_get_stats(&s);
    kprintf("Heap total   : %u bytes  (%u KB)\n",
            (uint64_t)s.total,  (uint64_t)(s.total  / 1024));
    kprintf("Heap used    : %u bytes  (%u KB)\n",
            (uint64_t)s.used,   (uint64_t)(s.used   / 1024));
    kprintf("Heap free    : %u bytes  (%u KB)\n",
            (uint64_t)s.free,   (uint64_t)(s.free   / 1024));
    kprintf("Free blocks  : %u\n",     (uint64_t)s.blocks);
    kprintf("Total allocs : %u\n",     (uint64_t)s.allocs);
    kprintf("Total frees  : %u\n",     (uint64_t)s.frees);
}

static void cmd_uptime(void) {
    uint64_t ticks = kernel_timer_irq_ticks();
    uint32_t hz    = kernel_timer_irq_hz();
    if (hz == 0) {
        kprintf("Timer not initialised.\n");
        return;
    }
    uint64_t secs  = ticks / (uint64_t)hz;
    uint64_t mins  = secs  / 60ULL;
    uint64_t hours = mins  / 60ULL;
    secs %= 60ULL;
    mins %= 60ULL;
    kprintf("Uptime : %u h  %u m  %u s",   hours, mins, secs);
    kprintf("  (%u ticks @ %u Hz)\n", ticks, (uint64_t)hz);
}

static void cmd_reboot(void) {
    kprintf("Rebooting...\n");
    serial_write("[SHELL] reboot requested\n");
    __asm__ volatile ("cli");
    /* Pulse the keyboard controller reset line — works in QEMU and real HW */
    shell_outb(0x64, 0xFE);
    /* Fallback: spin — should never be reached */
    while (1) { __asm__ volatile ("hlt"); }
}

/* -------------------------------------------------------------------------
 * Command dispatcher
 * ---------------------------------------------------------------------- */

static void shell_dispatch(void) {
    /* Trim leading whitespace */
    uint32_t start = 0;
    while (start < cmd_len && cmd_buf[start] == ' ') start++;

    /* Trim trailing whitespace */
    uint32_t end = cmd_len;
    while (end > start && cmd_buf[end - 1] == ' ') end--;

    uint32_t len = end - start;
    if (len == 0) return; /* blank line */

    const char *tok = cmd_buf + start;

#define MATCH(name) (len == sizeof(name)-1 && strncmp(tok, name, sizeof(name)-1) == 0)

    if (MATCH("help"))   { cmd_help();    return; }
    if (MATCH("clear"))  { console_clear(); return; }
    if (MATCH("version")){ cmd_version(); return; }
    if (MATCH("info"))   { cmd_info();    return; }
    if (MATCH("mem"))    { cmd_mem();     return; }
    if (MATCH("uptime")) { cmd_uptime();  return; }
    if (MATCH("reboot")) { cmd_reboot();  return; }

#undef MATCH

    /* Unknown command */
    console_write_color("Unknown command: ", VGA_LIGHT_RED, VGA_BLACK);
    for (uint32_t i = 0; i < len; i++) console_putchar(tok[i]);
    console_newline();
    kprintf("Type 'help' for a list of commands.\n");
}

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

aios_status_t shell_init(void) {
    cmd_len = 0;
    memset(cmd_buf, 0, sizeof(cmd_buf));
    return AIOS_OK;
}

void shell_run(void) {
    /* Switch to a clean black-background terminal */
    console_set_color(VGA_WHITE, VGA_BLACK);
    console_clear();

    /* Welcome banner */
    console_write_color(
        "+==============================================+\n",
        VGA_LIGHT_CYAN, VGA_BLACK);
    console_write_color(
        "|   AIOS Interactive Shell  -  v" SHELL_VERSION "  |\n",
        VGA_YELLOW, VGA_BLACK);
    console_write_color(
        "+==============================================+\n",
        VGA_LIGHT_CYAN, VGA_BLACK);
    kprintf("Type 'help' for available commands.\n\n");

    serial_write("[SHELL] Interactive shell started\n");

    /* Make sure interrupts are enabled so keyboard IRQs can fire */
    __asm__ volatile ("sti" ::: "memory");

    shell_prompt();

    while (1) {
        char c = keyboard_getchar();

        /* Ctrl+C — abort current line */
        if (c == '\x03') {
            console_write_color("^C", VGA_LIGHT_RED, VGA_BLACK);
            console_newline();
            cmd_len = 0;
            memset(cmd_buf, 0, sizeof(cmd_buf));
            shell_prompt();
            continue;
        }

        /* Enter — execute line */
        if (c == '\n' || c == '\r') {
            console_newline();
            cmd_buf[cmd_len] = '\0';
            shell_dispatch();
            cmd_len = 0;
            memset(cmd_buf, 0, sizeof(cmd_buf));
            shell_prompt();
            continue;
        }

        /* Backspace */
        if (c == '\b') {
            if (cmd_len > 0) {
                cmd_len--;
                cmd_buf[cmd_len] = '\0';
                /* Erase the character on screen */
                console_putchar('\b');
                console_putchar(' ');
                console_putchar('\b');
            }
            continue;
        }

        /* Printable character */
        if (cmd_len < CMD_MAX - 1) {
            cmd_buf[cmd_len++] = c;
            console_putchar(c);
        }
        /* else: line too long — silently drop */
    }
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
