/*
 * AIOS Kernel - Cooperative Kernel Threads
 * AI-Native Operating System
 */

#include <sched/kthread.h>
#include <drivers/serial.h>

/* Switch counter lives in kthread_switch.asm. */
extern volatile uint64_t g_kthread_switches;

uint64_t kthread_switch_count(void) {
    return g_kthread_switches;
}

void kthread_init(kthread_t *t, uint32_t id, const char *name,
                  void (*entry)(void), void *stack_top) {
    /* 16-align the stack top, then build the frame kthread_switch expects:
     *   [pad][entry][rbx][rbp][r12][r13][r14][r15]  (r15 lowest = rsp)
     * The pad makes rsp %16 == 8 at `entry`, matching the SysV ABI. */
    uint64_t *sp = (uint64_t *)((uintptr_t)stack_top & ~(uintptr_t)0xF);

    *(--sp) = 0;                    /* alignment pad */
    *(--sp) = (uint64_t)entry;      /* ret target of the first switch */
    *(--sp) = 0;                    /* rbx */
    *(--sp) = 0;                    /* rbp */
    *(--sp) = 0;                    /* r12 */
    *(--sp) = 0;                    /* r13 */
    *(--sp) = 0;                    /* r14 */
    *(--sp) = 0;                    /* r15 (rsp points here) */

    t->rsp = (uint64_t)sp;
    t->id = id;
    t->name = name;
}

/* -------------------------------------------------------------------------
 * Cooperative ping-pong selftest
 *
 * Two threads hand control back and forth a fixed number of rounds. The
 * proof of a correct switch is that each thread's loop counter — which
 * lives on its own stack — survives every switch: if the stacks were not
 * saved/restored correctly, the loops would not run the exact expected
 * number of times in strict alternation.
 * ---------------------------------------------------------------------- */

#define KT_STACK_SIZE     16384U
#define KT_PINGPONG_ROUNDS 3U
#define KT_SEQ_CAP        8U

static kthread_t g_main_ctx;
static kthread_t g_ping;
static kthread_t g_pong;
static uint8_t   g_ping_stack[KT_STACK_SIZE] ALIGNED(16);
static uint8_t   g_pong_stack[KT_STACK_SIZE] ALIGNED(16);

static volatile uint8_t  g_seq[KT_SEQ_CAP];
static volatile uint32_t g_seq_len;
static volatile uint32_t g_ping_count;
static volatile uint32_t g_pong_count;

static void seq_record(uint8_t id) {
    if (g_seq_len < KT_SEQ_CAP) {
        g_seq[g_seq_len++] = id;
    }
}

static void ping_entry(void) {
    for (uint32_t i = 0; i < KT_PINGPONG_ROUNDS; i++) {
        seq_record(1);
        g_ping_count++;
        kthread_switch(&g_ping.rsp, g_pong.rsp);
    }
    /* Loop finished on this thread's own stack — hand back to the bootstrap
     * context, which resumes inside kthread_selftest. */
    kthread_switch(&g_ping.rsp, g_main_ctx.rsp);
}

static void pong_entry(void) {
    for (;;) {
        seq_record(2);
        g_pong_count++;
        kthread_switch(&g_pong.rsp, g_ping.rsp);
    }
}

aios_status_t kthread_selftest(void) {
    uint64_t before;
    uint64_t delta;
    uint64_t flags;

    g_seq_len = 0;
    g_ping_count = 0;
    g_pong_count = 0;

    kthread_init(&g_ping, 1, "kt-ping", ping_entry,
                 g_ping_stack + KT_STACK_SIZE);
    kthread_init(&g_pong, 2, "kt-pong", pong_entry,
                 g_pong_stack + KT_STACK_SIZE);

    /* Run the whole exchange with interrupts masked for determinism, then
     * restore the caller's IF exactly. A bare `sti` here would be fatal: the
     * legacy PIC is not remapped until a later boot step, so IRQ0 would
     * arrive as vector 8 (#DF). Save/restore instead. */
    __asm__ volatile ("pushfq; pop %0; cli" : "=r"(flags) :: "memory");
    before = g_kthread_switches;
    kthread_switch(&g_main_ctx.rsp, g_ping.rsp);   /* returns when ping is done */
    delta = g_kthread_switches - before;
    if (flags & (1ULL << 9)) {
        __asm__ volatile ("sti" ::: "memory");
    }

    /* Expected: main->ping, then 3x (ping->pong, pong->ping), then
     * ping->main = 1 + 6 + 1 = 8 switches; strict 1,2,1,2,1,2 sequence. */
    if (g_seq_len != KT_PINGPONG_ROUNDS * 2 ||
        g_ping_count != KT_PINGPONG_ROUNDS ||
        g_pong_count != KT_PINGPONG_ROUNDS ||
        delta != (uint64_t)(KT_PINGPONG_ROUNDS * 2 + 2)) {
        serial_write("[SCHED] context switch selftest FAIL (counts)\n");
        return AIOS_ERR_IO;
    }
    for (uint32_t i = 0; i < g_seq_len; i++) {
        uint8_t expect = (i % 2 == 0) ? 1 : 2;
        if (g_seq[i] != expect) {
            serial_write("[SCHED] context switch selftest FAIL (order)\n");
            return AIOS_ERR_IO;
        }
    }

    serial_printf("[SCHED] context switch selftest PASS switches=%u seq_len=%u ping=%u pong=%u\n",
        delta, (uint64_t)g_seq_len, (uint64_t)g_ping_count,
        (uint64_t)g_pong_count);
    return AIOS_OK;
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
