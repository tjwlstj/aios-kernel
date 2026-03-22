; =============================================================================
; AIOS Kernel - Boot Entry Point (x86_64)
; AI-Native Operating System Kernel
; Multiboot2 compatible boot assembly
; =============================================================================

section .multiboot_header
align 8
header_start:
    dd 0xe85250d6                ; Multiboot2 magic number
    dd 0                         ; Architecture: i386 (protected mode)
    dd header_end - header_start ; Header length
    dd 0x100000000 - (0xe85250d6 + 0 + (header_end - header_start)) ; Checksum

    ; Framebuffer tag (request 80x25 text mode)
    dw 5                         ; Type: framebuffer
    dw 1                         ; Flags: optional
    dd 20                        ; Size
    dd 80                        ; Width
    dd 25                        ; Height
    dd 0                         ; Depth (text mode)

    ; End tag
    dw 0                         ; Type
    dw 0                         ; Flags
    dd 8                         ; Size
header_end:

; =============================================================================
; GDT (Global Descriptor Table) for 64-bit mode
; =============================================================================
section .rodata
align 16
gdt64:
    dq 0                                    ; Null descriptor
.code: equ $ - gdt64
    dq (1<<43) | (1<<44) | (1<<47) | (1<<53) ; Code segment: 64-bit, present, executable
.data: equ $ - gdt64
    dq (1<<44) | (1<<47) | (1<<41)          ; Data segment: present, writable
.pointer:
    dw $ - gdt64 - 1                        ; GDT limit
    dq gdt64                                ; GDT base address

; =============================================================================
; Page Tables for Identity Mapping (first 2MB)
; =============================================================================
section .bss
align 4096
p4_table:
    resb 4096
p3_table:
    resb 4096
p2_table:
    resb 4096

; Stack for the kernel
align 16
stack_bottom:
    resb 16384 * 4  ; 64KB stack
stack_top:

; =============================================================================
; Boot Entry Point
; =============================================================================
section .text
bits 32

global _start
extern kernel_main
extern _init_sse

_start:
    ; Save multiboot info pointer
    mov edi, ebx                ; Multiboot2 info structure pointer
    mov esi, eax                ; Multiboot2 magic number

    ; Set up stack
    mov esp, stack_top

    ; Check for multiboot2
    cmp eax, 0x36d76289
    jne .no_multiboot

    ; Check for CPUID support
    call check_cpuid
    ; Check for long mode support
    call check_long_mode

    ; Set up paging
    call setup_page_tables
    call enable_paging

    ; Load 64-bit GDT
    lgdt [gdt64.pointer]

    ; Jump to 64-bit code
    jmp gdt64.code:long_mode_start

.no_multiboot:
    ; Error: not booted by multiboot2 compliant bootloader
    mov dword [0xb8000], 0x4f524f45  ; "ER"
    mov dword [0xb8004], 0x4f3a4f52  ; "R:"
    mov dword [0xb8008], 0x4f424f4d  ; "MB"
    hlt

; =============================================================================
; CPU Feature Checks
; =============================================================================
check_cpuid:
    pushfd
    pop eax
    mov ecx, eax
    xor eax, 1 << 21           ; Flip CPUID bit
    push eax
    popfd
    pushfd
    pop eax
    push ecx
    popfd
    cmp eax, ecx
    je .no_cpuid
    ret
.no_cpuid:
    mov dword [0xb8000], 0x4f524f45
    mov dword [0xb8004], 0x4f3a4f52
    mov dword [0xb8008], 0x4f434f4e  ; "NC" - No CPUID
    hlt

check_long_mode:
    mov eax, 0x80000000
    cpuid
    cmp eax, 0x80000001
    jb .no_long_mode

    mov eax, 0x80000001
    cpuid
    test edx, 1 << 29           ; Long mode bit
    jz .no_long_mode
    ret
.no_long_mode:
    mov dword [0xb8000], 0x4f524f45
    mov dword [0xb8004], 0x4f3a4f52
    mov dword [0xb8008], 0x4f4c4f4e  ; "NL" - No Long Mode
    hlt

; =============================================================================
; Page Table Setup - Identity map first 1GB using 2MB pages
; =============================================================================
setup_page_tables:
    ; Map P4 -> P3
    mov eax, p3_table
    or eax, 0b11               ; Present + Writable
    mov [p4_table], eax

    ; Map P3 -> P2
    mov eax, p2_table
    or eax, 0b11               ; Present + Writable
    mov [p3_table], eax

    ; Map P2 entries (512 * 2MB = 1GB identity mapped)
    mov ecx, 0
.map_p2:
    mov eax, 0x200000          ; 2MB
    mul ecx
    or eax, 0b10000011         ; Present + Writable + Huge Page
    mov [p2_table + ecx * 8], eax
    inc ecx
    cmp ecx, 512
    jne .map_p2
    ret

; =============================================================================
; Enable Paging and Enter Long Mode
; =============================================================================
enable_paging:
    ; Load P4 table address into CR3
    mov eax, p4_table
    mov cr3, eax

    ; Enable PAE (Physical Address Extension)
    mov eax, cr4
    or eax, 1 << 5
    mov cr4, eax

    ; Set Long Mode Enable bit in EFER MSR
    mov ecx, 0xC0000080
    rdmsr
    or eax, 1 << 8
    wrmsr

    ; Enable paging
    mov eax, cr0
    or eax, 1 << 31
    mov cr0, eax
    ret

; =============================================================================
; 64-bit Long Mode Entry
; =============================================================================
bits 64
long_mode_start:
    ; Reload segment registers
    mov ax, gdt64.data
    mov ds, ax
    mov es, ax
    mov fs, ax
    mov gs, ax
    mov ss, ax

    ; Clear screen with AIOS branding color (blue background)
    mov rdi, 0xb8000
    mov rcx, 2000              ; 80*25 characters
    mov rax, 0x1F201F20        ; Blue bg, white fg, space
    rep stosw

    ; Display AIOS boot banner
    mov rdi, 0xb8000
    mov rsi, boot_banner
    call print_string_64

    ; Enable SSE for floating point (needed for AI computations)
    call enable_sse

    ; Call kernel main (C entry point)
    ; rdi = multiboot info pointer (already set from 32-bit mode)
    call kernel_main

    ; If kernel returns, halt
    cli
.halt:
    hlt
    jmp .halt

; =============================================================================
; Enable SSE (Streaming SIMD Extensions) - Critical for AI workloads
; =============================================================================
enable_sse:
    mov rax, cr0
    and ax, 0xFFFB             ; Clear CR0.EM (coprocessor emulation)
    or ax, 0x2                 ; Set CR0.MP (monitor coprocessor)
    mov cr0, rax

    mov rax, cr4
    or ax, 3 << 9              ; Set CR4.OSFXSR and CR4.OSXMMEXCPT
    mov cr4, rax

    ; Enable AVX if supported (for AI vector operations)
    mov eax, 1
    cpuid
    test ecx, 1 << 28          ; Check AVX support
    jz .no_avx

    ; Enable XSAVE and AVX
    mov rax, cr4
    or rax, 1 << 18            ; Set CR4.OSXSAVE
    mov cr4, rax

    xor rcx, rcx
    xgetbv
    or eax, 0x7                ; Enable SSE, AVX state saving
    xsetbv

.no_avx:
    ret

; =============================================================================
; Print String (64-bit mode) - VGA text mode
; =============================================================================
print_string_64:
    ; rdi = VGA buffer address, rsi = string pointer
    mov ah, 0x1F               ; Blue bg, white fg
.loop:
    lodsb
    test al, al
    jz .done
    stosw
    jmp .loop
.done:
    ret

; =============================================================================
; Boot Banner
; =============================================================================
section .rodata
boot_banner:
    db "  AIOS - AI-Native Operating System Kernel v0.1.0  ", 0
    db "  Booting AI Kernel... Initializing subsystems...   ", 0
