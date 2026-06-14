/*
 * AIOS Kernel - Core Type Definitions
 * AI-Native Operating System
 */

#ifndef _AIOS_TYPES_H
#define _AIOS_TYPES_H

/* Fixed-width integer types */
typedef unsigned char       uint8_t;
typedef unsigned short      uint16_t;
typedef unsigned int        uint32_t;
typedef unsigned long long  uint64_t;

typedef signed char         int8_t;
typedef signed short        int16_t;
typedef signed int          int32_t;
typedef signed long long    int64_t;

/* Size types */
typedef uint64_t            size_t;
typedef int64_t             ssize_t;
typedef uint64_t            uintptr_t;
typedef int64_t             intptr_t;

/* Boolean type */
typedef uint8_t             bool;
#define true                1
#define false               0

/* NULL pointer */
#define NULL                ((void*)0)

/* AI-specific types */
typedef uint32_t            tensor_id_t;     /* Tensor identifier */
typedef uint32_t            accel_id_t;      /* Accelerator device ID */
typedef uint32_t            model_id_t;      /* AI model identifier */
typedef uint32_t            task_id_t;       /* AI task identifier */
typedef uint32_t            pid_t;           /* Process ID */
typedef int32_t             priority_t;      /* Scheduling priority */

/* Memory alignment macros */
#define ALIGN_UP(x, align)    (((x) + ((align) - 1)) & ~((align) - 1))
#define ALIGN_DOWN(x, align)  ((x) & ~((align) - 1))

/* Page size constants */
#define PAGE_SIZE           4096
#define PAGE_SHIFT          12
#define HUGE_PAGE_SIZE      (2 * 1024 * 1024)   /* 2MB huge pages */
#define HUGE_PAGE_SHIFT     21

/* Tensor memory alignment (for SIMD/AVX operations) */
#define TENSOR_ALIGN        64   /* 64-byte alignment for AVX-512 */
#define CACHE_LINE_SIZE     64

/* AI workload constants */
#define MAX_TENSOR_DIMS     8    /* Maximum tensor dimensions */
#define MAX_ACCELERATORS    16   /* Maximum accelerator devices */
#define MAX_AI_TASKS        256  /* Maximum concurrent AI tasks */
#define MAX_MODELS          64   /* Maximum loaded models */

/* Status codes */
typedef enum {
    AIOS_OK             = 0,
    AIOS_ERR_NOMEM      = -1,
    AIOS_ERR_INVAL      = -2,
    AIOS_ERR_BUSY       = -3,
    AIOS_ERR_NODEV      = -4,
    AIOS_ERR_TIMEOUT    = -5,
    AIOS_ERR_PERM       = -6,
    AIOS_ERR_IO         = -7,
    AIOS_ERR_NOSYS      = -8,
    AIOS_ERR_OVERFLOW   = -9,
    AIOS_ERR_ACCEL      = -10,  /* Accelerator error */
    AIOS_ERR_TENSOR     = -11,  /* Tensor operation error */
    AIOS_ERR_MODEL      = -12,  /* Model loading error */
} aios_status_t;

/* Compiler attributes */
#define PACKED              __attribute__((packed))
#define ALIGNED(x)          __attribute__((aligned(x)))
#define NORETURN            __attribute__((noreturn))
#define UNUSED              __attribute__((unused))
#define LIKELY(x)           __builtin_expect(!!(x), 1)
#define UNLIKELY(x)         __builtin_expect(!!(x), 0)

/* Bit manipulation */
#define BIT(n)              (1ULL << (n))
#define BITS(hi, lo)        (((1ULL << ((hi) - (lo) + 1)) - 1) << (lo))

/* Min/Max macros */
#define MIN(a, b)           ((a) < (b) ? (a) : (b))
#define MAX(a, b)           ((a) > (b) ? (a) : (b))

/* Array size */
#define ARRAY_SIZE(arr)     (sizeof(arr) / sizeof((arr)[0]))

/* Compile-time assertions */
#if defined(__STDC_VERSION__) && (__STDC_VERSION__ >= 201112L)
#define AIOS_STATIC_ASSERT(cond, msg) _Static_assert(cond, msg)
#else
#define AIOS_STATIC_ASSERT(cond, msg) \
    typedef char aios_static_assertion_##__LINE__[(cond) ? 1 : -1]
#endif

/* Memory size helpers */
#define KB(x)               ((x) * 1024ULL)
#define MB(x)               ((x) * 1024ULL * 1024ULL)
#define GB(x)               ((x) * 1024ULL * 1024ULL * 1024ULL)

#endif /* _AIOS_TYPES_H */
