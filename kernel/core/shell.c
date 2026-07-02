/*
 * AIOS Kernel - Interactive Kernel Shell
 * AI-Native Operating System
 *
 * A minimal line-oriented REPL that accepts input from both the PS/2
 * keyboard and the COM1 serial port (polled), so a human at the console
 * and an automated host on the other end of the serial line can drive
 * the same shell.  Implements basic line editing (backspace, Ctrl+C).
 *
 * Built-in commands (human-oriented, VGA output)
 * ----------------------------------------------
 *   help     — list available commands
 *   clear    — clear the screen
 *   version  — print kernel version string
 *   info     — print CPU/timer information
 *   mem      — print kernel heap statistics
 *   uptime   — print time since boot (ticks / Hz)
 *   reboot   — reset the machine via the keyboard controller
 *
 * Machine-oriented commands (single-line `[STATE]` responses, emitted to
 * both serial and VGA so a host-side harness can drive the kernel over
 * `-serial stdio` and assert on structured key=value output)
 * ----------------------------------------------
 *   ping             — liveness probe → `[STATE] pong ticks=<n>`
 *   state list       — enumerate available topics
 *   state health     — kernel health summary
 *   state mem        — heap statistics
 *   state pipeline   — node pipeline registry statistics
 *   state sec        — hardening status (nx/smep/umip/smap/canary)
 *   state time       — timer/TSC status
 *   state version    — kernel release
 */

#include <kernel/shell.h>
#include <kernel/time.h>
#include <kernel/health.h>
#include <kernel/cpu_sec.h>
#include <kernel/stack_guard.h>
#include <runtime/node_pipeline.h>
#include <drivers/keyboard.h>
#include <drivers/vga.h>
#include <drivers/serial.h>
#include <mm/heap.h>
#include <lib/string.h>

/* -------------------------------------------------------------------------
 * Version string (keep in sync with main.c defines)
 * ---------------------------------------------------------------------- */

#define SHELL_RELEASE  "0.2.0-beta.6"
#define SHELL_VERSION  SHELL_RELEASE " \"Genesis\""

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
    serial_write("aios# ");
}

/* -------------------------------------------------------------------------
 * Machine-oriented `[STATE]` responses
 *
 * Every response is a single line, prefixed `[STATE]`, using key=value
 * fields with no embedded spaces in values, and is emitted to both the
 * serial port (for host-side harnesses) and the VGA console.
 * ---------------------------------------------------------------------- */

#define STATE_EMIT(fmt, ...) do {                  \
    serial_printf(fmt, ##__VA_ARGS__);             \
    kprintf(fmt, ##__VA_ARGS__);                   \
} while (0)

static void cmd_ping(void) {
    STATE_EMIT("[STATE] pong ticks=%u\n", kernel_timer_irq_ticks());
}

static void state_list(void) {
    STATE_EMIT("[STATE] topics list=health,mem,pipeline,sec,time,version\n");
}

static void state_pipeline(void) {
    node_pipeline_snapshot_t s;
    node_pipeline_get_snapshot(&s);
    STATE_EMIT("[STATE] pipeline active=%u max=%u executions=%u stage_runs=%u denied=%u last_status=%d\n",
        (uint64_t)s.active_count,
        (uint64_t)s.max_pipelines,
        s.total_executions,
        s.total_stage_runs,
        (uint64_t)s.denied_count,
        (int64_t)s.last_status);
}

static void state_health(void) {
    kernel_health_summary_t s;
    kernel_health_get_summary(&s);
    STATE_EMIT("[STATE] health stability=%s ok=%u degraded=%u failed=%u unknown=%u io_degraded=%u autonomy=%u risky_io=%u\n",
        kernel_stability_name(s.level),
        (uint64_t)s.ok_count,
        (uint64_t)s.degraded_count,
        (uint64_t)s.failed_count,
        (uint64_t)s.unknown_count,
        (uint64_t)s.io_degraded,
        (uint64_t)s.autonomy_allowed,
        (uint64_t)s.risky_io_allowed);
}

static void state_mem(void) {
    heap_stats_t s;
    heap_get_stats(&s);
    STATE_EMIT("[STATE] mem heap_total=%u heap_used=%u heap_free=%u blocks=%u allocs=%u frees=%u\n",
        (uint64_t)s.total,
        (uint64_t)s.used,
        (uint64_t)s.free,
        (uint64_t)s.blocks,
        (uint64_t)s.allocs,
        (uint64_t)s.frees);
}

static void state_sec(void) {
    cpu_sec_info_t s;
    cpu_security_info(&s);
    STATE_EMIT("[STATE] sec nx=%u smep=%u umip=%u smap=%u canary=%u\n",
        (uint64_t)s.nx_enabled,
        (uint64_t)s.smep_enabled,
        (uint64_t)s.umip_enabled,
        (uint64_t)s.smap_enabled,
        (uint64_t)stack_guard_armed());
}

static void state_time(void) {
    STATE_EMIT("[STATE] time ticks=%u hz=%u tsc_khz=%u invariant=%u\n",
        kernel_timer_irq_ticks(),
        (uint64_t)kernel_timer_irq_hz(),
        kernel_time_tsc_khz(),
        (uint64_t)kernel_time_invariant_tsc());
}

static void state_version(void) {
    STATE_EMIT("[STATE] version release=%s arch=x86_64\n", SHELL_RELEASE);
}

static bool topic_is(const char *arg, uint32_t arg_len, const char *name) {
    size_t n = strlen(name);
    return (size_t)arg_len == n && strncmp(arg, name, n) == 0;
}

static void cmd_state(const char *arg, uint32_t arg_len) {
    if (arg_len == 0 || topic_is(arg, arg_len, "list")) { state_list();    return; }
    if (topic_is(arg, arg_len, "health"))               { state_health();  return; }
    if (topic_is(arg, arg_len, "mem"))                  { state_mem();     return; }
    if (topic_is(arg, arg_len, "pipeline"))             { state_pipeline(); return; }
    if (topic_is(arg, arg_len, "sec"))                  { state_sec();     return; }
    if (topic_is(arg, arg_len, "time"))                 { state_time();    return; }
    if (topic_is(arg, arg_len, "version"))              { state_version(); return; }

    STATE_EMIT("[STATE] error reason=unknown-topic\n");
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
    kprintf("  ping     - machine-readable liveness probe\n");
    kprintf("  state    - machine-readable status (state list)\n");
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

    /* Split "<cmd> <arg...>" so commands can take one argument */
    uint32_t cmd_end = 0;
    while (cmd_end < len && tok[cmd_end] != ' ') cmd_end++;
    uint32_t arg_start = cmd_end;
    while (arg_start < len && tok[arg_start] == ' ') arg_start++;
    const char *arg = tok + arg_start;
    uint32_t arg_len = len - arg_start;

#define MATCH(name) (len == sizeof(name)-1 && strncmp(tok, name, sizeof(name)-1) == 0)
#define MATCH_CMD(name) (cmd_end == sizeof(name)-1 && strncmp(tok, name, sizeof(name)-1) == 0)

    if (MATCH("help"))   { cmd_help();    return; }
    if (MATCH("clear"))  { console_clear(); return; }
    if (MATCH("version")){ cmd_version(); return; }
    if (MATCH("info"))   { cmd_info();    return; }
    if (MATCH("mem"))    { cmd_mem();     return; }
    if (MATCH("uptime")) { cmd_uptime();  return; }
    if (MATCH("reboot")) { cmd_reboot();  return; }
    if (MATCH("ping"))   { cmd_ping();    return; }
    if (MATCH_CMD("state")) { cmd_state(arg, arg_len); return; }

#undef MATCH
#undef MATCH_CMD

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
        char c = 0;
        bool from_serial = false;

        /* Poll both input sources; sleep until the next IRQ when idle.
         * Serial RX has no IRQ enabled, so the 100 Hz timer bounds the
         * polling latency to ~10ms. */
        if (keyboard_haschar()) {
            c = keyboard_getchar();
        } else if (serial_data_ready()) {
            c = serial_getchar();
            from_serial = true;
        } else {
            __asm__ volatile ("hlt");
            continue;
        }

        /* Ctrl+C — abort current line */
        if (c == '\x03') {
            console_write_color("^C", VGA_LIGHT_RED, VGA_BLACK);
            console_newline();
            if (from_serial) serial_write("^C\n");
            cmd_len = 0;
            memset(cmd_buf, 0, sizeof(cmd_buf));
            shell_prompt();
            continue;
        }

        /* Enter — execute line */
        if (c == '\n' || c == '\r') {
            console_newline();
            if (from_serial) serial_write("\n");
            cmd_buf[cmd_len] = '\0';
            shell_dispatch();
            cmd_len = 0;
            memset(cmd_buf, 0, sizeof(cmd_buf));
            shell_prompt();
            continue;
        }

        /* Backspace (0x7F covers serial DEL) */
        if (c == '\b' || c == '\x7f') {
            if (cmd_len > 0) {
                cmd_len--;
                cmd_buf[cmd_len] = '\0';
                /* Erase the character on screen */
                console_putchar('\b');
                console_putchar(' ');
                console_putchar('\b');
                if (from_serial) serial_write("\b \b");
            }
            continue;
        }

        /* Printable character */
        if (cmd_len < CMD_MAX - 1) {
            cmd_buf[cmd_len++] = c;
            console_putchar(c);
            if (from_serial) serial_putchar(c);
        }
        /* else: line too long — silently drop */
    }
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
