/*
 * AIOS Kernel - VGA Text Mode Console Driver
 * AI-Native Operating System
 */

#ifndef _AIOS_VGA_H
#define _AIOS_VGA_H

#include <kernel/types.h>

/* VGA text mode constants */
#define VGA_BUFFER_ADDR     0xB8000
#define VGA_WIDTH           80
#define VGA_HEIGHT          25
#define VGA_SIZE            (VGA_WIDTH * VGA_HEIGHT)

/* VGA color codes */
typedef enum {
    VGA_BLACK           = 0,
    VGA_BLUE            = 1,
    VGA_GREEN           = 2,
    VGA_CYAN            = 3,
    VGA_RED             = 4,
    VGA_MAGENTA         = 5,
    VGA_BROWN           = 6,
    VGA_LIGHT_GREY      = 7,
    VGA_DARK_GREY       = 8,
    VGA_LIGHT_BLUE      = 9,
    VGA_LIGHT_GREEN     = 10,
    VGA_LIGHT_CYAN      = 11,
    VGA_LIGHT_RED       = 12,
    VGA_LIGHT_MAGENTA   = 13,
    VGA_YELLOW          = 14,
    VGA_WHITE           = 15,
} vga_color_t;

/* VGA entry creation */
static inline uint8_t vga_entry_color(vga_color_t fg, vga_color_t bg) {
    return fg | (bg << 4);
}

static inline uint16_t vga_entry(unsigned char c, uint8_t color) {
    return (uint16_t)c | ((uint16_t)color << 8);
}

/* Console API */
void console_init(void);
void console_clear(void);
void console_set_color(vga_color_t fg, vga_color_t bg);
void console_putchar(char c);
void console_write(const char *str);
void console_write_color(const char *str, vga_color_t fg, vga_color_t bg);
void console_write_hex(uint64_t value);
void console_write_dec(uint64_t value);
void console_newline(void);

/* Kernel print functions */
void kprintf(const char *fmt, ...);

#endif /* _AIOS_VGA_H */
