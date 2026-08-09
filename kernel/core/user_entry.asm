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
global user_elf_image_start
global user_elf_image_end
extern ai_syscall_dispatch
extern g_cpu_smap_entry_clac_active
extern g_cpu_sec_entry_ac

; Selectors (must match kernel/include/kernel/user_mode.h)
%define USER_CS_RPL3 0x23
%define USER_DS_RPL3 0x1b
%define KERNEL_DS    0x10

; bootstrap_user_run_state_t offsets. These are an append-only C/NASM ABI;
; keep synchronized with kernel/include/kernel/process.h.
%define RUN_STATE_RESUME_RSP      0
%define RUN_STATE_SYSCALLS        8
%define RUN_STATE_INT80_ENTRIES  16
%define RUN_STATE_EXIT_CODE      24
%define RUN_STATE_ENTRY_RSP_MIN  32
%define RUN_STATE_ENTRY_RSP_MAX  40
%define RUN_STATE_EXITED         48

; Trapframe register canary base (kernel/include/interrupt/trapframe.h);
; index = interrupt_frame_t field position (r15=0 .. rax=14).
%define TRAPFRAME_CANARY_BASE   0xC0DE5AFE00000000

; cpu_sec_entry_ac_info_t offsets (kernel/include/kernel/cpu_sec.h).
%define CPU_SEC_ENTRY_INT80_ENTRIES     40
%define CPU_SEC_ENTRY_INT80_SAVED_AC    48
%define CPU_SEC_ENTRY_INT80_CLAC        56
%define CPU_SEC_ENTRY_INT80_FALLBACK    64
%define CPU_SEC_ENTRY_INT80_POST_AC0    72
%define SYSCALL_FRAME_RFLAGS_OFFSET     16
%define RFLAGS_AC                       (1 << 18)

; r11 is already scratch in this bootstrap int80 ABI. Return 1 when CLAC was
; selected, 0 when the #UD-safe flags fallback was selected.
%macro CPU_SEC_CLEAR_SYSCALL_AC 0
    cmp byte [rel g_cpu_smap_entry_clac_active], 0
    je %%fallback
    clac
    mov r11, 1
    jmp %%done
%%fallback:
    pushfq
    btr qword [rsp], 18
    popfq
    xor r11, r11
%%done:
%endmacro

; -----------------------------------------------------------------------------
; void user_mode_run(uint64_t entry_rip, uint64_t user_stack_top,
;                    bootstrap_user_run_state_t *state)
;   rdi = entry, rsi = user stack top, rdx = process-owned run state.
;   Enters ring3; returns here only when the user program issues exit.
; -----------------------------------------------------------------------------
user_mode_run:
    push rbx
    push rbp
    push r12
    push r13
    push r14
    push r15
    mov [rel g_active_user_run_state], rdx
    mov [rdx + RUN_STATE_RESUME_RSP], rsp

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
    ; Sanitize the live kernel ABI before touching run state or calling C.
    ; The CPU frame at rsp (including the user's AC bit) is not modified.
    cld
    CPU_SEC_CLEAR_SYSCALL_AC

    inc qword [rel g_cpu_sec_entry_ac + CPU_SEC_ENTRY_INT80_ENTRIES]
    test qword [rsp + SYSCALL_FRAME_RFLAGS_OFFSET], RFLAGS_AC
    jz .entry_ac_saved_done
    inc qword [rel g_cpu_sec_entry_ac + CPU_SEC_ENTRY_INT80_SAVED_AC]
.entry_ac_saved_done:
    test r11, r11
    jz .entry_ac_fallback
    inc qword [rel g_cpu_sec_entry_ac + CPU_SEC_ENTRY_INT80_CLAC]
    jmp .entry_ac_path_done
.entry_ac_fallback:
    inc qword [rel g_cpu_sec_entry_ac + CPU_SEC_ENTRY_INT80_FALLBACK]
.entry_ac_path_done:
    pushfq
    pop r11
    test r11, RFLAGS_AC
    jnz .entry_ac_post_done
    inc qword [rel g_cpu_sec_entry_ac + CPU_SEC_ENTRY_INT80_POST_AC0]
.entry_ac_post_done:
    mov r11, [rel g_active_user_run_state]
    test r11, r11
    jz .fatal_state

    ; Capture the untouched CPU entry frame location. A CPL3->0 int gate
    ; loads TSS.rsp0 and pushes SS/RSP/RFLAGS/CS/RIP (40 bytes) before here.
    inc qword [r11 + RUN_STATE_INT80_ENTRIES]
    cmp rsp, [r11 + RUN_STATE_ENTRY_RSP_MIN]
    jae .entry_min_done
    mov [r11 + RUN_STATE_ENTRY_RSP_MIN], rsp
.entry_min_done:
    cmp rsp, [r11 + RUN_STATE_ENTRY_RSP_MAX]
    jbe .entry_max_done
    mov [r11 + RUN_STATE_ENTRY_RSP_MAX], rsp
.entry_max_done:
    test rax, rax
    jz .exit

    inc qword [r11 + RUN_STATE_SYSCALLS]

    ; Re-map ring3 arg registers to the System V order expected by
    ; ai_syscall_dispatch(num, a1, a2, a3, a4, a5). Move low-to-high so
    ; no source is overwritten before it is read.
    mov r9, r8              ; a5
    mov r8, r10            ; a4
    mov rcx, rdx           ; a3
    mov rdx, rsi           ; a2
    mov rsi, rdi           ; a1
    mov rdi, rax           ; num
    sub rsp, 8             ; align the SysV call site (CPU frame is 40 bytes)
    call ai_syscall_dispatch
    add rsp, 8
    ; Return value is in rax; iretq leaves rax untouched so ring3 sees it.
    iretq

.exit:
    mov [r11 + RUN_STATE_EXIT_CODE], rdi
    mov byte [r11 + RUN_STATE_EXITED], 1
    mov rsp, [r11 + RUN_STATE_RESUME_RSP]
    mov qword [rel g_active_user_run_state], 0
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

.fatal_state:
    cli
.fatal_state_halt:
    hlt
    jmp .fatal_state_halt

; -----------------------------------------------------------------------------
; Demo user program delivered as a real static ELF64 image, parsed by the
; kernel elf_loader. The code segment loads at USER_CODE_VADDR; p_memsz is
; larger than p_filesz so the loader also exercises .bss zeroing.
;   1) SYS_PIPE_STATS(0x604) with the result buffer at 0x4001000
;   2) int3 with register canaries loaded — the armed kernel capture proves
;      the CPL3 trapframe layout, from_user discrimination, and that the
;      frame landed on this process's ring0 entry stack
;   3) hostile SYS_PIPE_STATS with a kernel-range pointer (must be denied)
;   4) exit(42)
; -----------------------------------------------------------------------------
%define USER_CODE_VADDR 0x4000000

section .rodata
align 8
user_elf_image_start:
elf_ehdr:
    db 0x7f, 'E', 'L', 'F'      ; e_ident magic
    db 2, 1, 1, 0               ; ELFCLASS64, ELFDATA2LSB, EV_CURRENT, ABI
    times 8 db 0                ; e_ident padding
    dw 2                        ; e_type = ET_EXEC
    dw 62                       ; e_machine = EM_X86_64
    dd 1                        ; e_version
    dq USER_CODE_VADDR + (elf_code - user_elf_image_start) ; e_entry
    dq elf_phdr - user_elf_image_start                     ; e_phoff
    dq 0                        ; e_shoff
    dd 0                        ; e_flags
    dw 64                       ; e_ehsize
    dw 56                       ; e_phentsize
    dw 1                        ; e_phnum
    dw 0                        ; e_shentsize
    dw 0                        ; e_shnum
    dw 0                        ; e_shstrndx
elf_phdr:
    dd 1                        ; p_type = PT_LOAD
    dd 5                        ; p_flags = R+X
    dq elf_code - user_elf_image_start                     ; p_offset
    dq USER_CODE_VADDR + (elf_code - user_elf_image_start) ; p_vaddr
    dq USER_CODE_VADDR + (elf_code - user_elf_image_start) ; p_paddr
    dq elf_code_end - elf_code                             ; p_filesz
    dq (elf_code_end - elf_code) + 64                      ; p_memsz (+bss)
    dq 0x1000                   ; p_align
elf_code:
    mov rax, 0x604          ; SYS_PIPE_STATS
    mov rdi, 0x4001000      ; user result buffer (USER_REGION + 4KB)
    int 0x80
    ; Challenge both entry stubs with user AC=1. Each iretq restores this
    ; saved user flag; the exit int80 has no iretq, so its entry sanitizer
    ; also proves the kernel resumes with live AC=0.
    pushfq
    bts qword [rsp], 18
    popfq
    mov rbx, TRAPFRAME_CANARY_BASE + 13
    mov rcx, TRAPFRAME_CANARY_BASE + 12
    mov r8,  TRAPFRAME_CANARY_BASE + 7
    mov r12, TRAPFRAME_CANARY_BASE + 3
    int3                    ; CPL3 trapframe capture evidence
    mov rax, 0x604          ; hostile: kernel-range pointer
    mov rdi, 0x100000
    int 0x80
    mov [0x4001800], rax    ; record the rejection return value
    xor rax, rax            ; exit
    mov rdi, 42             ; exit code
    int 0x80
.hang:
    jmp .hang
elf_code_end:
user_elf_image_end:

; -----------------------------------------------------------------------------
section .bss
align 8
g_active_user_run_state: resq 1

section .note.GNU-stack noalloc noexec nowrite progbits
