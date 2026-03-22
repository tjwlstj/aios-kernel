/*
 * AIOS Kernel - VGA Text Mode Console Driver
 * AI-Native Operating System
 */

#include <drivers/vga.h>

/* Console state */
static uint16_t *vga_buffer = (uint16_t *)VGA_BUFFER_ADDR;
static uint8_t console_color;
static uint32_t console_row;
static uint32_t console_col;

/* I/O port operations */
static inline void outb(uint16_t port, uint8_t val) {
    __asm__ volatile ("outb %0, %1" : : "a"(val), "Nd"(port));
}

static inline uint8_t inb(uint16_t port) {
    uint8_t ret;
    __asm__ volatile ("inb %1, %0" : "=a"(ret) : "Nd"(port));
    return ret;
}

/* Update hardware cursor position */
static void update_cursor(void) {
    uint16_t pos = console_row * VGA_WIDTH + console_col;
    outb(0x3D4, 14);
    outb(0x3D5, (uint8_t)(pos >> 8));
    outb(0x3D4, 15);
    outb(0x3D5, (uint8_t)(pos & 0xFF));
}

/* Scroll screen up by one line */
static void console_scroll(void) {
    /* Move all lines up by one */
    for (uint32_t i = 0; i < (VGA_HEIGHT - 1) * VGA_WIDTH; i++) {
        vga_buffer[i] = vga_buffer[i + VGA_WIDTH];
    }
    /* Clear the last line */
    uint16_t blank = vga_entry(' ', console_color);
    for (uint32_t i = (VGA_HEIGHT - 1) * VGA_WIDTH; i < VGA_SIZE; i++) {
        vga_buffer[i] = blank;
    }
    console_row = VGA_HEIGHT - 1;
}

void console_init(void) {
    console_color = vga_entry_color(VGA_LIGHT_CYAN, VGA_BLUE);
    console_row = 0;
    console_col = 0;
    console_clear();
}

void console_clear(void) {
    uint16_t blank = vga_entry(' ', console_color);
    for (uint32_t i = 0; i < VGA_SIZE; i++) {
        vga_buffer[i] = blank;
    }
    console_row = 0;
    console_col = 0;
    update_cursor();
}

void console_set_color(vga_color_t fg, vga_color_t bg) {
    console_color = vga_entry_color(fg, bg);
}

void console_putchar(char c) {
    if (c == '\n') {
        console_col = 0;
        console_row++;
    } else if (c == '\r') {
        console_col = 0;
    } else if (c == '\t') {
        console_col = (console_col + 8) & ~7;
    } else if (c == '\b') {
        if (console_col > 0) {
            console_col--;
            vga_buffer[console_row * VGA_WIDTH + console_col] = vga_entry(' ', console_color);
        }
    } else {
        vga_buffer[console_row * VGA_WIDTH + console_col] = vga_entry(c, console_color);
        console_col++;
    }

    if (console_col >= VGA_WIDTH) {
        console_col = 0;
        console_row++;
    }

    if (console_row >= VGA_HEIGHT) {
        console_scroll();
    }

    update_cursor();
}

void console_write(const char *str) {
    while (*str) {
        console_putchar(*str++);
    }
}

void console_write_color(const char *str, vga_color_t fg, vga_color_t bg) {
    uint8_t old_color = console_color;
    console_color = vga_entry_color(fg, bg);
    console_write(str);
    console_color = old_color;
}

void console_write_hex(uint64_t value) {
    console_write("0x");
    char hex_chars[] = "0123456789ABCDEF";
    bool leading = true;

    for (int i = 60; i >= 0; i -= 4) {
        uint8_t nibble = (value >> i) & 0xF;
        if (nibble == 0 && leading && i > 0) continue;
        leading = false;
        console_putchar(hex_chars[nibble]);
    }

    if (leading) console_putchar('0');
}

void console_write_dec(uint64_t value) {
    if (value == 0) {
        console_putchar('0');
        return;
    }

    char buf[21];
    int i = 0;
    while (value > 0) {
        buf[i++] = '0' + (value % 10);
        value /= 10;
    }
    for (int j = i - 1; j >= 0; j--) {
        console_putchar(buf[j]);
    }
}

void console_newline(void) {
    console_putchar('\n');
}

/* Simple kprintf implementation */
void kprintf(const char *fmt, ...) {
    __builtin_va_list args;
    __builtin_va_start(args, fmt);

    while (*fmt) {
        if (*fmt == '%') {
            fmt++;
            switch (*fmt) {
                case 's': {
                    const char *s = __builtin_va_arg(args, const char *);
                    console_write(s ? s : "(null)");
                    break;
                }
                case 'd': {
                    int64_t val = __builtin_va_arg(args, int64_t);
                    if (val < 0) {
                        console_putchar('-');
                        val = -val;
                    }
                    console_write_dec((uint64_t)val);
                    break;
                }
                case 'u': {
                    uint64_t val = __builtin_va_arg(args, uint64_t);
                    console_write_dec(val);
                    break;
                }
                case 'x':
                case 'p': {
                    uint64_t val = __builtin_va_arg(args, uint64_t);
                    console_write_hex(val);
                    break;
                }
                case 'c': {
                    char c = (char)__builtin_va_arg(args, int);
                    console_putchar(c);
                    break;
                }
                case '%':
                    console_putchar('%');
                    break;
                default:
                    console_putchar('%');
                    console_putchar(*fmt);
                    break;
            }
        } else {
            console_putchar(*fmt);
        }
        fmt++;
    }

    __builtin_va_end(args);
}
