/*
 * AIOS Kernel - General-Purpose Kernel Heap Allocator
 * AI-Native Operating System
 *
 * Provides kmalloc/kfree for small, arbitrary kernel allocations.
 * Separate from the AI tensor memory manager (mm/tensor_mm.c).
 */

#ifndef _AIOS_MM_HEAP_H
#define _AIOS_MM_HEAP_H

#include <kernel/types.h>

/* Heap statistics */
typedef struct {
    size_t   total;     /* Total heap capacity in bytes */
    size_t   used;      /* Currently allocated bytes (excluding headers) */
    size_t   free;      /* Currently free bytes (excluding headers) */
    uint32_t allocs;    /* Cumulative allocation count */
    uint32_t frees;     /* Cumulative free count */
    uint32_t blocks;    /* Current number of free blocks */
} heap_stats_t;

/* Initialize the kernel heap. Must be called before kmalloc/kfree. */
aios_status_t heap_init(void);

/*
 * Allocate at least 'size' bytes of kernel memory.
 * Returns NULL if the heap is exhausted or not yet initialized.
 * Returned pointer is 16-byte aligned.
 */
void *kmalloc(size_t size);

/*
 * Free a block previously returned by kmalloc.
 * No-op if ptr is NULL. Panics on double-free or corruption.
 */
void kfree(void *ptr);

/* Fill heap statistics into *out. */
void heap_get_stats(heap_stats_t *out);

#endif /* _AIOS_MM_HEAP_H */
