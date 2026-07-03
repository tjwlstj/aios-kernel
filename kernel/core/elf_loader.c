/*
 * AIOS Kernel - Minimal Static ELF64 Loader
 * AI-Native Operating System
 */

#include <kernel/elf.h>
#include <drivers/serial.h>
#include <lib/string.h>

static bool elf_ident_valid(const Elf64_Ehdr *ehdr) {
    return ehdr->e_ident[0] == ELFMAG0 &&
           ehdr->e_ident[1] == ELFMAG1 &&
           ehdr->e_ident[2] == ELFMAG2 &&
           ehdr->e_ident[3] == ELFMAG3 &&
           ehdr->e_ident[4] == ELFCLASS64 &&
           ehdr->e_ident[5] == ELFDATA2LSB &&
           ehdr->e_ident[6] == EV_CURRENT;
}

/* Bounds helper: [off, off+len) must fit within [0, image_size). */
static bool in_image(uint64_t off, uint64_t len, uint64_t image_size) {
    return len <= image_size && off <= image_size - len;
}

/* [vaddr, vaddr+len) must fit within the user region. */
static bool in_region(uint64_t vaddr, uint64_t len,
                      uint64_t region_base, uint64_t region_size) {
    if (vaddr < region_base) {
        return false;
    }
    uint64_t offset = vaddr - region_base;
    return len <= region_size && offset <= region_size - len;
}

aios_status_t elf_load(const void *image, uint64_t image_size,
                       uint64_t region_base, uint64_t region_size,
                       elf_load_result_t *out) {
    const uint8_t *bytes = (const uint8_t *)image;
    const Elf64_Ehdr *ehdr = (const Elf64_Ehdr *)image;
    uint64_t min_vaddr = ~0ULL;
    uint64_t max_vaddr = 0;
    uint32_t loadable = 0;
    uint32_t total_filesz = 0;
    uint32_t total_memsz = 0;

    if (!image || !out || image_size < sizeof(Elf64_Ehdr)) {
        return AIOS_ERR_INVAL;
    }
    if (!elf_ident_valid(ehdr)) {
        serial_write("[ELF] reject: bad ELF identity\n");
        return AIOS_ERR_INVAL;
    }
    if (ehdr->e_type != ET_EXEC || ehdr->e_machine != EM_X86_64) {
        serial_write("[ELF] reject: not a static x86_64 executable\n");
        return AIOS_ERR_INVAL;
    }
    if (ehdr->e_phentsize != sizeof(Elf64_Phdr) || ehdr->e_phnum == 0) {
        serial_write("[ELF] reject: bad program header table\n");
        return AIOS_ERR_INVAL;
    }
    if (!in_image(ehdr->e_phoff,
                  (uint64_t)ehdr->e_phnum * sizeof(Elf64_Phdr), image_size)) {
        serial_write("[ELF] reject: program headers out of image\n");
        return AIOS_ERR_INVAL;
    }

    for (uint16_t i = 0; i < ehdr->e_phnum; i++) {
        const Elf64_Phdr *ph =
            (const Elf64_Phdr *)(bytes + ehdr->e_phoff + (uint64_t)i * sizeof(Elf64_Phdr));

        if (ph->p_type != PT_LOAD) {
            continue;
        }
        if (ph->p_filesz > ph->p_memsz) {
            serial_write("[ELF] reject: filesz > memsz\n");
            return AIOS_ERR_INVAL;
        }
        if (!in_image(ph->p_offset, ph->p_filesz, image_size)) {
            serial_write("[ELF] reject: segment file range out of image\n");
            return AIOS_ERR_INVAL;
        }
        if (!in_region(ph->p_vaddr, ph->p_memsz, region_base, region_size)) {
            serial_write("[ELF] reject: segment vaddr out of user region\n");
            return AIOS_ERR_PERM;
        }

        /* Copy filesz bytes, then zero the bss tail (memsz - filesz).
         * Caller has mapped the region and opened the SMAP fence. */
        memcpy((void *)(uintptr_t)ph->p_vaddr,
               bytes + ph->p_offset, (size_t)ph->p_filesz);
        if (ph->p_memsz > ph->p_filesz) {
            memset((void *)(uintptr_t)(ph->p_vaddr + ph->p_filesz), 0,
                   (size_t)(ph->p_memsz - ph->p_filesz));
        }

        if (ph->p_vaddr < min_vaddr) {
            min_vaddr = ph->p_vaddr;
        }
        if (ph->p_vaddr + ph->p_memsz > max_vaddr) {
            max_vaddr = ph->p_vaddr + ph->p_memsz;
        }
        loadable++;
        total_filesz += (uint32_t)ph->p_filesz;
        total_memsz += (uint32_t)ph->p_memsz;
    }

    if (loadable == 0) {
        serial_write("[ELF] reject: no PT_LOAD segments\n");
        return AIOS_ERR_INVAL;
    }
    if (!in_region(ehdr->e_entry, 1, region_base, region_size) ||
        ehdr->e_entry < min_vaddr || ehdr->e_entry >= max_vaddr) {
        serial_write("[ELF] reject: entry point outside loaded segments\n");
        return AIOS_ERR_INVAL;
    }

    out->entry = ehdr->e_entry;
    out->load_min_vaddr = min_vaddr;
    out->load_max_vaddr = max_vaddr;
    out->loadable_segments = loadable;
    out->total_filesz = total_filesz;
    out->total_memsz = total_memsz;

    serial_printf("[ELF] loaded entry=%x segments=%u filesz=%u memsz=%u vaddr=[%x,%x)\n",
        out->entry,
        (uint64_t)loadable,
        (uint64_t)total_filesz,
        (uint64_t)total_memsz,
        min_vaddr,
        max_vaddr);
    return AIOS_OK;
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
