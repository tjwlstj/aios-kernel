; =============================================================================
; AIOS Kernel - ISR Assembly Stubs (x86_64)
; AI-Native Operating System
;
; Provides low-level interrupt entry/exit points that save CPU state
; and call the C exception handler.
; =============================================================================

section .text
bits 64

extern exception_handler
extern g_cpu_smap_entry_clac_active
extern g_cpu_sec_entry_ac

; Trapframe C/NASM contract. Mirrors kernel/include/interrupt/trapframe.h;
; the runtime canary selftest catches drift on the real ISR path.
%define TRAPFRAME_SIZE          176
%define TRAPFRAME_GPR_BYTES     120
%define TRAPFRAME_VEC_ERR_BYTES 16
%define TRAPFRAME_HW_FRAME_SIZE 40
%define TRAPFRAME_INT_NO_OFFSET 120
%define TRAPFRAME_CS_OFFSET     144
%define TRAPFRAME_RFLAGS_OFFSET 152
; Register canary base shared with trapframe.h; index = interrupt_frame_t
; field position (r15=0 .. rax=14).
%define TRAPFRAME_CANARY_BASE   0xC0DE5AFE00000000

; cpu_sec_entry_ac_info_t C/NASM contract (kernel/include/kernel/cpu_sec.h).
%define CPU_SEC_ENTRY_COMMON_ENTRIES     0
%define CPU_SEC_ENTRY_COMMON_SAVED_AC    8
%define CPU_SEC_ENTRY_COMMON_CLAC       16
%define CPU_SEC_ENTRY_COMMON_FALLBACK   24
%define CPU_SEC_ENTRY_COMMON_POST_AC0   32
%define RFLAGS_AC                       (1 << 18)

; Clear live kernel AC without changing the CPU-saved user RFLAGS frame.
; CLAC is #UD without SMAP, so the boot-latched gate selects a flags-only
; fallback on unsupported/disabled CPUs. The caller supplies a saved scratch
; GPR and receives 1 for CLAC or 0 for fallback.
%macro CPU_SEC_CLEAR_ENTRY_AC 1
    cmp byte [rel g_cpu_smap_entry_clac_active], 0
    je %%fallback
    clac
    mov %1, 1
    jmp %%done
%%fallback:
    pushfq
    btr qword [rsp], 18
    popfq
    xor %1, %1
%%done:
%endmacro

; Macro for ISR without error code (CPU does not push one)
%macro ISR_NOERR 1
global isr%1
isr%1:
    push qword 0            ; Push dummy error code
    push qword %1           ; Push interrupt number
    jmp isr_common_stub
%endmacro

; Macro for ISR with error code (CPU pushes one automatically)
%macro ISR_ERR 1
global isr%1
isr%1:
    push qword %1           ; Push interrupt number (error code already on stack)
    jmp isr_common_stub
%endmacro

; =============================================================================
; Exception ISRs (0-31)
; =============================================================================
ISR_NOERR 0    ; #DE - Divide Error
ISR_NOERR 1    ; #DB - Debug Exception
ISR_NOERR 2    ; NMI - Non-Maskable Interrupt
ISR_NOERR 3    ; #BP - Breakpoint
ISR_NOERR 4    ; #OF - Overflow
ISR_NOERR 5    ; #BR - Bound Range Exceeded
ISR_NOERR 6    ; #UD - Invalid Opcode
ISR_NOERR 7    ; #NM - Device Not Available
ISR_ERR   8    ; #DF - Double Fault
ISR_NOERR 9    ; Coprocessor Segment Overrun (legacy)
ISR_ERR   10   ; #TS - Invalid TSS
ISR_ERR   11   ; #NP - Segment Not Present
ISR_ERR   12   ; #SS - Stack-Segment Fault
ISR_ERR   13   ; #GP - General Protection Fault
ISR_ERR   14   ; #PF - Page Fault
ISR_NOERR 15   ; Reserved
ISR_NOERR 16   ; #MF - x87 FPU Error
ISR_ERR   17   ; #AC - Alignment Check
ISR_NOERR 18   ; #MC - Machine Check
ISR_NOERR 19   ; #XM - SIMD Floating-Point Exception
ISR_NOERR 20   ; #VE - Virtualization Exception
ISR_ERR   21   ; #CP - Control Protection Exception
ISR_NOERR 22   ; Reserved
ISR_NOERR 23   ; Reserved
ISR_NOERR 24   ; Reserved
ISR_NOERR 25   ; Reserved
ISR_NOERR 26   ; Reserved
ISR_NOERR 27   ; Reserved
ISR_NOERR 28   ; Reserved
ISR_NOERR 29   ; Reserved
ISR_NOERR 30   ; Reserved
ISR_NOERR 31   ; Reserved

; =============================================================================
; Legacy PIC IRQ ISRs (32-47)
; =============================================================================
ISR_NOERR 32   ; IRQ0  - PIT timer
ISR_NOERR 33   ; IRQ1  - Keyboard
ISR_NOERR 34   ; IRQ2  - PIC cascade
ISR_NOERR 35   ; IRQ3  - COM2
ISR_NOERR 36   ; IRQ4  - COM1
ISR_NOERR 37   ; IRQ5  - LPT2 / sound
ISR_NOERR 38   ; IRQ6  - Floppy
ISR_NOERR 39   ; IRQ7  - LPT1 / spurious
ISR_NOERR 40   ; IRQ8  - RTC
ISR_NOERR 41   ; IRQ9  - ACPI / redirected IRQ2
ISR_NOERR 42   ; IRQ10 - PCI
ISR_NOERR 43   ; IRQ11 - PCI
ISR_NOERR 44   ; IRQ12 - PS/2 mouse
ISR_NOERR 45   ; IRQ13 - FPU
ISR_NOERR 46   ; IRQ14 - Primary ATA
ISR_NOERR 47   ; IRQ15 - Secondary ATA

; =============================================================================
; Common ISR Stub - Save state, call C handler, restore state
; =============================================================================
isr_common_stub:
    ; Save all general-purpose registers
    push rax
    push rbx
    push rcx
    push rdx
    push rsi
    push rdi
    push rbp
    push r8
    push r9
    push r10
    push r11
    push r12
    push r13
    push r14
    push r15

    ; User RFLAGS may carry DF=1 or AC=1 across a privilege-changing
    ; interrupt. Clear the live kernel flags before entering C; iretq keeps
    ; the CPU-saved user flags untouched.
    cld
    CPU_SEC_CLEAR_ENTRY_AC rax

    ; Bounded proof: count only the two ring3 #BP challenges, not timer IRQs
    ; or the earlier CPL0 trapframe selftest that share this common stub.
    cmp qword [rsp + TRAPFRAME_INT_NO_OFFSET], 3
    jne .entry_ac_evidence_done
    mov rdx, [rsp + TRAPFRAME_CS_OFFSET]
    and rdx, 3
    cmp rdx, 3
    jne .entry_ac_evidence_done

    inc qword [rel g_cpu_sec_entry_ac + CPU_SEC_ENTRY_COMMON_ENTRIES]
    test qword [rsp + TRAPFRAME_RFLAGS_OFFSET], RFLAGS_AC
    jz .entry_ac_saved_done
    inc qword [rel g_cpu_sec_entry_ac + CPU_SEC_ENTRY_COMMON_SAVED_AC]
.entry_ac_saved_done:
    test rax, rax
    jz .entry_ac_fallback
    inc qword [rel g_cpu_sec_entry_ac + CPU_SEC_ENTRY_COMMON_CLAC]
    jmp .entry_ac_path_done
.entry_ac_fallback:
    inc qword [rel g_cpu_sec_entry_ac + CPU_SEC_ENTRY_COMMON_FALLBACK]
.entry_ac_path_done:
    pushfq
    pop rdx
    test rdx, RFLAGS_AC
    jnz .entry_ac_evidence_done
    inc qword [rel g_cpu_sec_entry_ac + CPU_SEC_ENTRY_COMMON_POST_AC0]
.entry_ac_evidence_done:

    ; Pass pointer to interrupt frame as first argument
    mov rdi, rsp
    call exception_handler

    ; Restore all general-purpose registers
    pop r15
    pop r14
    pop r13
    pop r12
    pop r11
    pop r10
    pop r9
    pop r8
    pop rbp
    pop rdi
    pop rsi
    pop rdx
    pop rcx
    pop rbx
    pop rax

    ; Remove interrupt number and error code from stack
    add rsp, TRAPFRAME_VEC_ERR_BYTES

    ; Return from interrupt
    iretq

; =============================================================================
; Trapframe contract selftest trigger (CPL0)
;
; uint64_t trapframe_selftest_trigger(uint64_t *rsp_at_int3_out)
;   Loads every GPR except rsp with its contract canary, records the exact
;   RSP the int3 frame must report, and breaks through the real
;   isr_common_stub path. The armed capture in trapframe.c consumes the
;   breakpoint quietly; iretq restores the canaries, which the pops below
;   then discard from the callee-saved registers.
; =============================================================================
global trapframe_selftest_trigger
global trapframe_selftest_resume_rip
trapframe_selftest_trigger:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15

    ; The saved RSP in the frame is the pre-interrupt value at the int3.
    mov [rdi], rsp

    mov r15, TRAPFRAME_CANARY_BASE + 0
    mov r14, TRAPFRAME_CANARY_BASE + 1
    mov r13, TRAPFRAME_CANARY_BASE + 2
    mov r12, TRAPFRAME_CANARY_BASE + 3
    mov r11, TRAPFRAME_CANARY_BASE + 4
    mov r10, TRAPFRAME_CANARY_BASE + 5
    mov r9,  TRAPFRAME_CANARY_BASE + 6
    mov r8,  TRAPFRAME_CANARY_BASE + 7
    mov rbp, TRAPFRAME_CANARY_BASE + 8
    mov rdi, TRAPFRAME_CANARY_BASE + 9
    mov rsi, TRAPFRAME_CANARY_BASE + 10
    mov rdx, TRAPFRAME_CANARY_BASE + 11
    mov rcx, TRAPFRAME_CANARY_BASE + 12
    mov rbx, TRAPFRAME_CANARY_BASE + 13
    mov rax, TRAPFRAME_CANARY_BASE + 14
    int3
trapframe_selftest_resume_rip:
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret

section .note.GNU-stack noalloc noexec nowrite progbits
