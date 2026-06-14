/*
 * AIOS Kernel - Accelerator Hardware Abstraction Layer (HAL)
 * AI-Native Operating System
 *
 * Provides a unified interface for AI accelerator devices:
 * - GPU (NVIDIA, AMD, Intel)
 * - TPU (Google Tensor Processing Units)
 * - NPU (Neural Processing Units)
 * - Custom AI ASICs
 *
 * The HAL abstracts away hardware-specific details and provides
 * a common API for compute dispatch, memory transfer, and device
 * management across all accelerator types.
 */

#ifndef _AIOS_ACCEL_HAL_H
#define _AIOS_ACCEL_HAL_H

#include <kernel/types.h>
#include <mm/tensor_mm.h>

/* Accelerator device types */
typedef enum {
    ACCEL_TYPE_NONE     = 0,
    ACCEL_TYPE_GPU      = 1,    /* General Purpose GPU */
    ACCEL_TYPE_TPU      = 2,    /* Tensor Processing Unit */
    ACCEL_TYPE_NPU      = 3,    /* Neural Processing Unit */
    ACCEL_TYPE_FPGA     = 4,    /* FPGA-based accelerator */
    ACCEL_TYPE_ASIC     = 5,    /* Custom AI ASIC */
    ACCEL_TYPE_CPU_SIMD = 6,    /* CPU with SIMD (fallback) */
} accel_type_t;

/* Accelerator vendor IDs */
typedef enum {
    ACCEL_VENDOR_UNKNOWN    = 0,
    ACCEL_VENDOR_NVIDIA     = 1,
    ACCEL_VENDOR_AMD        = 2,
    ACCEL_VENDOR_INTEL      = 3,
    ACCEL_VENDOR_GOOGLE     = 4,
    ACCEL_VENDOR_APPLE      = 5,
    ACCEL_VENDOR_QUALCOMM   = 6,
    ACCEL_VENDOR_CUSTOM     = 7,
} accel_vendor_t;

/* Compute capability / features */
typedef struct {
    bool    fp32;           /* 32-bit float support */
    bool    fp16;           /* 16-bit float support */
    bool    bf16;           /* BFloat16 support */
    bool    int8;           /* INT8 quantized support */
    bool    int4;           /* INT4 quantized support */
    bool    tf32;           /* TensorFloat-32 support */
    bool    tensor_cores;   /* Hardware tensor cores */
    bool    sparse;         /* Structured sparsity support */
    bool    flash_attn;     /* Flash attention hardware support */
    uint32_t max_threads;   /* Maximum concurrent threads */
    uint32_t warp_size;     /* Warp/wavefront size */
    uint32_t sm_count;      /* Streaming multiprocessor count */
} accel_capabilities_t;

/* Device memory info */
typedef struct {
    uint64_t    total_memory;       /* Total device memory */
    uint64_t    free_memory;        /* Available device memory */
    uint64_t    used_memory;        /* Used device memory */
    uint64_t    bandwidth;          /* Memory bandwidth (bytes/sec) */
    uint32_t    bus_width;          /* Memory bus width (bits) */
} accel_memory_info_t;

/* Accelerator device descriptor */
typedef struct {
    accel_id_t          id;             /* Device ID */
    accel_type_t        type;           /* Device type */
    accel_vendor_t      vendor;         /* Vendor */
    char                name[64];       /* Device name string */
    accel_capabilities_t caps;          /* Compute capabilities */
    accel_memory_info_t mem;            /* Memory information */
    uint32_t            pci_bus;        /* PCI bus number */
    uint32_t            pci_device;     /* PCI device number */
    uint32_t            pci_function;   /* PCI function number */
    uint64_t            mmio_base;      /* MMIO base address */
    uint64_t            mmio_size;      /* MMIO region size */
    uint32_t            clock_mhz;      /* Core clock (MHz) */
    uint32_t            mem_clock_mhz;  /* Memory clock (MHz) */
    uint32_t            power_watts;    /* TDP (watts) */
    uint32_t            temperature;    /* Current temperature (Celsius) */
    bool                active;         /* Whether device is active */
    uint32_t            utilization;    /* Current utilization (0-100%) */
} accel_device_t;

/* Compute kernel descriptor */
typedef struct {
    const char      *name;          /* Kernel name */
    uint64_t        code_addr;      /* Kernel code address */
    uint64_t        code_size;      /* Kernel code size */
    uint32_t        grid_x;         /* Grid dimension X */
    uint32_t        grid_y;         /* Grid dimension Y */
    uint32_t        grid_z;         /* Grid dimension Z */
    uint32_t        block_x;        /* Block dimension X */
    uint32_t        block_y;        /* Block dimension Y */
    uint32_t        block_z;        /* Block dimension Z */
    uint32_t        shared_mem;     /* Shared memory per block */
    uint32_t        num_args;       /* Number of kernel arguments */
    uint64_t        *args;          /* Kernel argument pointers */
} compute_kernel_t;

/* DMA transfer descriptor */
typedef struct {
    uint64_t    src_addr;       /* Source address */
    uint64_t    dst_addr;       /* Destination address */
    uint64_t    size;           /* Transfer size */
    bool        host_to_device; /* Direction: true=H2D, false=D2H */
    bool        async;          /* Asynchronous transfer */
} dma_transfer_t;

/* Device operations (virtual function table for each device type) */
typedef struct {
    aios_status_t (*init)(accel_device_t *dev);
    aios_status_t (*shutdown)(accel_device_t *dev);
    aios_status_t (*reset)(accel_device_t *dev);
    aios_status_t (*alloc_mem)(accel_device_t *dev, uint64_t size, uint64_t *addr);
    aios_status_t (*free_mem)(accel_device_t *dev, uint64_t addr);
    aios_status_t (*transfer)(accel_device_t *dev, dma_transfer_t *xfer);
    aios_status_t (*launch_kernel)(accel_device_t *dev, compute_kernel_t *kernel);
    aios_status_t (*sync)(accel_device_t *dev);
    aios_status_t (*get_info)(accel_device_t *dev, accel_memory_info_t *info);
} accel_ops_t;

/* ============================================================
 * Accelerator HAL API
 * ============================================================ */

/* Initialization and discovery */
aios_status_t accel_hal_init(void);
uint32_t accel_get_count(void);
aios_status_t accel_get_device(accel_id_t id, accel_device_t **dev);
aios_status_t accel_enumerate(void);

/* Device management */
aios_status_t accel_activate(accel_id_t id);
aios_status_t accel_deactivate(accel_id_t id);
aios_status_t accel_reset(accel_id_t id);

/* Memory operations */
aios_status_t accel_mem_alloc(accel_id_t id, uint64_t size, uint64_t *addr);
aios_status_t accel_mem_free(accel_id_t id, uint64_t addr);
aios_status_t accel_mem_copy_h2d(accel_id_t id, uint64_t host_addr,
                                  uint64_t dev_addr, uint64_t size);
aios_status_t accel_mem_copy_d2h(accel_id_t id, uint64_t dev_addr,
                                  uint64_t host_addr, uint64_t size);
aios_status_t accel_mem_copy_d2d(accel_id_t src_id, uint64_t src_addr,
                                  accel_id_t dst_id, uint64_t dst_addr,
                                  uint64_t size);

/* Compute dispatch */
aios_status_t accel_dispatch(accel_id_t id, compute_kernel_t *kernel);
aios_status_t accel_sync(accel_id_t id);

/* AI-specific operations */
aios_status_t accel_matmul(accel_id_t id, uint64_t a_addr, uint64_t b_addr,
                           uint64_t c_addr, uint32_t m, uint32_t n, uint32_t k,
                           tensor_dtype_t dtype);
aios_status_t accel_attention(accel_id_t id, uint64_t q_addr, uint64_t k_addr,
                              uint64_t v_addr, uint64_t out_addr,
                              uint32_t batch, uint32_t heads,
                              uint32_t seq_len, uint32_t head_dim);

/* Monitoring */
void accel_hal_dump(void);
uint32_t accel_get_utilization(accel_id_t id);
uint32_t accel_get_temperature(accel_id_t id);

#endif /* _AIOS_ACCEL_HAL_H */
