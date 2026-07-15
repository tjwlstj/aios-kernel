/*
 * AIOS Kernel - Bootstrap Userspace Layout
 *
 * Shared C-side constants for the current single 2MiB ring3 window. The
 * embedded NASM demo mirrors the base address; keep the two definitions in
 * lockstep until the image is built independently from the kernel.
 */

#ifndef _AIOS_KERNEL_USER_LAYOUT_H
#define _AIOS_KERNEL_USER_LAYOUT_H

#include <kernel/types.h>

#define AIOS_BOOTSTRAP_USER_BASE        MB(64)
#define AIOS_BOOTSTRAP_USER_SIZE        ((uint64_t)HUGE_PAGE_SIZE)
#define AIOS_BOOTSTRAP_USER_END         \
    (AIOS_BOOTSTRAP_USER_BASE + AIOS_BOOTSTRAP_USER_SIZE)
#define AIOS_BOOTSTRAP_USER_BUFFER      \
    (AIOS_BOOTSTRAP_USER_BASE + PAGE_SIZE)
#define AIOS_BOOTSTRAP_USER_REJECT      \
    (AIOS_BOOTSTRAP_USER_BASE + 0x1800ULL)
#define AIOS_BOOTSTRAP_USER_STACK_TOP   \
    (AIOS_BOOTSTRAP_USER_END - 16ULL)

AIOS_STATIC_ASSERT(
    (AIOS_BOOTSTRAP_USER_BASE & (HUGE_PAGE_SIZE - 1ULL)) == 0,
    "bootstrap user base must stay huge-page aligned");
AIOS_STATIC_ASSERT(AIOS_BOOTSTRAP_USER_SIZE == HUGE_PAGE_SIZE,
    "bootstrap user window must stay one huge page");

#endif /* _AIOS_KERNEL_USER_LAYOUT_H */
