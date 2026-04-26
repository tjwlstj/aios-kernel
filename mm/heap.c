/*
 * AIOS Kernel - General-Purpose Kernel Heap Allocator
 * AI-Native Operating System
 *
 * Simple first-fit free-list allocator backed by a static 2MB BSS pool.
 * Each block carries a 32-byte header (size-t + magic + free flag + two
 * pointers), so allocations returned to callers are always 16-byte aligned.
 *
 * Not thread-safe (no spinlock yet) — safe as long as we are single-threaded
 * in the boot/init path and the shell runs with interrupts enabled but no
 * concurrent kernel threads.
 */

#include <mm/heap.h>
#include <lib/string.h>
#include <interrupt/idt.h>    /* kernel_panic */

/* -------------------------------------------------------------------------
 * Pool configuration
 * ---------------------------------------------------------------------- */

#define HEAP_POOL_SIZE   (2U * 1024U * 1024U)   /* 2 MB static pool */
#define HEAP_MAGIC_FREE  0xCAFEF00DU
#define HEAP_MAGIC_USED  0xDEADBEEFU
#define HEAP_ALIGN       16U                     /* Minimum allocation alignment */

/* -------------------------------------------------------------------------
 * Block header (32 bytes on x86-64, always 16-byte aligned)
 * ---------------------------------------------------------------------- */

typedef struct heap_block {
    size_t            size;   /* Usable bytes after this header            */
    uint32_t          magic;  /* HEAP_MAGIC_FREE or HEAP_MAGIC_USED        */
    uint32_t          free;   /* 1 = free, 0 = allocated                   */
    struct heap_block *next;  /* Next block in the free/allocated chain     */
    struct heap_block *prev;  /* Previous block in the chain               */
} heap_block_t;

/* Verify the header is exactly 32 bytes so user data stays 16-byte aligned */
AIOS_STATIC_ASSERT(sizeof(heap_block_t) == 32,
    "heap_block_t must be 32 bytes to preserve 16-byte alignment");

/* -------------------------------------------------------------------------
 * Static backing store (BSS — zero-initialised at boot)
 * ---------------------------------------------------------------------- */

static uint8_t ALIGNED(16) heap_pool[HEAP_POOL_SIZE];

/* -------------------------------------------------------------------------
 * Allocator state
 * ---------------------------------------------------------------------- */

static heap_block_t *heap_head  = NULL;
static bool          heap_ready = false;
static heap_stats_t  heap_stats_data;

/* -------------------------------------------------------------------------
 * Internal helpers
 * ---------------------------------------------------------------------- */

/* Round size up to the nearest HEAP_ALIGN multiple (min 1 block). */
static inline size_t round_up(size_t n) {
    return (n + HEAP_ALIGN - 1) & ~(size_t)(HEAP_ALIGN - 1);
}

/* Coalesce block with its free neighbour(s). */
static void coalesce(heap_block_t *b) {
    /* Merge with next if free */
    if (b->next && b->next->free) {
        heap_block_t *nx = b->next;
        b->size += sizeof(heap_block_t) + nx->size;
        b->next  = nx->next;
        if (nx->next) nx->next->prev = b;
        nx->magic = 0; /* invalidate */
        heap_stats_data.blocks--;
    }
    /* Merge with prev if free */
    if (b->prev && b->prev->free) {
        heap_block_t *pv = b->prev;
        pv->size += sizeof(heap_block_t) + b->size;
        pv->next  = b->next;
        if (b->next) b->next->prev = pv;
        b->magic = 0; /* invalidate */
        heap_stats_data.blocks--;
    }
}

/* -------------------------------------------------------------------------
 * Public API
 * ---------------------------------------------------------------------- */

aios_status_t heap_init(void) {
    heap_head = (heap_block_t *)(void *)heap_pool;
    heap_head->size  = HEAP_POOL_SIZE - sizeof(heap_block_t);
    heap_head->magic = HEAP_MAGIC_FREE;
    heap_head->free  = 1;
    heap_head->next  = NULL;
    heap_head->prev  = NULL;

    memset(&heap_stats_data, 0, sizeof(heap_stats_data));
    heap_stats_data.total  = heap_head->size;
    heap_stats_data.free   = heap_head->size;
    heap_stats_data.blocks = 1;

    heap_ready = true;
    return AIOS_OK;
}

void *kmalloc(size_t size) {
    if (!heap_ready || size == 0) {
        return NULL;
    }

    size_t aligned = round_up(size);

    /* First-fit search */
    heap_block_t *cur = heap_head;
    while (cur) {
        if (cur->magic != HEAP_MAGIC_FREE && cur->magic != HEAP_MAGIC_USED) {
            kernel_panic("kmalloc: heap corruption detected");
        }
        if (cur->free && cur->size >= aligned) {
            break;
        }
        cur = cur->next;
    }

    if (!cur) {
        return NULL; /* OOM */
    }

    /* Split block if the remainder is large enough for another allocation */
    size_t remainder = cur->size - aligned;
    if (remainder > sizeof(heap_block_t) + HEAP_ALIGN) {
        heap_block_t *split = (heap_block_t *)((uint8_t *)cur
                                + sizeof(heap_block_t) + aligned);
        split->size  = remainder - sizeof(heap_block_t);
        split->magic = HEAP_MAGIC_FREE;
        split->free  = 1;
        split->next  = cur->next;
        split->prev  = cur;

        if (cur->next) cur->next->prev = split;
        cur->next = split;
        cur->size = aligned;
        heap_stats_data.blocks++;
    }

    cur->magic = HEAP_MAGIC_USED;
    cur->free  = 0;

    heap_stats_data.used   += cur->size;
    heap_stats_data.free   -= cur->size;
    heap_stats_data.allocs++;

    return (void *)((uint8_t *)cur + sizeof(heap_block_t));
}

void kfree(void *ptr) {
    if (!ptr || !heap_ready) {
        return;
    }

    heap_block_t *block = (heap_block_t *)((uint8_t *)ptr - sizeof(heap_block_t));

    if (block->magic != HEAP_MAGIC_USED) {
        kernel_panic("kfree: double-free or heap corruption");
    }
    if (block->free) {
        kernel_panic("kfree: block already marked free");
    }

    heap_stats_data.used -= block->size;
    heap_stats_data.free += block->size;
    heap_stats_data.frees++;

    block->magic = HEAP_MAGIC_FREE;
    block->free  = 1;

    coalesce(block);
}

void heap_get_stats(heap_stats_t *out) {
    if (out) {
        *out = heap_stats_data;
    }
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
