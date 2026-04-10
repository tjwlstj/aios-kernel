/*
 * AIOS Kernel - Serial Console Driver (COM1)
 * AI-Native Operating System
 */

#include <drivers/serial.h>

/* Port I/O helpers */
static inline void outb(uint16_t port, uint8_t val) {
    __asm__ volatile ("outb %0, %1" : : "a"(val), "Nd"(port));
}

static inline uint8_t inb(uint16_t port) {
    uint8_t ret;
    __asm__ volatile ("inb %1, %0" : "=a"(ret) : "Nd"(port));
    return ret;
}

static uint16_t serial_base = SERIAL_COM1_BASE;

aios_status_t serial_init(void) {
    uint16_t base = serial_base;

    /* Disable all interrupts */
    outb(base + SERIAL_IER, 0x00);

    /* Enable DLAB (set baud rate divisor) */
    outb(base + SERIAL_LCR, 0x80);

    /* Set divisor to 1 (115200 baud) */
    outb(base + SERIAL_DATA, 0x01);  /* Low byte */
    outb(base + SERIAL_IER,  0x00);  /* High byte */

    /* 8 bits, no parity, one stop bit (8N1) */
    outb(base + SERIAL_LCR, 0x03);

    /* Enable FIFO, clear them, with 14-byte threshold */
    outb(base + SERIAL_FIFO, 0xC7);

    /* IRQs enabled, RTS/DSR set */
    outb(base + SERIAL_MCR, 0x0B);

    /* Set in loopback mode, test the serial chip */
    outb(base + SERIAL_MCR, 0x1E);

    /* Test serial chip (send byte 0xAE and check if it returns same byte) */
    outb(base + SERIAL_DATA, 0xAE);
    if (inb(base + SERIAL_DATA) != 0xAE) {
        return AIOS_ERR_NODEV;
    }

    /* If serial is not faulty, set it in normal operation mode */
    /* (not-loopback with IRQs enabled and OUT#1 and OUT#2 bits enabled) */
    outb(base + SERIAL_MCR, 0x0F);

    return AIOS_OK;
}

static bool serial_transmit_ready(void) {
    return (inb(serial_base + SERIAL_LSR) & SERIAL_LSR_THRE) != 0;
}

void serial_putchar(char c) {
    /* Wait for transmit buffer to be empty */
    while (!serial_transmit_ready());
    outb(serial_base + SERIAL_DATA, (uint8_t)c);
}

void serial_write(const char *str) {
    while (*str) {
        if (*str == '\n') {
            serial_putchar('\r');
        }
        serial_putchar(*str);
        str++;
    }
}

bool serial_data_ready(void) {
    return (inb(serial_base + SERIAL_LSR) & SERIAL_LSR_DR) != 0;
}

char serial_getchar(void) {
    while (!serial_data_ready());
    return (char)inb(serial_base + SERIAL_DATA);
}

/* Minimal printf for serial - reuses kprintf-style formatting */
static void serial_print_uint(uint64_t val) {
    char buf[21];
    int i = 0;
    if (val == 0) {
        serial_putchar('0');
        return;
    }
    while (val > 0) {
        buf[i++] = '0' + (char)(val % 10);
        val /= 10;
    }
    for (int j = i - 1; j >= 0; j--) {
        serial_putchar(buf[j]);
    }
}

static void serial_print_hex(uint64_t val) {
    const char hex[] = "0123456789abcdef";
    serial_write("0x");
    bool leading = true;
    for (int i = 60; i >= 0; i -= 4) {
        uint8_t nibble = (val >> i) & 0xF;
        if (nibble == 0 && leading && i > 0) continue;
        leading = false;
        serial_putchar(hex[nibble]);
    }
    if (leading) serial_putchar('0');
}

void serial_printf(const char *fmt, ...) {
    __builtin_va_list args;
    __builtin_va_start(args, fmt);

    while (*fmt) {
        if (*fmt == '%') {
            fmt++;
            switch (*fmt) {
                case 'u':
                    serial_print_uint(__builtin_va_arg(args, uint64_t));
                    break;
                case 'x':
                    serial_print_hex(__builtin_va_arg(args, uint64_t));
                    break;
                case 's': {
                    const char *s = __builtin_va_arg(args, const char *);
                    serial_write(s ? s : "(null)");
                    break;
                }
                case 'd': {
                    int64_t val = __builtin_va_arg(args, int64_t);
                    if (val < 0) {
                        serial_putchar('-');
                        serial_print_uint((uint64_t)(-val));
                    } else {
                        serial_print_uint((uint64_t)val);
                    }
                    break;
                }
                case '%':
                    serial_putchar('%');
                    break;
                default:
                    serial_putchar('%');
                    serial_putchar(*fmt);
                    break;
            }
        } else {
            if (*fmt == '\n') serial_putchar('\r');
            serial_putchar(*fmt);
        }
        fmt++;
    }

    __builtin_va_end(args);
}

__asm__(".section .note.GNU-stack,\"\",@progbits\n\t.previous");
