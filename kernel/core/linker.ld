/*
 * AIOS Kernel - Linker Script
 * AI-Native Operating System
 * Target: x86_64
 */

ENTRY(_start)

SECTIONS {
    /* Kernel loaded at 1MB (standard for multiboot) */
    . = 1M;

    /* Multiboot header must be in first 32KB */
    .multiboot_header : ALIGN(8) {
        *(.multiboot_header)
    }

    /* Code section */
    .text : ALIGN(4K) {
        __text_start = .;
        *(.text)
        *(.text.*)
        __text_end = .;
    }

    /* Read-only data */
    .rodata : ALIGN(4K) {
        __rodata_start = .;
        *(.rodata)
        *(.rodata.*)
        __rodata_end = .;
    }

    /* Initialized data */
    .data : ALIGN(4K) {
        __data_start = .;
        *(.data)
        *(.data.*)
        __data_end = .;
    }

    /* Uninitialized data (BSS) */
    .bss : ALIGN(4K) {
        __bss_start = .;
        *(COMMON)
        *(.bss)
        *(.bss.*)
        __bss_end = .;
    }

    /* Kernel end marker */
    __kernel_end = .;

    /* Tensor memory pool starts after kernel */
    . = ALIGN(2M);
    __tensor_pool_start = .;

    /* Discard unnecessary sections */
    /DISCARD/ : {
        *(.comment)
        *(.note.*)
        *(.eh_frame*)
    }
}
