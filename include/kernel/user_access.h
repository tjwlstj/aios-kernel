/*
 * AIOS Kernel - Userspace Access Boundary
 * AI-Native Operating System
 *
 * Provides the early shared entry point for validating and copying buffers
 * that cross the future userspace/kernel boundary.  This first stage performs
 * structural checks only; page ownership and privilege checks are intentionally
 * left for the later address-space implementation.
 */

#ifndef _AIOS_KERNEL_USER_ACCESS_H
#define _AIOS_KERNEL_USER_ACCESS_H

#include <kernel/types.h>

#define USER_ACCESS_F_READ        BIT(0)
#define USER_ACCESS_F_WRITE       BIT(1)
#define USER_ACCESS_F_ALLOW_ZERO  BIT(2)
#define USER_ACCESS_F_MASK        (USER_ACCESS_F_READ | \
                                   USER_ACCESS_F_WRITE | \
                                   USER_ACCESS_F_ALLOW_ZERO)

typedef enum {
    USER_ACCESS_REASON_OK = 0,
    USER_ACCESS_REASON_NULL_PTR = 1,
    USER_ACCESS_REASON_ZERO_SIZE = 2,
    USER_ACCESS_REASON_RANGE_OVERFLOW = 3,
    USER_ACCESS_REASON_BAD_FLAGS = 4,
    USER_ACCESS_REASON_PROTECTION_UNAVAILABLE = 5,
    USER_ACCESS_REASON_COUNT = 6
} user_access_reason_t;

AIOS_STATIC_ASSERT(USER_ACCESS_REASON_COUNT == 6,
    "Update user access reason tables when enum changes");

typedef struct {
    bool ok;
    user_access_reason_t reason;
    uintptr_t start;
    uintptr_t end;
    uint64_t size;
    uint32_t flags;
} user_access_check_t;

user_access_check_t user_access_probe(const void *ptr, uint64_t size,
                                      uint32_t flags);
bool access_ok(const void *ptr, uint64_t size, uint32_t flags);
aios_status_t user_access_status(user_access_reason_t reason);
const char *user_access_reason_name(user_access_reason_t reason);

aios_status_t copy_to_user(void *user_dst, const void *kernel_src,
                           uint64_t size);
aios_status_t copy_from_user(void *kernel_dst, const void *user_src,
                             uint64_t size);
aios_status_t copy_string_from_user(char *kernel_dst, const char *user_src,
                                    uint64_t max_len);
aios_status_t user_access_selftest(void);

#endif /* _AIOS_KERNEL_USER_ACCESS_H */
