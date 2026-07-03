/*
 * AIOS Kernel - Userspace Access Boundary
 * AI-Native Operating System
 *
 * Provides the shared entry point for validating and copying buffers that
 * cross the userspace/kernel boundary. Structural checks (null, size,
 * range overflow) always apply. When a user address window is active
 * (set for the duration of a ring3 run), access_ok additionally requires
 * the buffer to lie fully inside that window, so a ring3-supplied pointer
 * cannot reach kernel memory. Copies are bracketed with STAC/CLAC when
 * SMAP is enabled so the kernel may touch user pages only inside a copy.
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
    USER_ACCESS_REASON_OUT_OF_WINDOW = 6,
    USER_ACCESS_REASON_COUNT = 7
} user_access_reason_t;

AIOS_STATIC_ASSERT(USER_ACCESS_REASON_COUNT == 7,
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

/*
 * User address window. While active, access_ok requires user buffers to
 * lie entirely within [base, base+size). Set for the duration of a ring3
 * run and cleared on return, so kernel-internal uaccess (which runs with
 * no window) is unaffected. Nesting is not supported (single ring3 slice).
 */
void user_access_set_window(uintptr_t base, uint64_t size);
void user_access_clear_window(void);
bool user_access_window_active(void);

/* Called once after cpu_security_init so copies can bracket user-page
 * touches with STAC/CLAC only when SMAP is actually enabled. */
void user_access_set_smap_active(bool active);

/*
 * Bracket a deliberate kernel access to user pages that isn't one of the
 * copy_* helpers (e.g. staging a program into a user page before ring3).
 * Emits STAC/CLAC only when SMAP is enabled. Must be balanced and must not
 * span a context switch. copy_to_user/copy_from_user bracket themselves.
 */
void user_access_fence_begin(void);
void user_access_fence_end(void);

aios_status_t user_access_selftest(void);

#endif /* _AIOS_KERNEL_USER_ACCESS_H */
