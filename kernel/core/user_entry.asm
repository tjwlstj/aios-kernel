; =============================================================================
; AIOS Kernel - Ring3 Entry, int 0x80 Syscall Path, and Demo User Program
; AI-Native Operating System
;
; First real userspace slice: enter CPL3 with iretq, service int 0x80 from
; ring3 through the existing ai_syscall_dispatch, and return to the kernel
; on an exit syscall by restoring the saved kernel stack.
; =============================================================================

section .text
bits 64

global user_mode_run
global isr_syscall
global user_program_start
global user_program_end
global g_user_syscalls
global g_user_exit_code
global g_user_exited

extern ai_syscall_dispatch

; Selectors (must match kernel/include/kernel/user_mode.h)
%define USER_CS_RPL3 0x23
%define USER_DS_RPL3 0x1b
%define KERNEL_DS    0x10

; -----------------------------------------------------------------------------
; void user_mode_run(uint64_t entry_rip, uint64_t user_stack_top)
;   rdi = entry, rsi = user stack top. Enters ring3; returns here only when
;   the user program issues the exit syscall (isr_syscall .exit path).
; -----------------------------------------------------------------------------
user_mode_run:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    mov [rel g_kernel_resume_rsp], rsp   ; exit path restores this

    ; User data segments for ds/es (ss comes from the iretq frame).
    mov ax, USER_DS_RPL3
    mov ds, ax
    mov es, ax

    ; Build the iretq frame: ss, rsp, rflags, cs, rip.
    push USER_DS_RPL3
    push rsi                ; user stack top
    push 0x202              ; rflags: IF=1, reserved bit1=1
    push USER_CS_RPL3
    push rdi                ; entry rip
    iretq

; -----------------------------------------------------------------------------
; isr_syscall - int 0x80 gate (DPL=3). rax = syscall number, args in
;   rdi/rsi/rdx/r10/r8 (Linux-style). rax==0 means exit.
;   Runs on TSS rsp0 (dedicated syscall stack) after the ring3 transition.
; -----------------------------------------------------------------------------
isr_syscall:
    test rax, rax
    jz .exit

    inc qword [rel g_user_syscalls]

    ; Re-map ring3 arg registers to the System V order expected by
    ; ai_syscall_dispatch(num, a1, a2, a3, a4, a5). Move low-to-high so
    ; no source is overwritten before it is read.
    mov r9, r8              ; a5
    mov r8, r10            ; a4
    mov rcx, rdx           ; a3
    mov rdx, rsi           ; a2
    mov rsi, rdi           ; a1
    mov rdi, rax           ; num
    call ai_syscall_dispatch
    ; Return value is in rax; iretq leaves rax untouched so ring3 sees it.
    iretq

.exit:
    mov [rel g_user_exit_code], rdi
    mov byte [rel g_user_exited], 1
    mov rsp, [rel g_kernel_resume_rsp]   ; back to user_mode_run's frame
    mov ax, KERNEL_DS
    mov ds, ax
    mov es, ax
    pop r15
    pop r14
    pop r13
    pop r12
    pop rbp
    pop rbx
    ret

; -----------------------------------------------------------------------------
; Demo user program (position-fixed). Copied into the user page at
; 0x4000000 before entry; addresses below are absolute into that page.
;   1) SYS_PIPE_STATS(0x604) with the result buffer at 0x4001000
;   2) exit(42)
; -----------------------------------------------------------------------------
user_program_start:
    ; 1) Valid call: result buffer inside the user window.
    mov rax, 0x604          ; SYS_PIPE_STATS
    mov rdi, 0x4001000      ; user result buffer (USER_REGION + 4KB)
    int 0x80
    ; 2) Hostile call: ask the kernel to write into kernel memory. The
    ;    uaccess window must reject it (return AIOS_ERR_PERM = -6). Stash
    ;    the return value so the kernel can verify enforcement happened.
    mov rax, 0x604          ; SYS_PIPE_STATS
    mov rdi, 0x100000       ; kernel image address, outside the user window
    int 0x80
    mov [0x4001800], rax    ; record the rejection return value
    ; 3) exit(42)
    xor rax, rax
    mov rdi, 42
    int 0x80
.hang:
    jmp .hang
user_program_end:

; -----------------------------------------------------------------------------
section .bss
align 8
g_kernel_resume_rsp: resq 1
g_user_syscalls:     resq 1
g_user_exit_code:    resq 1
g_user_exited:       resb 1

section .note.GNU-stack noalloc noexec nowrite progbits
