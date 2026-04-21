/*
 * AIOS Kernel - Userspace Access Boundary
 * AI-Native Operating System
 */

#include <kernel/user_access.h>
#include <drivers/serial.h>
#include <lib/string.h>

#define USER_ACCESS_UINTPTR_MAX ((uintptr_t)~0ULL)

static const char *const user_access_reason_names[USER_ACCESS_REASON_COUNT] = {
    [USER_ACCESS_REASON_OK] = "ok",
    [USER_ACCESS_REASON_NULL_PTR] = "null-ptr",
    [USER_ACCESS_REASON_ZERO_SIZE] = "zero-size",
    [USER_ACCESS_REASON_RANGE_OVERFLOW] = "range-overflow",
    [USER_ACCESS_REASON_BAD_FLAGS] = "bad-flags",
    [USER_ACCESS_REASON_PROTECTION_UNAVAILABLE] = "protection-unavailable",
};

AIOS_STATIC_ASSERT(ARRAY_SIZE(user_access_reason_names) == USER_ACCESS_REASON_COUNT,
    "User access reason table must stay aligned");

static bool user_access_flags_valid(uint32_t flags) {
    uint32_t access = flags & (USER_ACCESS_F_READ | USER_ACCESS_F_WRITE);

    if ((flags & ~USER_ACCESS_F_MASK) != 0) {
        return false;
    }

    return access != 0;
}

static user_access_check_t user_access_make_result(bool ok,
                                                   user_access_reason_t reason,
                                                   uintptr_t start,
                                                   uintptr_t end,
                                                   uint64_t size,
                                                   uint32_t flags) {
    user_access_check_t check;

    check.ok = ok;
    check.reason = reason;
    check.start = start;
    check.end = end;
    check.size = size;
    check.flags = flags;
    return check;
}

user_access_check_t user_access_probe(const void *ptr, uint64_t size,
                                      uint32_t flags) {
    uintptr_t start = (uintptr_t)ptr;
    uintptr_t end = start;

    if (!user_access_flags_valid(flags)) {
        return user_access_make_result(false, USER_ACCESS_REASON_BAD_FLAGS,
            start, end, size, flags);
    }

    if (size == 0) {
        bool ok = (flags & USER_ACCESS_F_ALLOW_ZERO) != 0;
        return user_access_make_result(ok,
            ok ? USER_ACCESS_REASON_OK : USER_ACCESS_REASON_ZERO_SIZE,
            start, end, size, flags);
    }

    if (!ptr) {
        return user_access_make_result(false, USER_ACCESS_REASON_NULL_PTR,
            start, end, size, flags);
    }

    if (start > USER_ACCESS_UINTPTR_MAX - (uintptr_t)(size - 1)) {
        return user_access_make_result(false,
            USER_ACCESS_REASON_RANGE_OVERFLOW, start, end, size, flags);
    }

    end = start + (uintptr_t)(size - 1);
    return user_access_make_result(true, USER_ACCESS_REASON_OK,
        start, end, size, flags);
}

bool access_ok(const void *ptr, uint64_t size, uint32_t flags) {
    return user_access_probe(ptr, size, flags).ok;
}

aios_status_t user_access_status(user_access_reason_t reason) {
    switch (reason) {
        case USER_ACCESS_REASON_OK:
            return AIOS_OK;
        case USER_ACCESS_REASON_RANGE_OVERFLOW:
            return AIOS_ERR_OVERFLOW;
        case USER_ACCESS_REASON_PROTECTION_UNAVAILABLE:
            return AIOS_ERR_PERM;
        case USER_ACCESS_REASON_NULL_PTR:
        case USER_ACCESS_REASON_ZERO_SIZE:
        case USER_ACCESS_REASON_BAD_FLAGS:
        case USER_ACCESS_REASON_COUNT:
        default:
            return AIOS_ERR_INVAL;
    }
}

const char *user_access_reason_name(user_access_reason_t reason) {
    if ((uint32_t)reason >= USER_ACCESS_REASON_COUNT) {
        return "unknown";
    }

    return user_access_reason_names[reason];
}

aios_status_t copy_to_user(void *user_dst, const void *kernel_src,
                           uint64_t size) {
    user_access_check_t check;

    if (size == 0) {
        return AIOS_OK;
    }
    if (!kernel_src) {
        return AIOS_ERR_INVAL;
    }

    check = user_access_probe(user_dst, size, USER_ACCESS_F_WRITE);
    if (!check.ok) {
        return user_access_status(check.reason);
    }

    memcpy(user_dst, kernel_src, (size_t)size);
    return AIOS_OK;
}

aios_status_t copy_from_user(void *kernel_dst, const void *user_src,
                             uint64_t size) {
    user_access_check_t check;

    if (size == 0) {
        return AIOS_OK;
    }
    if (!kernel_dst) {
        return AIOS_ERR_INVAL;
    }

    check = user_access_probe(user_src, size, USER_ACCESS_F_READ);
    if (!check.ok) {
        return user_access_status(check.reason);
    }

    memcpy(kernel_dst, user_src, (size_t)size);
    return AIOS_OK;
}

aios_status_t copy_string_from_user(char *kernel_dst, const char *user_src,
                                    uint64_t max_len) {
    user_access_check_t check;

    if (!kernel_dst || max_len == 0) {
        return AIOS_ERR_INVAL;
    }

    check = user_access_probe(user_src, max_len, USER_ACCESS_F_READ);
    if (!check.ok) {
        kernel_dst[0] = '\0';
        return user_access_status(check.reason);
    }

    for (uint64_t i = 0; i < max_len; i++) {
        char c = user_src[i];
        kernel_dst[i] = c;
        if (c == '\0') {
            return AIOS_OK;
        }
    }

    kernel_dst[max_len - 1] = '\0';
    return AIOS_ERR_OVERFLOW;
}

aios_status_t user_access_selftest(void) {
    uint32_t source = 0xA105CAFEu;
    uint32_t user_slot = 0;
    uint32_t kernel_slot = 0;
    char string_slot[8];
    const char unterminated[4] = {'a', 'i', 'o', 's'};
    user_access_check_t check;

    check = user_access_probe(NULL, sizeof(source), USER_ACCESS_F_WRITE);
    if (check.ok || check.reason != USER_ACCESS_REASON_NULL_PTR ||
        user_access_status(check.reason) != AIOS_ERR_INVAL) {
        return AIOS_ERR_IO;
    }

    check = user_access_probe(&user_slot, 0, USER_ACCESS_F_WRITE);
    if (check.ok || check.reason != USER_ACCESS_REASON_ZERO_SIZE) {
        return AIOS_ERR_IO;
    }

    check = user_access_probe(&user_slot, 0,
        USER_ACCESS_F_WRITE | USER_ACCESS_F_ALLOW_ZERO);
    if (!check.ok || check.reason != USER_ACCESS_REASON_OK) {
        return AIOS_ERR_IO;
    }

    check = user_access_probe((void *)(USER_ACCESS_UINTPTR_MAX - 7ULL), 16,
        USER_ACCESS_F_READ);
    if (check.ok || check.reason != USER_ACCESS_REASON_RANGE_OVERFLOW ||
        user_access_status(check.reason) != AIOS_ERR_OVERFLOW) {
        return AIOS_ERR_IO;
    }

    if (copy_to_user(&user_slot, &source, sizeof(source)) != AIOS_OK ||
        user_slot != source) {
        return AIOS_ERR_IO;
    }

    if (copy_from_user(&kernel_slot, &user_slot, sizeof(kernel_slot)) != AIOS_OK ||
        kernel_slot != source) {
        return AIOS_ERR_IO;
    }

    if (copy_to_user(NULL, &source, sizeof(source)) != AIOS_ERR_INVAL ||
        copy_from_user(&kernel_slot, NULL, sizeof(kernel_slot)) != AIOS_ERR_INVAL) {
        return AIOS_ERR_IO;
    }

    if (copy_to_user(NULL, NULL, 0) != AIOS_OK ||
        copy_from_user(NULL, NULL, 0) != AIOS_OK) {
        return AIOS_ERR_IO;
    }

    if (copy_string_from_user(string_slot, "aios", sizeof(string_slot)) != AIOS_OK ||
        strcmp(string_slot, "aios") != 0) {
        return AIOS_ERR_IO;
    }

    if (copy_string_from_user(string_slot, unterminated, sizeof(unterminated)) !=
        AIOS_ERR_OVERFLOW || string_slot[sizeof(unterminated) - 1] != '\0') {
        return AIOS_ERR_IO;
    }

    if (copy_string_from_user(string_slot, NULL, sizeof(string_slot)) !=
        AIOS_ERR_INVAL) {
        return AIOS_ERR_IO;
    }

    serial_write("[UACCESS] selftest PASS structural=1 copy=1 zero_copy=1 string=1\n");
    return AIOS_OK;
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
