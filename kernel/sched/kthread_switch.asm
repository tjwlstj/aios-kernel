; =============================================================================
; AIOS Kernel - Kernel Thread Context Switch (x86_64)
; AI-Native Operating System
;
; Cooperative context switch between kernel threads. Only callee-saved
; registers and rsp cross the switch; a switch looks like a normal function
; call, so the compiler has already spilled caller-saved state. This is the
; foundational primitive for M3-b preemption; the preemptive (IRQ-driven)
; path builds on the same saved-frame layout.
; =============================================================================

section .text
bits 64

global kthread_switch
global g_kthread_switches

; void kthread_switch(uint64_t *save_rsp, uint64_t load_rsp)
;   rdi = &prev->rsp : where to store the outgoing stack pointer
;   rsi = next->rsp  : stack pointer to resume
kthread_switch:
    inc qword [rel g_kthread_switches]
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    mov [rdi], rsp          ; save outgoing context
    mov rsp, rsi            ; load incoming context
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret                     ; resume incoming thread (fresh: jumps to entry)

section .bss
align 8
g_kthread_switches: resq 1

section .note.GNU-stack noalloc noexec nowrite progbits
