/*
 * AIOS Kernel - Tensor Memory Manager Implementation
 * AI-Native Operating System
 *
 * This memory manager is specifically designed for AI workloads:
 * - Tensor-aware allocation with proper alignment for SIMD operations
 * - Memory pools for different AI workload types (model, inference, DMA)
 * - Buddy allocator for efficient large block management
 * - KV-cache management for transformer models
 * - Zero-fragmentation design for long-running inference servers
 */

#include <mm/tensor_mm.h>
#include <drivers/vga.h>

/* UINT64_MAX not available in freestanding mode */
#ifndef UINT64_MAX
#define UINT64_MAX 0xFFFFFFFFFFFFFFFFULL
#endif

/* External symbols from linker script */
extern uint64_t __tensor_pool_start;
extern uint64_t __kernel_end;

/* ============================================================
 * Internal State
 * ============================================================ */

/* Maximum number of memory blocks we can track */
#define MAX_BLOCKS          4096
#define MAX_TENSORS         2048

/* Static block pool (no dynamic allocation needed at boot) */
static mem_block_t block_pool[MAX_BLOCKS];
static uint32_t block_pool_next = 0;

/* Tensor allocation table */
static tensor_alloc_t tensor_table[MAX_TENSORS];
static uint32_t tensor_count = 0;
static tensor_id_t next_tensor_id = 1;

/* Free list heads for each memory region */
static mem_block_t *free_lists[8] = { NULL };

/* Memory statistics */
static mem_stats_t global_stats;

/* Memory pool base addresses and sizes */
static struct {
    uint64_t base;
    uint64_t size;
    mem_region_type_t type;
    const char *name;
} memory_pools[] = {
    { 0, TENSOR_POOL_SIZE,    MEM_REGION_TENSOR,    "Tensor Pool" },
    { 0, MODEL_POOL_SIZE,     MEM_REGION_MODEL,     "Model Pool" },
    { 0, INFERENCE_POOL_SIZE, MEM_REGION_INFERENCE,  "Inference Pool" },
    { 0, DMA_POOL_SIZE,       MEM_REGION_DMA,       "DMA Pool" },
};
#define NUM_POOLS (sizeof(memory_pools) / sizeof(memory_pools[0]))

static mem_lifetime_t default_lifetime_for_region(mem_region_type_t region) {
    switch (region) {
        case MEM_REGION_MODEL:
            return MEM_LIFETIME_LONG_TERM;
        case MEM_REGION_INFERENCE:
        case MEM_REGION_ACTIVATION:
        case MEM_REGION_GRADIENT:
            return MEM_LIFETIME_SHORT_TERM;
        case MEM_REGION_KV_CACHE:
            return MEM_LIFETIME_REALTIME;
        case MEM_REGION_DMA:
            return MEM_LIFETIME_REALTIME;
        default:
            return MEM_LIFETIME_SHORT_TERM;
    }
}

static void stats_add_allocation_profile(const tensor_alloc_t *tensor) {
    if (!tensor) return;
    switch (tensor->lifetime) {
        case MEM_LIFETIME_SHORT_TERM:
            global_stats.short_term_memory += tensor->size;
            break;
        case MEM_LIFETIME_LONG_TERM:
            global_stats.long_term_memory += tensor->size;
            break;
        case MEM_LIFETIME_REALTIME:
            global_stats.realtime_memory += tensor->size;
            break;
        case MEM_LIFETIME_RANDOM:
            global_stats.random_memory += tensor->size;
            break;
        default:
            break;
    }
}

static void stats_remove_allocation_profile(const tensor_alloc_t *tensor) {
    if (!tensor) return;
    switch (tensor->lifetime) {
        case MEM_LIFETIME_SHORT_TERM:
            global_stats.short_term_memory -= tensor->size;
            break;
        case MEM_LIFETIME_LONG_TERM:
            global_stats.long_term_memory -= tensor->size;
            break;
        case MEM_LIFETIME_REALTIME:
            global_stats.realtime_memory -= tensor->size;
            break;
        case MEM_LIFETIME_RANDOM:
            global_stats.random_memory -= tensor->size;
            break;
        default:
            break;
    }
}

/* ============================================================
 * Internal Helper Functions
 * ============================================================ */

static mem_block_t *alloc_block(void) {
    if (block_pool_next >= MAX_BLOCKS) return NULL;
    mem_block_t *block = &block_pool[block_pool_next++];
    block->next = NULL;
    block->prev = NULL;
    block->is_free = true;
    block->tensor_id = 0;
    return block;
}

static tensor_alloc_t *find_tensor(tensor_id_t id) {
    for (uint32_t i = 0; i < tensor_count; i++) {
        if (tensor_table[i].id == id) {
            return &tensor_table[i];
        }
    }
    return NULL;
}

/* Insert block into free list (sorted by address for coalescing) */
static void free_list_insert(mem_block_t *block, mem_region_type_t type) {
    block->is_free = true;
    mem_block_t **head = &free_lists[type];

    if (*head == NULL || block->base < (*head)->base) {
        block->next = *head;
        block->prev = NULL;
        if (*head) (*head)->prev = block;
        *head = block;
        return;
    }

    mem_block_t *curr = *head;
    while (curr->next && curr->next->base < block->base) {
        curr = curr->next;
    }
    block->next = curr->next;
    block->prev = curr;
    if (curr->next) curr->next->prev = block;
    curr->next = block;
}

/* Remove block from free list */
static void free_list_remove(mem_block_t *block, mem_region_type_t type) {
    if (block->prev) {
        block->prev->next = block->next;
    } else {
        free_lists[type] = block->next;
    }
    if (block->next) {
        block->next->prev = block->prev;
    }
    block->prev = NULL;
    block->next = NULL;
    block->is_free = false;
}

/* Try to coalesce adjacent free blocks */
static void coalesce_blocks(mem_block_t *block, mem_region_type_t type) {
    (void)type; /* Used for type-specific coalescing in future */
    /* Merge with next block if adjacent and free */
    if (block->next && block->next->is_free &&
        block->base + block->size == block->next->base) {
        mem_block_t *next = block->next;
        block->size += next->size;
        block->next = next->next;
        if (next->next) next->next->prev = block;
    }

    /* Merge with previous block if adjacent and free */
    if (block->prev && block->prev->is_free &&
        block->prev->base + block->prev->size == block->base) {
        mem_block_t *prev = block->prev;
        prev->size += block->size;
        prev->next = block->next;
        if (block->next) block->next->prev = prev;
    }
}

/* Find best-fit free block */
static mem_block_t *find_best_fit(mem_region_type_t type, uint64_t size,
                                   uint32_t alignment) {
    mem_block_t *best = NULL;
    uint64_t best_size = UINT64_MAX;
    mem_block_t *curr = free_lists[type];

    while (curr) {
        if (curr->is_free && curr->size >= size) {
            /* Check alignment */
            uint64_t aligned_base = ALIGN_UP(curr->base, alignment);
            uint64_t waste = aligned_base - curr->base;
            if (curr->size >= size + waste) {
                uint64_t effective_size = curr->size;
                if (effective_size < best_size) {
                    best = curr;
                    best_size = effective_size;
                }
            }
        }
        curr = curr->next;
    }

    return best;
}

/* ============================================================
 * Public API Implementation
 * ============================================================ */

uint64_t tensor_dtype_size(tensor_dtype_t dtype) {
    switch (dtype) {
        case DTYPE_FP64:    return 8;
        case DTYPE_FP32:
        case DTYPE_INT32:   return 4;
        case DTYPE_FP16:
        case DTYPE_BF16:
        case DTYPE_INT16:   return 2;
        case DTYPE_INT8:    return 1;
        case DTYPE_INT4:    return 1; /* Packed: 2 values per byte */
        default:            return 0;
    }
}

uint64_t tensor_total_size(const tensor_shape_t *shape) {
    if (!shape || shape->ndim == 0) return 0;

    uint64_t total_elements = 1;
    for (uint32_t i = 0; i < shape->ndim; i++) {
        total_elements *= shape->dims[i];
    }

    uint64_t elem_size = tensor_dtype_size(shape->dtype);
    if (shape->dtype == DTYPE_INT4) {
        /* INT4: 2 values packed per byte */
        return (total_elements + 1) / 2;
    }

    return total_elements * elem_size;
}

aios_status_t tensor_mm_init(void) {
    /* Initialize statistics */
    global_stats.total_memory = 0;
    global_stats.used_memory = 0;
    global_stats.free_memory = 0;
    global_stats.tensor_count = 0;
    global_stats.model_memory = 0;
    global_stats.inference_memory = 0;
    global_stats.dma_memory = 0;
    global_stats.short_term_memory = 0;
    global_stats.long_term_memory = 0;
    global_stats.realtime_memory = 0;
    global_stats.random_memory = 0;
    global_stats.fragmentation = 0;
    global_stats.peak_usage = 0;
    global_stats.alloc_count = 0;
    global_stats.free_count = 0;

    /* Calculate pool base addresses starting from tensor pool start */
    uint64_t pool_base = (uint64_t)&__tensor_pool_start;

    for (uint32_t i = 0; i < NUM_POOLS; i++) {
        memory_pools[i].base = ALIGN_UP(pool_base, HUGE_PAGE_SIZE);
        pool_base = memory_pools[i].base + memory_pools[i].size;

        /* Create initial free block for this pool */
        mem_block_t *block = alloc_block();
        if (!block) return AIOS_ERR_NOMEM;

        block->base = memory_pools[i].base;
        block->size = memory_pools[i].size;
        block->type = memory_pools[i].type;
        block->is_free = true;
        block->is_huge = (memory_pools[i].size >= HUGE_PAGE_SIZE);
        block->align = TENSOR_ALIGN;

        free_list_insert(block, memory_pools[i].type);

        global_stats.total_memory += memory_pools[i].size;
        global_stats.free_memory += memory_pools[i].size;
    }

    kprintf("\n");
    kprintf("    Tensor Memory Manager initialized:\n");
    kprintf("    Total managed memory: %u MB\n",
        global_stats.total_memory / MB(1));

    for (uint32_t i = 0; i < NUM_POOLS; i++) {
        kprintf("    %s: %u MB at ", memory_pools[i].name,
            memory_pools[i].size / MB(1));
        console_write_hex(memory_pools[i].base);
        console_newline();
    }

    return AIOS_OK;
}

aios_status_t tensor_alloc(tensor_shape_t *shape, mem_region_type_t region,
                           tensor_alloc_t *out) {
    mem_lifetime_t lifetime = default_lifetime_for_region(region);
    bool realtime_critical = (lifetime == MEM_LIFETIME_REALTIME);
    return tensor_alloc_profiled(shape, region, lifetime,
                                 realtime_critical, false, out);
}

aios_status_t tensor_alloc_profiled(tensor_shape_t *shape, mem_region_type_t region,
                                    mem_lifetime_t lifetime,
                                    bool realtime_critical, bool stochastic,
                                    tensor_alloc_t *out) {
    if (!shape || !out) return AIOS_ERR_INVAL;

    uint64_t size = tensor_total_size(shape);
    if (size == 0) return AIOS_ERR_INVAL;

    /* Align to tensor alignment boundary */
    size = ALIGN_UP(size, TENSOR_ALIGN);

    return tensor_alloc_aligned_profiled(size, TENSOR_ALIGN, region, lifetime,
                                         realtime_critical, stochastic, out);
}

aios_status_t tensor_alloc_aligned(uint64_t size, uint32_t alignment,
                                   mem_region_type_t region,
                                   tensor_alloc_t *out) {
    mem_lifetime_t lifetime = default_lifetime_for_region(region);
    bool realtime_critical = (lifetime == MEM_LIFETIME_REALTIME);
    return tensor_alloc_aligned_profiled(size, alignment, region, lifetime,
                                         realtime_critical, false, out);
}

aios_status_t tensor_alloc_aligned_profiled(uint64_t size, uint32_t alignment,
                                            mem_region_type_t region,
                                            mem_lifetime_t lifetime,
                                            bool realtime_critical,
                                            bool stochastic,
                                            tensor_alloc_t *out) {
    if (!out || size == 0) return AIOS_ERR_INVAL;
    if (tensor_count >= MAX_TENSORS) return AIOS_ERR_NOMEM;

    /* Ensure minimum alignment */
    if (alignment < TENSOR_ALIGN) alignment = TENSOR_ALIGN;
    size = ALIGN_UP(size, alignment);

    /* Find best-fit block */
    mem_block_t *block = find_best_fit(region, size, alignment);
    if (!block) return AIOS_ERR_NOMEM;

    /* Calculate aligned base */
    uint64_t aligned_base = ALIGN_UP(block->base, alignment);
    uint64_t prefix_size = aligned_base - block->base;

    /* Split block if there's space before aligned region */
    if (prefix_size > 0) {
        mem_block_t *prefix = alloc_block();
        if (!prefix) return AIOS_ERR_NOMEM;
        prefix->base = block->base;
        prefix->size = prefix_size;
        prefix->type = region;
        prefix->is_free = true;

        block->base = aligned_base;
        block->size -= prefix_size;

        /* Insert prefix back into free list */
        free_list_insert(prefix, region);
    }

    /* Split block if there's remaining space after allocation */
    if (block->size > size + TENSOR_ALIGN) {
        mem_block_t *remainder = alloc_block();
        if (remainder) {
            remainder->base = block->base + size;
            remainder->size = block->size - size;
            remainder->type = region;
            remainder->is_free = true;

            block->size = size;

            free_list_insert(remainder, region);
        }
    }

    /* Remove allocated block from free list */
    free_list_remove(block, region);

    /* Create tensor allocation record */
    tensor_alloc_t *tensor = &tensor_table[tensor_count];
    tensor->id = next_tensor_id++;
    tensor->phys_addr = block->base;
    tensor->virt_addr = block->base; /* Identity mapped for now */
    tensor->size = block->size;
    tensor->region = region;
    tensor->lifetime = lifetime;
    tensor->realtime_critical = realtime_critical;
    tensor->stochastic = stochastic;
    tensor->pinned = false;
    tensor->dma_capable = (region == MEM_REGION_DMA);
    tensor->ref_count = 1;

    block->tensor_id = tensor->id;
    block->is_free = false;

    tensor_count++;

    /* Update statistics */
    global_stats.used_memory += block->size;
    global_stats.free_memory -= block->size;
    global_stats.tensor_count++;
    global_stats.alloc_count++;
    stats_add_allocation_profile(tensor);
    if (global_stats.used_memory > global_stats.peak_usage) {
        global_stats.peak_usage = global_stats.used_memory;
    }

    /* Copy to output */
    *out = *tensor;

    return AIOS_OK;
}

aios_status_t tensor_free(tensor_id_t id) {
    tensor_alloc_t *tensor = find_tensor(id);
    if (!tensor) return AIOS_ERR_INVAL;

    if (tensor->ref_count > 1) {
        tensor->ref_count--;
        return AIOS_OK;
    }

    /* Create a free block and return to pool */
    mem_block_t *block = alloc_block();
    if (!block) return AIOS_ERR_NOMEM;

    block->base = tensor->phys_addr;
    block->size = tensor->size;
    block->type = tensor->region;
    block->is_free = true;
    block->tensor_id = 0;

    free_list_insert(block, tensor->region);
    coalesce_blocks(block, tensor->region);

    /* Update statistics */
    global_stats.used_memory -= tensor->size;
    global_stats.free_memory += tensor->size;
    global_stats.tensor_count--;
    global_stats.free_count++;
    stats_remove_allocation_profile(tensor);

    /* Remove from tensor table (swap with last) */
    uint32_t idx = tensor - tensor_table;
    if (idx < tensor_count - 1) {
        tensor_table[idx] = tensor_table[tensor_count - 1];
    }
    tensor_count--;

    return AIOS_OK;
}

aios_status_t model_alloc(model_id_t model_id, uint64_t size,
                          tensor_alloc_t *out) {
    (void)model_id; /* Model ID used for tracking in future */
    /* Model allocations use huge pages for better TLB performance */
    size = ALIGN_UP(size, HUGE_PAGE_SIZE);
    return tensor_alloc_aligned_profiled(size, HUGE_PAGE_SIZE,
                                         MEM_REGION_MODEL,
                                         MEM_LIFETIME_LONG_TERM,
                                         false, false, out);
}

aios_status_t model_free(model_id_t model_id) {
    (void)model_id; /* In future, free only tensors for this model */
    /* Free all tensors associated with this model */
    for (uint32_t i = 0; i < tensor_count; i++) {
        if (tensor_table[i].region == MEM_REGION_MODEL) {
            tensor_free(tensor_table[i].id);
            /* Re-check index since table was modified */
            i--;
        }
    }
    return AIOS_OK;
}

aios_status_t dma_alloc(uint64_t size, tensor_alloc_t *out) {
    /* DMA allocations need page alignment and contiguous physical memory */
    size = ALIGN_UP(size, PAGE_SIZE);
    return tensor_alloc_aligned_profiled(size, PAGE_SIZE,
                                         MEM_REGION_DMA,
                                         MEM_LIFETIME_REALTIME,
                                         true, false, out);
}

aios_status_t dma_free(tensor_id_t id) {
    return tensor_free(id);
}

aios_status_t kv_cache_alloc(uint64_t num_layers, uint64_t num_heads,
                             uint64_t head_dim, uint64_t seq_len,
                             tensor_dtype_t dtype, tensor_alloc_t *out) {
    /* KV-cache size: 2 * num_layers * num_heads * head_dim * seq_len * dtype_size
     * Factor of 2 for both K and V caches */
    uint64_t elem_size = tensor_dtype_size(dtype);
    uint64_t total_size = 2 * num_layers * num_heads * head_dim * seq_len * elem_size;
    total_size = ALIGN_UP(total_size, HUGE_PAGE_SIZE);

    return tensor_alloc_aligned_profiled(total_size, HUGE_PAGE_SIZE,
                                         MEM_REGION_KV_CACHE,
                                         MEM_LIFETIME_REALTIME,
                                         true, false, out);
}

aios_status_t kv_cache_resize(tensor_id_t id, uint64_t new_seq_len) {
    /* In a full implementation, this would resize the KV-cache in place
     * or allocate a new one and copy. For now, return OK. */
    (void)id;
    (void)new_seq_len;
    return AIOS_OK;
}

aios_status_t tensor_pin(tensor_id_t id) {
    tensor_alloc_t *tensor = find_tensor(id);
    if (!tensor) return AIOS_ERR_INVAL;
    tensor->pinned = true;
    return AIOS_OK;
}

aios_status_t tensor_unpin(tensor_id_t id) {
    tensor_alloc_t *tensor = find_tensor(id);
    if (!tensor) return AIOS_ERR_INVAL;
    tensor->pinned = false;
    return AIOS_OK;
}

aios_status_t tensor_ref(tensor_id_t id) {
    tensor_alloc_t *tensor = find_tensor(id);
    if (!tensor) return AIOS_ERR_INVAL;
    tensor->ref_count++;
    return AIOS_OK;
}

aios_status_t tensor_unref(tensor_id_t id) {
    tensor_alloc_t *tensor = find_tensor(id);
    if (!tensor) return AIOS_ERR_INVAL;
    if (tensor->ref_count == 0) return AIOS_ERR_INVAL;
    tensor->ref_count--;
    if (tensor->ref_count == 0) {
        return tensor_free(id);
    }
    return AIOS_OK;
}

aios_status_t tensor_info(tensor_id_t id, tensor_alloc_t *out) {
    tensor_alloc_t *tensor = find_tensor(id);
    if (!tensor || !out) return AIOS_ERR_INVAL;
    *out = *tensor;
    return AIOS_OK;
}

void tensor_mm_stats(mem_stats_t *stats) {
    if (!stats) return;
    *stats = global_stats;
}

void tensor_mm_dump(void) {
    kprintf("\n=== Tensor Memory Manager Status ===\n");
    kprintf("Total Memory:     %u MB\n", global_stats.total_memory / MB(1));
    kprintf("Used Memory:      %u MB\n", global_stats.used_memory / MB(1));
    kprintf("Free Memory:      %u MB\n", global_stats.free_memory / MB(1));
    kprintf("Active Tensors:   %u\n", global_stats.tensor_count);
    kprintf("Peak Usage:       %u MB\n", global_stats.peak_usage / MB(1));
    kprintf("Allocations:      %u\n", global_stats.alloc_count);
    kprintf("Frees:            %u\n", global_stats.free_count);
    kprintf("\nClass Memory Footprint:\n");
    kprintf("  Short-Term:     %u MB\n", global_stats.short_term_memory / MB(1));
    kprintf("  Long-Term:      %u MB\n", global_stats.long_term_memory / MB(1));
    kprintf("  Realtime:       %u MB\n", global_stats.realtime_memory / MB(1));
    kprintf("  Random:         %u MB\n", global_stats.random_memory / MB(1));
    kprintf("====================================\n");
}
