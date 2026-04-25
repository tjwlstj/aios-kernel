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
    add rsp, 16

    ; Return from interrupt
    iretq

section .note.GNU-stack noalloc noexec nowrite progbits
