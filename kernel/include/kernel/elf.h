/*
 * AIOS Kernel - Minimal Static ELF64 Loader
 * AI-Native Operating System
 *
 * Parses a static ELF64 executable and maps its PT_LOAD segments into an
 * already-mapped user region (identity mapped, virtual == physical for
 * this first slice). Segment permissions are parsed but not yet enforced
 * per-page (the user region is a single W^X+U huge page; 4K per-segment
 * W^X is a later step). No dynamic linking, no interpreter.
 */

#ifndef _AIOS_KERNEL_ELF_H
#define _AIOS_KERNEL_ELF_H

#include <kernel/types.h>

/* e_ident indices and values */
#define ELF_NIDENT      16
#define ELFMAG0         0x7F
#define ELFMAG1         'E'
#define ELFMAG2         'L'
#define ELFMAG3         'F'
#define ELFCLASS64      2
#define ELFDATA2LSB     1
#define EV_CURRENT      1

#define ET_EXEC         2       /* e_type: executable */
#define EM_X86_64       62      /* e_machine */

#define PT_LOAD         1       /* p_type: loadable segment */
#define PF_X            0x1
#define PF_W            0x2
#define PF_R            0x4

typedef struct PACKED {
    uint8_t  e_ident[ELF_NIDENT];
    uint16_t e_type;
    uint16_t e_machine;
    uint32_t e_version;
    uint64_t e_entry;
    uint64_t e_phoff;
    uint64_t e_shoff;
    uint32_t e_flags;
    uint16_t e_ehsize;
    uint16_t e_phentsize;
    uint16_t e_phnum;
    uint16_t e_shentsize;
    uint16_t e_shnum;
    uint16_t e_shstrndx;
} Elf64_Ehdr;

AIOS_STATIC_ASSERT(sizeof(Elf64_Ehdr) == 64, "Elf64_Ehdr must be 64 bytes");

typedef struct PACKED {
    uint32_t p_type;
    uint32_t p_flags;
    uint64_t p_offset;
    uint64_t p_vaddr;
    uint64_t p_paddr;
    uint64_t p_filesz;
    uint64_t p_memsz;
    uint64_t p_align;
} Elf64_Phdr;

AIOS_STATIC_ASSERT(sizeof(Elf64_Phdr) == 56, "Elf64_Phdr must be 56 bytes");

typedef struct {
    uint64_t entry;          /* e_entry */
    uint64_t load_min_vaddr; /* lowest PT_LOAD p_vaddr */
    uint64_t load_max_vaddr; /* highest p_vaddr + p_memsz */
    uint32_t loadable_segments;
    uint32_t total_filesz;
    uint32_t total_memsz;
} elf_load_result_t;

/*
 * Load a static ELF64 image (already in kernel-readable memory) into the
 * user address range [region_base, region_base+region_size). All PT_LOAD
 * segments must fall inside that range. The caller must have mapped the
 * range as user memory and must supply the SMAP fence bracketing (the
 * loader writes directly to the segment virtual addresses).
 */
aios_status_t elf_load(const void *image, uint64_t image_size,
                       uint64_t region_base, uint64_t region_size,
                       elf_load_result_t *out);

#endif /* _AIOS_KERNEL_ELF_H */
