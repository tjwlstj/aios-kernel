/*
 * AIOS Kernel - Trapframe Contract Runtime (M3-b-3b2c entry gate)
 * AI-Native Operating System
 *
 * Owns the armed one-shot #BP frame capture and the boot-time CPL0 proof
 * that the C interrupt_frame_t layout matches what isr_common_stub pushes.
 * The capture is a single-BSP best-effort contract like the tensor stats
 * read: arm/take run with the capture idle, consume runs in the #BP path.
 */

#include <interrupt/trapframe.h>
#include <drivers/serial.h>
#include <lib/string.h>

/* kernel/interrupt/isr_stub.asm */
extern uint64_t trapframe_selftest_trigger(uint64_t *rsp_at_int3_out);
extern uint8_t trapframe_selftest_resume_rip[];

typedef struct {
    trapframe_capture_result_t result;
    bool armed;
} trapframe_capture_state_t;

static trapframe_capture_state_t g_capture;
static bool g_contract_passed;

void trapframe_capture_arm(void) {
    memset(&g_capture, 0, sizeof(g_capture));
    g_capture.armed = true;
}

bool trapframe_capture_take(trapframe_capture_result_t *out) {
    bool valid = g_capture.result.valid;

    if (out) {
        *out = g_capture.result;
    }
    memset(&g_capture, 0, sizeof(g_capture));
    return valid;
}

bool trapframe_capture_consume(interrupt_frame_t *frame) {
    if (!g_capture.armed || !frame || frame->int_no != 3ULL) {
        return false;
    }
    g_capture.result.captures++;
    if (!g_capture.result.valid) {
        g_capture.result.frame = *frame;
        g_capture.result.frame_addr = (uint64_t)(uintptr_t)frame;
        g_capture.result.valid = true;
    }
    return true;
}

bool trapframe_contract_passed(void) {
    return g_contract_passed;
}

aios_status_t trapframe_contract_selftest(void) {
    trapframe_capture_result_t cap = {0};
    const interrupt_frame_t *f = &cap.frame;
    uint64_t rsp_at_int3 = 0;
    uint64_t expected_frame_addr;
    uint16_t live_cs = 0;
    uint16_t live_ss = 0;
    uint32_t canaries = 0;
    bool captured_once;
    bool vector_ok;
    bool cs_match;
    bool ss_match;
    bool cpl0;
    bool rip_exact;
    bool rsp_exact;
    bool frame_addr_exact;
    bool rflags_bit1;
    bool df_clear;
    bool ok;

    __asm__ volatile ("mov %%cs, %0" : "=r"(live_cs));
    __asm__ volatile ("mov %%ss, %0" : "=r"(live_ss));

    trapframe_capture_arm();
    (void)trapframe_selftest_trigger(&rsp_at_int3);
    captured_once = trapframe_capture_take(&cap) && cap.captures == 1ULL;

    canaries += f->r15 == TRAPFRAME_SELFTEST_CANARY(0);
    canaries += f->r14 == TRAPFRAME_SELFTEST_CANARY(1);
    canaries += f->r13 == TRAPFRAME_SELFTEST_CANARY(2);
    canaries += f->r12 == TRAPFRAME_SELFTEST_CANARY(3);
    canaries += f->r11 == TRAPFRAME_SELFTEST_CANARY(4);
    canaries += f->r10 == TRAPFRAME_SELFTEST_CANARY(5);
    canaries += f->r9  == TRAPFRAME_SELFTEST_CANARY(6);
    canaries += f->r8  == TRAPFRAME_SELFTEST_CANARY(7);
    canaries += f->rbp == TRAPFRAME_SELFTEST_CANARY(8);
    canaries += f->rdi == TRAPFRAME_SELFTEST_CANARY(9);
    canaries += f->rsi == TRAPFRAME_SELFTEST_CANARY(10);
    canaries += f->rdx == TRAPFRAME_SELFTEST_CANARY(11);
    canaries += f->rcx == TRAPFRAME_SELFTEST_CANARY(12);
    canaries += f->rbx == TRAPFRAME_SELFTEST_CANARY(13);
    canaries += f->rax == TRAPFRAME_SELFTEST_CANARY(14);

    vector_ok = f->int_no == 3ULL && f->err_code == 0ULL;
    cs_match = f->cs == (uint64_t)live_cs;
    ss_match = f->ss == (uint64_t)live_ss;
    cpl0 = !interrupt_frame_from_user(f);
    rip_exact = f->rip == (uint64_t)(uintptr_t)trapframe_selftest_resume_rip;
    rsp_exact = f->rsp == rsp_at_int3;
    /* Long mode aligns RSP down to 16 bytes before pushing the frame; the
     * saved RSP value stays pre-alignment. */
    expected_frame_addr = (rsp_at_int3 & ~0xFULL) - (uint64_t)TRAPFRAME_SIZE;
    frame_addr_exact = cap.frame_addr == expected_frame_addr;
    rflags_bit1 = (f->rflags & 0x2ULL) != 0ULL;
    df_clear = (f->rflags & 0x400ULL) == 0ULL;

    ok = captured_once &&
         canaries == TRAPFRAME_SELFTEST_GPR_CANARIES &&
         vector_ok && cs_match && ss_match && cpl0 &&
         rip_exact && rsp_exact && frame_addr_exact &&
         rflags_bit1 && df_clear;
    g_contract_passed = ok;

    serial_printf("[TRAP] frame contract selftest %s size=%u canaries=%u int_no=%u err=%u cpl0=%u cs_match=%u ss_match=%u rip_exact=%u rsp_exact=%u frame_addr_exact=%u rflags_bit1=%u df_clear=%u\n",
        ok ? "PASS" : "FAIL",
        (uint64_t)TRAPFRAME_SIZE,
        (uint64_t)canaries,
        f->int_no,
        f->err_code,
        (uint64_t)cpl0,
        (uint64_t)cs_match,
        (uint64_t)ss_match,
        (uint64_t)rip_exact,
        (uint64_t)rsp_exact,
        (uint64_t)frame_addr_exact,
        (uint64_t)rflags_bit1,
        (uint64_t)df_clear);

    return ok ? AIOS_OK : AIOS_ERR_IO;
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
