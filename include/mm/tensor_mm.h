/*
 * AIOS Kernel - Tensor Memory Manager
 * AI-Native Operating System
 *
 * Unlike traditional page-based memory managers, the Tensor Memory Manager
 * is designed around tensor allocation patterns common in AI workloads:
 * - Large contiguous allocations for model weights
 * - Aligned allocations for SIMD/AVX operations
 * - Memory pooling for inference buffers
 * - Zero-copy DMA regions for host-device transfers
 * - Huge page support for large model parameters
 */

#ifndef _AIOS_TENSOR_MM_H
#define _AIOS_TENSOR_MM_H

#include <kernel/types.h>

/* Memory pool configuration */
#define TENSOR_POOL_SIZE        MB(256)     /* Initial tensor memory pool */
#define MODEL_POOL_SIZE         MB(512)     /* Model weight storage pool */
#define INFERENCE_POOL_SIZE     MB(128)     /* Inference buffer pool */
#define DMA_POOL_SIZE           MB(64)      /* DMA transfer buffer pool */

/* Memory region types */
typedef enum {
    MEM_REGION_KERNEL       = 0,    /* Kernel code and data */
    MEM_REGION_TENSOR       = 1,    /* General tensor allocations */
    MEM_REGION_MODEL        = 2,    /* Model weight storage */
    MEM_REGION_INFERENCE    = 3,    /* Inference computation buffers */
    MEM_REGION_DMA          = 4,    /* DMA-capable memory for device transfer */
    MEM_REGION_GRADIENT     = 5,    /* Gradient accumulation buffers */
    MEM_REGION_ACTIVATION   = 6,    /* Activation cache for backprop */
    MEM_REGION_KV_CACHE     = 7,    /* KV-cache for transformer models */
} mem_region_type_t;

/* Tensor data types (for alignment and size calculation) */
typedef enum {
    DTYPE_FP32      = 0,    /* 32-bit float */
    DTYPE_FP16      = 1,    /* 16-bit float (half precision) */
    DTYPE_BF16      = 2,    /* Brain float 16 */
    DTYPE_INT8      = 3,    /* 8-bit integer (quantized) */
    DTYPE_INT4      = 4,    /* 4-bit integer (quantized) */
    DTYPE_FP64      = 5,    /* 64-bit float (double) */
    DTYPE_INT32     = 6,    /* 32-bit integer */
    DTYPE_INT16     = 7,    /* 16-bit integer */
} tensor_dtype_t;

/* Tensor shape descriptor */
typedef struct {
    uint32_t    ndim;                       /* Number of dimensions */
    uint64_t    dims[MAX_TENSOR_DIMS];      /* Dimension sizes */
    uint64_t    strides[MAX_TENSOR_DIMS];   /* Strides for each dimension */
    tensor_dtype_t dtype;                   /* Data type */
} tensor_shape_t;

/* Memory block header (used in free list) */
typedef struct mem_block {
    uint64_t            base;       /* Base physical address */
    uint64_t            size;       /* Block size in bytes */
    mem_region_type_t   type;       /* Memory region type */
    bool                is_free;    /* Whether block is free */
    bool                is_huge;    /* Whether using huge pages */
    uint32_t            align;      /* Alignment requirement */
    tensor_id_t         tensor_id;  /* Associated tensor ID (if allocated) */
    struct mem_block    *next;      /* Next block in list */
    struct mem_block    *prev;      /* Previous block in list */
} mem_block_t;

/* Tensor allocation descriptor */
typedef struct {
    tensor_id_t     id;             /* Unique tensor ID */
    uint64_t        phys_addr;      /* Physical address */
    uint64_t        virt_addr;      /* Virtual address (if mapped) */
    uint64_t        size;           /* Total size in bytes */
    tensor_shape_t  shape;          /* Tensor shape info */
    mem_region_type_t region;       /* Memory region type */
    bool            pinned;         /* Whether memory is pinned (non-swappable) */
    bool            dma_capable;    /* Whether DMA-capable */
    uint32_t        ref_count;      /* Reference count */
} tensor_alloc_t;

/* Memory statistics */
typedef struct {
    uint64_t    total_memory;       /* Total managed memory */
    uint64_t    used_memory;        /* Currently allocated */
    uint64_t    free_memory;        /* Available memory */
    uint64_t    tensor_count;       /* Number of active tensors */
    uint64_t    model_memory;       /* Memory used by models */
    uint64_t    inference_memory;   /* Memory used by inference */
    uint64_t    dma_memory;         /* Memory in DMA regions */
    uint64_t    fragmentation;      /* Fragmentation percentage (0-100) */
    uint64_t    peak_usage;         /* Peak memory usage */
    uint64_t    alloc_count;        /* Total allocation count */
    uint64_t    free_count;         /* Total free count */
} mem_stats_t;

/* ============================================================
 * Tensor Memory Manager API
 * ============================================================ */

/* Initialization */
aios_status_t tensor_mm_init(void);

/* Tensor allocation */
aios_status_t tensor_alloc(tensor_shape_t *shape, mem_region_type_t region,
                           tensor_alloc_t *out);
aios_status_t tensor_alloc_aligned(uint64_t size, uint32_t alignment,
                                   mem_region_type_t region, tensor_alloc_t *out);
aios_status_t tensor_free(tensor_id_t id);

/* Model memory management */
aios_status_t model_alloc(model_id_t model_id, uint64_t size, tensor_alloc_t *out);
aios_status_t model_free(model_id_t model_id);

/* DMA memory operations */
aios_status_t dma_alloc(uint64_t size, tensor_alloc_t *out);
aios_status_t dma_free(tensor_id_t id);

/* KV-Cache management */
aios_status_t kv_cache_alloc(uint64_t num_layers, uint64_t num_heads,
                             uint64_t head_dim, uint64_t seq_len,
                             tensor_dtype_t dtype, tensor_alloc_t *out);
aios_status_t kv_cache_resize(tensor_id_t id, uint64_t new_seq_len);

/* Memory pinning */
aios_status_t tensor_pin(tensor_id_t id);
aios_status_t tensor_unpin(tensor_id_t id);

/* Reference counting */
aios_status_t tensor_ref(tensor_id_t id);
aios_status_t tensor_unref(tensor_id_t id);

/* Statistics */
void tensor_mm_stats(mem_stats_t *stats);
void tensor_mm_dump(void);

/* Utility */
uint64_t tensor_dtype_size(tensor_dtype_t dtype);
uint64_t tensor_total_size(const tensor_shape_t *shape);

#endif /* _AIOS_TENSOR_MM_H */
