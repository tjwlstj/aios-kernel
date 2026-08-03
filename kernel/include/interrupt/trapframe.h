/*
 * AIOS Kernel - Trapframe Contract (M3-b-3b2c entry gate)
 *
 * Pins the full saved-register interrupt frame built by isr_common_stub
 * (kernel/interrupt/isr_stub.asm) as an explicit C/NASM offset and size
 * contract, and provides CPL0/CPL3 frame discrimination plus a bounded
 * armed-capture path used by the boot selftests. The layout is fixed by
 * the stub push order and the x86_64 interrupt frame; it is an exact
 * internal contract, not an append-only schema.
 */

#ifndef _AIOS_INTERRUPT_TRAPFRAME_H
#define _AIOS_INTERRUPT_TRAPFRAME_H

#include <interrupt/idt.h>
#include <kernel/types.h>

/* interrupt_frame_t field offsets. NASM mirrors the derived byte counts in
 * kernel/interrupt/isr_stub.asm; the runtime canary selftest catches drift
 * on the real ISR path. */
#define TRAPFRAME_R15_OFFSET        0U
#define TRAPFRAME_R14_OFFSET        8U
#define TRAPFRAME_R13_OFFSET       16U
#define TRAPFRAME_R12_OFFSET       24U
#define TRAPFRAME_R11_OFFSET       32U
#define TRAPFRAME_R10_OFFSET       40U
#define TRAPFRAME_R9_OFFSET        48U
#define TRAPFRAME_R8_OFFSET        56U
#define TRAPFRAME_RBP_OFFSET       64U
#define TRAPFRAME_RDI_OFFSET       72U
#define TRAPFRAME_RSI_OFFSET       80U
#define TRAPFRAME_RDX_OFFSET       88U
#define TRAPFRAME_RCX_OFFSET       96U
#define TRAPFRAME_RBX_OFFSET      104U
#define TRAPFRAME_RAX_OFFSET      112U
#define TRAPFRAME_INT_NO_OFFSET   120U
#define TRAPFRAME_ERR_CODE_OFFSET 128U
#define TRAPFRAME_RIP_OFFSET      136U
#define TRAPFRAME_CS_OFFSET       144U
#define TRAPFRAME_RFLAGS_OFFSET   152U
#define TRAPFRAME_RSP_OFFSET      160U
#define TRAPFRAME_SS_OFFSET       168U
#define TRAPFRAME_SIZE            176U

/* CPU-pushed tail (rip..ss) and the stub-pushed remainder. */
#define TRAPFRAME_HW_FRAME_SIZE    40U
#define TRAPFRAME_GPR_BYTES       120U
#define TRAPFRAME_VEC_ERR_BYTES    16U

AIOS_STATIC_ASSERT(TRAPFRAME_GPR_BYTES + TRAPFRAME_VEC_ERR_BYTES +
        TRAPFRAME_HW_FRAME_SIZE == TRAPFRAME_SIZE,
    "trapframe section byte counts must compose the full frame");

#define TRAPFRAME_ASSERT_OFFSET(field, expected) \
    AIOS_STATIC_ASSERT(__builtin_offsetof(interrupt_frame_t, field) == \
        (expected), "trapframe " #field " C/NASM offset drift")

TRAPFRAME_ASSERT_OFFSET(r15, TRAPFRAME_R15_OFFSET);
TRAPFRAME_ASSERT_OFFSET(r14, TRAPFRAME_R14_OFFSET);
TRAPFRAME_ASSERT_OFFSET(r13, TRAPFRAME_R13_OFFSET);
TRAPFRAME_ASSERT_OFFSET(r12, TRAPFRAME_R12_OFFSET);
TRAPFRAME_ASSERT_OFFSET(r11, TRAPFRAME_R11_OFFSET);
TRAPFRAME_ASSERT_OFFSET(r10, TRAPFRAME_R10_OFFSET);
TRAPFRAME_ASSERT_OFFSET(r9,  TRAPFRAME_R9_OFFSET);
TRAPFRAME_ASSERT_OFFSET(r8,  TRAPFRAME_R8_OFFSET);
TRAPFRAME_ASSERT_OFFSET(rbp, TRAPFRAME_RBP_OFFSET);
TRAPFRAME_ASSERT_OFFSET(rdi, TRAPFRAME_RDI_OFFSET);
TRAPFRAME_ASSERT_OFFSET(rsi, TRAPFRAME_RSI_OFFSET);
TRAPFRAME_ASSERT_OFFSET(rdx, TRAPFRAME_RDX_OFFSET);
TRAPFRAME_ASSERT_OFFSET(rcx, TRAPFRAME_RCX_OFFSET);
TRAPFRAME_ASSERT_OFFSET(rbx, TRAPFRAME_RBX_OFFSET);
TRAPFRAME_ASSERT_OFFSET(rax, TRAPFRAME_RAX_OFFSET);
TRAPFRAME_ASSERT_OFFSET(int_no, TRAPFRAME_INT_NO_OFFSET);
TRAPFRAME_ASSERT_OFFSET(err_code, TRAPFRAME_ERR_CODE_OFFSET);
TRAPFRAME_ASSERT_OFFSET(rip, TRAPFRAME_RIP_OFFSET);
TRAPFRAME_ASSERT_OFFSET(cs, TRAPFRAME_CS_OFFSET);
TRAPFRAME_ASSERT_OFFSET(rflags, TRAPFRAME_RFLAGS_OFFSET);
TRAPFRAME_ASSERT_OFFSET(rsp, TRAPFRAME_RSP_OFFSET);
TRAPFRAME_ASSERT_OFFSET(ss, TRAPFRAME_SS_OFFSET);

AIOS_STATIC_ASSERT(sizeof(interrupt_frame_t) == TRAPFRAME_SIZE,
    "trapframe C/NASM size drift");

/* Register canaries used by the CPL0 contract selftest and the ring3 demo.
 * The index is the register's interrupt_frame_t field position (r15=0 ..
 * rax=14, rsp excluded); NASM mirrors the base in isr_stub.asm and
 * user_entry.asm. */
#define TRAPFRAME_SELFTEST_CANARY_BASE 0xC0DE5AFE00000000ULL
#define TRAPFRAME_SELFTEST_CANARY(index) \
    (TRAPFRAME_SELFTEST_CANARY_BASE + (uint64_t)(index))
#define TRAPFRAME_SELFTEST_GPR_CANARIES 15U
#define TRAPFRAME_USER_CANARY_COUNT 4U

/* CPL0/CPL3 discrimination: the frame came from ring3 iff the saved CS
 * requestor privilege level is 3. Both privilege levels push the same
 * 176-byte layout in long mode; only the RSP/SS semantics differ. */
static inline bool interrupt_frame_from_user(const interrupt_frame_t *frame) {
    return frame != NULL && (frame->cs & 0x3ULL) == 0x3ULL;
}

/* Armed one-shot #BP frame capture. Single-BSP best-effort contract: arm and
 * take run outside the ISR, consume runs inside the #BP path; there is no
 * cross-CPU synchronization. While armed, the first breakpoint frame is
 * copied and every armed breakpoint resumes quietly (no exception dump). */
typedef struct {
    interrupt_frame_t frame;   /* first captured frame while armed */
    uint64_t frame_addr;       /* address the ISR frame occupied */
    uint64_t captures;         /* armed #BP consumes since arm */
    bool valid;                /* frame/frame_addr hold a capture */
} trapframe_capture_result_t;

void trapframe_capture_arm(void);
bool trapframe_capture_take(trapframe_capture_result_t *out);
/* ISR-side hook (kernel/interrupt/idt.c). Returns true when the armed
 * capture consumed the breakpoint and the handler must resume quietly. */
bool trapframe_capture_consume(interrupt_frame_t *frame);

/* Boot-time CPL0 contract proof over the real isr_common_stub path. */
aios_status_t trapframe_contract_selftest(void);
bool trapframe_contract_passed(void);

#endif /* _AIOS_INTERRUPT_TRAPFRAME_H */
